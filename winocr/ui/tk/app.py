# -*- coding: utf-8 -*-
"""Tkinter UI 适配器 — 表现层与业务的唯一接缝。

它做三件事，别的都不做：
  1. 建 Tk 主循环，组装窗口部件；
  2. 把用户操作翻译成 Pipeline 调用（一律丢后台线程，UI 永不阻塞）；
  3. 订阅事件总线，把结果经 after(0) 送回主线程刷新界面。

线程安全铁律：任何非主线程都不得直接碰 Tk 部件。
所有回写统一走 self.post()，这是 2.0「偶发卡死 / 控件状态错乱」的根治办法。
"""
from __future__ import annotations

import logging
import os as _os
import queue as _queue
import threading
import time

from ..base import UiAdapter
from ...core.event_bus import Events
from ...core.types import Capture

# 取词底层函数已迁至 services/capture/selection.py（功能域，可单测 / console 复用）。
# 这里只保留向后兼容别名，避免外部（测试 / main_window）因改名而断链。
from ...services.capture.selection import (
    SelectionCapturer,
    _ensure_sel_logging,
    poll_clipboard_text as _poll_clipboard_text,
    send_ctrl_c as _send_ctrl_c,
    foreground_title as _foreground_title,
)

# 进程退出守卫（P-11/P-13）：run.bat 的 .venv shim 宿主进程连根强杀。
from .exit_guard import force_exit_venv_tree as _force_exit_venv_tree

# 日志收口（P2-9）：按需初始化 winocr.selection 日志；WINOCR_DEBUG=1 时全量 DEBUG。
_ensure_sel_logging()

logger = logging.getLogger(__name__)


def _sel_log_static(msg: str, level: int = logging.DEBUG) -> None:
    """取词域诊断日志。默认 DEBUG（生产静默）；可传 level 升级为 WARNING/ERROR。

    落盘/可见性由 winocr.selection 日志器统一控制：WINOCR_DEBUG=1 时写 selection.log。
    """
    _ensure_sel_logging()
    logging.getLogger("winocr.selection").log(level, msg)


# 注：_send_ctrl_c / _poll_clipboard_text / _foreground_title 的实现已迁至
# winocr/services/capture/selection.py，上面以别名形式导入，仅供向后兼容。



# busy 看门狗秒数：后台任务（OCR→翻译→AI）超时仍无回调时强制解锁，防界面"假死"。
# 15s 对 medium 档 OCR（实测 ~10s）+ 翻译 / 云端视觉 OCR 不够，曾导致并行识别；
# 30s 足够覆盖正常链路，又不会让卡死拖太久。
_BUSY_WATCHDOG_SECONDS = 30.0


class TkUi(UiAdapter):
    def __init__(self) -> None:
        self.app = None
        self.root = None
        self.window = None
        self._busy = threading.Event()      # 仅用于避免重复触发，不是状态机
        self._translate_note = ""           # 本次翻译的方向说明，用完即清
        # 跨线程 UI 更新队列：所有 post() 先把回调入队，由主线程的 _pump_ui
        # 定时取出执行。这样彻底绕开「非主线程直接调 root.after」在 Tk 下的
        # 不稳定（偶发回调不被泵起、图贴/状态卡在旧值），是根治「异步结果不刷新」
        # 的可靠手段。队列本身线程安全，入队不会阻塞调用方。
        self._ui_queue = _queue.Queue()
        self._pump_running = False
        self._pump_last = 0.0             # 泵最近一次执行的时刻（自愈心跳用）
        self._ui_thread = None            # 创建 Tk 主循环的那条线程（UI 线程守卫用）
        self._cancel_event = threading.Event()   # 任务取消令牌：用户可在长任务期间中断

    # ------------------------------------------------------------------
    def bind(self, app) -> None:
        self.app = app

    def run(self) -> None:
        # AI 对话面板的「拖拽文件进附件」依赖 tkinterdnd2，而它要求根窗口必须是
        # TkinterDnD.Tk()（普通 tk.Tk() 没有 drop_target_register）。
        # 装了 tkinterdnd2 就用它（拖放生效）；没装回退普通 Tk（拖放不可用但程序照常）。
        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
        except Exception:
            import tkinter as tk
            self.root = tk.Tk()

        # 记录 UI 主线程：所有 Tk 控件访问必须发生在这条线程（见 guards.ui_thread）
        self._ui_thread = threading.current_thread()

        self._setup_style()

        from .main_window import MainWindow
        self.window = MainWindow(self)

        self._wire_events()
        self._wire_hotkeys()
        self._bind_global_keys()
        self._warmup_async()

        # 首次运行向导（P2-1）：未配置过 API Key 且没有用户配置痕迹时弹一次。
        # 用 after 延迟到主循环起来后再弹，避免与窗口构建抢焦点。
        if self._should_show_first_run():
            self.root.after(400, lambda: self._show_first_run())

        # 系统托盘（P2-3，可选）：装了 pystray 才启用；没装静默跳过
        self._tray = None
        try:
            from .tray import TrayIcon
            self._tray = TrayIcon(show_cb=self.show_window,
                                  quit_cb=self._tray_quit)
            if self._tray.start():
                _sel_log_static("tray icon enabled", logging.INFO)
        except Exception as e:
            logger.warning("[托盘] 初始化失败（忽略）: %s", e)

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._start_pump()                   # 主线程启动跨线程 UI 队列泵
        from ...version import __version__
        _sel_log_static(
            "=== WinOCR %s UI started (pump_running=%s) ==="
            % (__version__, self._pump_running), logging.INFO)
        self.root.mainloop()

    # ------------------------------------------------------------------
    # 首次运行向导（P2-1）
    # ------------------------------------------------------------------
    def _should_show_first_run(self) -> bool:
        """仅当 AI Key 未配置且用户从未写过任何 Key 时才弹向导。"""
        try:
            cfg = self.app.config
            if cfg.ai.api_key:
                return False
            # 配置文件若存在且用户配置过任何 Key（即使当前为空）不再打扰
            from ...core.paths import config_path
            p = config_path()
            if p and p.is_file():
                try:
                    raw = p.read_text(encoding="utf-8")
                    if "api_key" in raw and "=" in raw:
                        return False
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _show_first_run(self) -> None:
        try:
            from .dialogs import open_first_run
            open_first_run(self.window)
        except Exception as e:
            logger.warning("[首次运行向导] 弹出失败: %s", e)

    def _setup_style(self) -> None:
        cfg = self.app.config
        from ...version import __version__
        # 按配置加载配色主题（多主题 + 浅/深 + 自定义覆盖）+ 字号
        dark = False
        try:
            from . import theme
            fam, mode = theme.parse_theme(cfg.ui.theme)
            theme.set_active(fam, mode, cfg.ui.theme_colors)
            theme.set_font_size(cfg.ui.font_size)
            dark = (mode == "dark")
        except Exception:
            pass
        self.root.title(f"WinOCR {__version__} — 截图识字 · 翻译 · AI")
        self.root.geometry(cfg.ui.window_size)
        self.root.minsize(640, 460)
        from .style import apply_ttk_style
        apply_ttk_style(self.root, dark)

    def _bind_global_keys(self) -> None:
        """应用内永久闸门：全局热键库失效/关闭时，主窗口有焦点也能退出。

        只绑一次（在 run() 里），UI 主题切换重建窗口时不会重复绑定。
        旧版 Alt+字母快捷键已移除（输入法/粘滞键易误触，见历史记录）。
        """
        self.root.bind_all("<Escape>", lambda e: self.hide_window())
        self.root.bind_all("<Control-Shift-KeyPress-q>", lambda e: self.quit_app())
        self.root.bind_all("<Control-Shift-KeyPress-Q>", lambda e: self.quit_app())

    def reload_ui(self) -> None:
        """用当前活动调色板重建主窗口（切主题/改配色后即时生效）。

        不销毁 root（保留全局键绑定、计时器、选区监听），只清掉主窗口
        部件与划词贴条再重新构建；bus 订阅用的是 self.window 属性，
        重建后自动指向新窗口，无需重新订阅。
        """
        try:
            if self.window is not None:
                sticker = getattr(self.window, "sticker", None)
                if sticker is not None:
                    sticker.destroy()
        except Exception:
            pass
        for child in list(self.root.children.values()):
            try:
                child.destroy()
            except Exception:
                pass
        try:
            self._setup_style()
        except Exception:
            pass
        from .main_window import MainWindow
        self.window = MainWindow(self)

    # ------------------------------------------------------------------
    # 线程安全回写
    # ------------------------------------------------------------------
    def post(self, fn, *args, **kwargs) -> None:
        """把一次 UI 更新排进主线程事件队列（线程安全，任何线程可调用）。

        实现：先把 (fn, args, kwargs) 塞进 ``_ui_queue``，再由主线程的
        ``_pump_ui`` 取出执行。相比旧版「非主线程直接 root.after(0, ...)」，
        队列方案让绝大部分回写【不】做跨线程 Tcl 调用——只有泵未启动时的
        第一次入队会用一次 after(0) 引导泵（与旧版单次调用风险相同），
        泵起来后完全由主线程自续期。这是「划词图贴卡在『识别中』、
        异步结果不刷新」的根治要点：后台线程只入队，绝不直接碰 Tk。
        """
        try:
            self._ui_queue.put((fn, args, kwargs), block=False)
        except Exception:
            pass                              # 队列满/销毁，安静丢弃
        if self.root is None:
            return
        # 泵自愈心跳：若泵声称在跑但已超过 1.5s 没有执行（意外死亡/续期失败），
        # 重新引导一次。泵每次执行都会刷新 _pump_last，正常运行不受影响。
        stale = self._pump_running and (time.time() - self._pump_last > 1.5)
        if not self._pump_running or stale:
            if stale:
                _sel_log_static("post: pump stale, restarting", logging.WARNING)
            self._pump_running = True
            self._pump_last = time.time()
            try:
                self.root.after(0, self._pump_ui)   # 一次跨线程引导，仅此一次
            except Exception:
                self._pump_running = False

    def _start_pump(self) -> None:
        """主线程启动 UI 队列泵（run() 里显式调用，测试/后门可提前引导）。"""
        if self._pump_running:
            return
        self._pump_running = True
        self._pump_last = time.time()
        try:
            self.root.after(40, self._pump_ui)
        except Exception:
            self._pump_running = False

    def _pump_ui(self) -> None:
        """主线程：取出队列里的 UI 回调并执行（单条失败不影响后续）。

        泵长驻：每次执行完都 after(40) 续期自己，直到 root 销毁才停。
        """
        self._pump_last = time.time()
        try:
            while True:
                try:
                    fn, args, kwargs = self._ui_queue.get_nowait()
                except _queue.Empty:
                    break
                try:
                    fn(*args, **kwargs)
                except Exception:
                    pass                      # 单条 UI 回调异常不能拖垮整个泵
        finally:
            try:
                self.root.after(40, self._pump_ui)
            except Exception:
                self._pump_running = False


    def status(self, text: str) -> None:
        self.post(self.window.set_status, text)

    def _take_translate_note(self) -> str:
        note, self._translate_note = self._translate_note, ""
        return note

    # ------------------------------------------------------------------
    # 事件订阅：业务只管 publish，界面在这里统一响应
    # ------------------------------------------------------------------
    def _wire_events(self) -> None:
        bus = self.app.bus
        bus.subscribe(Events.STATUS, lambda t: self.post(self.window.set_status, t))
        bus.subscribe(Events.ERROR, lambda t: self.post(self.window.set_status, f"❌ {t}"))
        bus.subscribe(Events.OCR_START, lambda _: self.post(
            self.window.set_status, "识别中…"))
        bus.subscribe(Events.OCR_DONE, lambda r: self.post(self.window.show_original, r.text))
        bus.subscribe(Events.TRANSLATE_START, lambda _: self.post(
            self.window.set_status, "翻译中…" + self._take_translate_note()))
        bus.subscribe(Events.TRANSLATE_DONE, lambda r: self.post(
            self.window.show_translation, r))
        bus.subscribe(Events.TRANSLATE_DONE, self._maybe_auto_read)

    def _maybe_auto_read(self, result) -> None:
        """开了「翻译后自动朗读」时读出译文（听着比看着省事，尤其学外语）。"""
        if not getattr(self.app.config.tts, "auto_read", False):
            return
        text = getattr(result, "text", "") or ""
        if text.strip():
            self.post(lambda: self.do_tts_read(text))

    _HOTKEY_DEFAULTS = {
        # 截图翻译用 A 而非 S：Ctrl+Shift+S 被大量软件（IDE 另存为、微信截图）占用
        "snap_translate": "ctrl+shift+a",
        "clipboard_extract": "ctrl+shift+c",
        "translate_text": "ctrl+shift+t",
        "cycle_engine": "ctrl+shift+e",
        "selection_translate": "ctrl+shift+d",
        "tts_read": "ctrl+shift+r",
        "open_knowledge": "ctrl+shift+k",
        "import_knowledge": "ctrl+shift+i",
        "cancel": "ctrl+shift+x",
        "quit": "ctrl+shift+q",
    }

    def _wire_hotkeys(self) -> None:
        """全局热键回调必须切回主线程，否则 Tk 会在别的线程里画界面。"""
        handlers = {
            "snap_translate": lambda: self.post(self.do_snap),
            "clipboard_extract": lambda: self.post(self.do_clipboard),
            "translate_text": lambda: self.post(self.do_translate),
            "cycle_engine": lambda: self.post(self.do_switch_engine),
            "selection_translate": self._on_hotkey_selection,
            "tts_read": lambda: self.post(self.do_tts_read),
            "open_knowledge": lambda: self.post(self._open_knowledge),
            "import_knowledge": lambda: self.post(self._import_knowledge),
            "cancel": lambda: self.post(self.do_cancel),
            "quit": lambda: self.post(self.quit_app),
        }
        ok = self.app.start_hotkeys(handlers, defaults=self._HOTKEY_DEFAULTS)
        summary = self.app.hotkeys.summary()
        self.window.set_status(("就绪 — " + summary) if ok else ("就绪（" + summary + "）"))

    def _open_knowledge(self) -> None:
        """全局热键「打开知识库」：打开知识库管理面板（导入/检索/导出都在内）。"""
        try:
            from .dialogs import open_knowledge
            open_knowledge(self.window)
        except Exception as e:
            self.window.set_status(f"打开知识库失败: {e}")

    def _import_knowledge(self) -> None:
        """全局热键「导入知识库」：直接弹导入流程（选文件 + 选项目）。"""
        try:
            from .dialogs import open_import_dialog
            open_import_dialog(self.window)
        except Exception as e:
            self.window.set_status(f"导入知识库失败: {e}")

    def _warmup_async(self) -> None:
        """后台预热 OCR 模型 + TTS 链路，避免第一次操作干等。
        TTS 预热现在会真合成一段极短文本，把 edge 的 DNS/TLS/WebSocket
        链路预热好（首声延迟从 ~19s 降到 ~3s），不阻塞启动。
        """
        def _work():
            ocr = self.app.services.get("ocr")
            if ocr is not None and ocr.available():
                ocr.warmup()
            tts = self.app.services.get("tts")
            if tts is not None:
                try:
                    tts.warmup()
                except Exception:
                    pass
        threading.Thread(target=_work, daemon=True, name="winocr-warmup").start()

    # ==================================================================
    # 业务动作
    # ==================================================================
    def do_snap(self) -> None:
        """框选截图 → OCR → 翻译。必须在主线程发起（框选窗口是 Tk 部件）。"""
        sources = self.app.services.get("capture") or {}
        src = sources.get("screenshot")
        if src is None:
            self.status("未找到截图捕获源")
            return
        src.configure(parent=self.root)
        self.root.withdraw()                       # 让主窗口先让开
        self.root.after(160, lambda: self._snap_stage2(src))

    def _snap_stage2(self, src) -> None:
        try:
            capture = src.capture()
        except Exception as e:
            self.show_window()
            self.status(f"截图失败: {e}")
            return
        self.show_window()
        if capture.is_empty:
            self.status("已取消截图")
            return
        self._process_capture(capture)

    def do_clipboard(self) -> None:
        src = (self.app.services.get("capture") or {}).get("clipboard")
        if src is None:
            return
        capture = src.capture()
        if capture.is_empty:
            self.status("剪贴板里没有图片或文字")
            return
        self.show_window()
        self._process_capture(capture)

    def do_open_file(self) -> None:
        src = (self.app.services.get("capture") or {}).get("file")
        if src is None:
            return
        src.configure(parent=self.root)
        capture = src.capture()
        if capture.is_empty:
            self.status(capture.source_label or "未选择文件")
            return
        self._process_capture(capture)

    def _process_capture(self, capture: Capture) -> None:
        """统一入口：后台跑「OCR→翻译→存历史」，全程只靠事件回传。"""
        if self._busy.is_set():
            self.status("上一次识别还在进行中…")
            return
        # 留给 AI 面板当可选上下文（用户勾选「带上最近截图」时才会真的发出去）
        self.window.last_image = capture.image
        self._busy.set()
        self.window.set_busy(True)

        def _done(_result):
            self._busy.clear()
            self.post(self.window.set_busy, False)
            self.post(self.window.set_status, "完成")

        def _err(_e):
            self._busy.clear()
            self.post(self.window.set_busy, False)

        # 看门狗：超时兜底，万一回调没跑（引擎无响应/线程异常）也强制解锁
        watchdog = threading.Timer(_BUSY_WATCHDOG_SECONDS, self._release_busy)
        watchdog.daemon = True

        def _done_wrapped(result):
            watchdog.cancel()
            _done(result)

        def _err_wrapped(e):
            watchdog.cancel()
            _err(e)

        self._cancel_event.clear()
        watchdog.start()
        self.app.pipeline.run_async(
            self.app.pipeline.extract_and_translate,
            capture, self.app.config.translate.target,
            on_done=_done_wrapped, on_error=_err_wrapped,
            cancel_event=self._cancel_event)

    def do_translate(self, target: str = None) -> None:
        """翻译。target 由按钮显式传入时（译中/译英），方向不再被自动纠正。

        源文本按「用户当下最可能的意图」挑，优先级：
          1. 有选中文字 → 只翻选中的那段（长文里挑一句看，比整篇重翻实用）
          2. 原文区（常规路径）
          3. 原文已经是目标语种 → 改翻译文区，即「译回来」
        第 3 条正是「中文原文 → 译英 → 再点译中」这个场景，
        以前这里直接原地重翻中文，看起来就像按钮失灵。
        """
        explicit = target is not None
        target = target or self.app.config.translate.target
        disp = self.app.services.get("translate")

        text = self.window.get_selected_text()
        origin = "选中片段"
        if not text:
            orig = self.window.get_original().strip()
            trans = self.window.get_translation().strip()
            same = disp.is_same_language if disp else (lambda *_: False)
            if orig and not (explicit and same(orig, target)):
                text, origin = orig, "原文"
            elif trans and not same(trans, target):
                text, origin = trans, "译文"
            else:
                lang = self.window.lang_label(target)
                self.status(f"原文已经是{lang}了，无需翻译" if orig
                            else "没有可翻译的文字")
                return

        # 交给 TRANSLATE_START 事件统一显示，避免我这条被事件回调覆盖掉
        self._translate_note = f"（{origin} → {self.window.lang_label(target)}）"
        self.app.pipeline.run_async(self.app.pipeline.translate, text, target,
                                    explicit=explicit)

    def do_switch_engine(self) -> None:
        disp = self.app.services.get("translate")
        new = disp.cycle_engine()
        self.app.config.translate.engine = new
        self.app.apply_config()
        self.window.refresh_engine_label()
        self.status(f"翻译引擎已切换为：{disp.engine_display(new) if new != 'auto' else '自动回退链'}")

    def _release_busy(self) -> None:
        """无论成功还是异常，最终都要释放 busy 锁，避免界面"假死"。

        set_busy 会触碰 Tk 控件（按钮禁用、进度条启停），**必须在主线程执行**。
        本方法常被后台线程（run_async 的 on_done/on_error、看门狗 Timer）调用，
        若在此同步调 set_busy，后台线程直触 Tk 会死锁/卡死，导致后续弹贴条
        永远到不了。故 set_busy 一定经 post 切回主线程；并包一层容错，
        即使窗口接口缺失也不影响 busy 锁的释放。
        """
        try:
            self._busy.clear()
        except Exception:
            pass
        self.post(self._safe_set_busy, False)

    def _safe_set_busy(self, busy: bool) -> None:
        try:
            self.window.set_busy(busy)
        except Exception:
            pass

    def _on_hotkey_selection(self) -> None:
        """全局热键 Ctrl+Shift+D 入口（运行在 keyboard 监听线程，须立即返回不阻塞）。

        为什么另起【独立工作线程】取词，而不是直接在回调里或回主线程取：

        1) 死锁：3.4.5 曾在监听线程内同步取词，兜底路径用 SendInput 注入 Ctrl+C，
           该注入事件又被 keyboard 库自身钩子捕获、钩子处理时需持同一把内部锁 →
           监听线程持锁等待自己 → 死锁，表现为「完全弹不出贴图」。把取词放进独立
           工作线程后，SendInput 不再跑在监听线程里，死锁消失。

        2) 焦点/时序：3.4.3/3.4.6 把取词排在 Tk 主线程 after(0) 执行，但此刻焦点
           可能已被上一轮贴图或主窗口抢走，``GetFocusedControl()`` 读到的是 WinOCR
           自己的窗口，回落到旧内容 —— 表现为「只能跑一次 / 新选中不更新 / 总是同一段」。
           工作线程在按键发生的【同一瞬间】启动，目标软件仍持有焦点，UIA 直读与剪贴板
           兜底都能读到【屏幕任意处】最新选中的文本（这正是你要的「不局限主窗口」）。
        """
        # 即时弹窗：按键发生的【同一瞬间】先把图贴弹出来（加载中占位），
        # 让用户立刻看到反馈；真正的取词+翻译在独立工作线程异步完成后回填。
        # 若已有翻译任务在途（busy）则不再重复弹空窗，避免「处理中」闪烁。
        self._sel_log("hotkey: selection_translate triggered, busy=%s" % self._busy.is_set())
        if not self._busy.is_set():
            self.post(self.window.show_sticker, "", "⏳ 取词/翻译中…")
            self._sel_log("hotkey: placeholder posted")
        else:
            self._sel_log("hotkey: busy, skipped instant popup")
        threading.Thread(
            target=self._capture_and_translate,
            daemon=True, name="winocr-selcap").start()

    def _capture_and_translate(self) -> None:
        """工作线程体：取词 → 投递主线程翻译弹图。"""
        try:
            self._sel_log("worker: capture start")
            text, src = self._capture_selection()
            self._sel_log("worker: capture done src=%s len=%d" % (src, len(text)))
            if not text:
                self.post(self.window.show_sticker, "",
                          "没有可翻译的文本：\n先在其它软件选中文字，再按 Ctrl+Shift+D。")
                self._sel_log("worker: no-text sticker posted")
                return
            self._sel_log("key-trigger src=%s len=%d -> translate" % (src, len(text)))
            self.post(self._translate_to_sticker, text, "划词")
        except Exception as e:
            self._sel_log("worker: capture crashed: %r" % (e,))
            self.post(self.window.show_sticker, "",
                      f"取词失败：{e}\n请检查选中内容或重启 WinOCR。")

    def _capture_selection(self):
        """读取【当前屏幕任意处】选中文本。返回 (text, src)。

        取词逻辑已抽到 services/capture/selection.py（功能域，可单测 / console 复用）。
        这里只做薄封装：把诊断日志回调传给取词服务，其余一概不管。
        运行在独立工作线程（见 _on_hotkey_selection），确保目标软件仍持有焦点。
        """
        from ...services.capture.selection import SelectionCapturer
        return SelectionCapturer(log=self._sel_log).capture()

    # ---- 划词翻译共用的「翻译并弹贴条」核心 ----
    def _translate_to_sticker(self, text: str, note: str = "划词",
                               target=None, explicit: bool = False) -> bool:
        """翻译 ``text``，把原文 + 译文弹到划词小贴条。

        带 busy 守卫（防重复触发叠加）与 30s 看门狗（异常卡死兜底）。
        ``target`` 由贴图方向按钮显式传入（"zh-CN"/"en"），``explicit`` 控制是否
        强制该方向（True 时调度器只推断 source、绝不改 target）。
        返回是否真正发起了翻译。
        """
        self._sel_log("translate: entered")
        if self._busy.is_set():
            self._sel_log("translate: busy set, skipped")
            return False
        text = (text or "").strip()
        if not text:
            self._sel_log("translate: empty text, skipped")
            return False
        if target is None:
            target = self.app.config.translate.target   # None = 自动检测方向
            explicit = False
        self.status(f"{note}翻译中…")
        self._busy.set()
        self.window.set_busy(True)
        self._cancel_event.clear()

        # 看门狗：超时不但要释放 busy 锁，还要把图贴从"识别中"更新为超时提示，
        # 否则用户会以为图贴永远卡死。注意看门狗跑在 Timer 线程，所有 UI 操作走 post。
        def _on_watchdog():
            if self._cancel_event.is_set():
                return                      # 已取消：不要弹出"超时"误报
            self._sel_log("translate: watchdog timeout")
            self._release_busy()
            self.post(self.window.show_sticker, text,
                      "翻译超时：模型/网络未响应，请检查配置或稍后重试。")
            self.post(self.window.set_status, f"{note}翻译超时")

        watchdog = threading.Timer(_BUSY_WATCHDOG_SECONDS, _on_watchdog)
        watchdog.daemon = True

        def _done(result):
            self._sel_log("translate: on_done")
            watchdog.cancel()
            self._release_busy()
            _t = getattr(result, "text", "") or ""
            try:
                self.app.pipeline.record(text, _t)   # 划词翻译也进历史
            except Exception:
                pass
            self.post(self.window.show_sticker, text, _t)
            self._sel_log("translate: show_sticker posted len=%d" % len(_t))

        def _err(_e):
            self._sel_log("translate: on_error %r" % (_e,))
            watchdog.cancel()
            self._release_busy()
            try:
                self.post(self.window.show_sticker, text, f"翻译失败：{_e}")
            except Exception:
                pass
            self._sel_log("translate: show_sticker posted (error)")
            self.post(self.window.set_status, f"{note}翻译失败：{_e}")

        watchdog.start()
        self._sel_log("translate: run_async start")
        self.app.pipeline.run_async(
            self.app.pipeline.translate, text, target, explicit,
            on_done=_done, on_error=_err, cancel_event=self._cancel_event)
        return True

    def translate_sticker(self, text: str, target: str = None,
                          note: str = "划词") -> None:
        """划词小贴条「自动/译中/译英」方向按钮的入口。

        先弹「翻译中」占位，再按所选方向重译；结果经事件总线回填同一贴条。
        ``target=None`` 走自动检测，非 None 强制对应方向。
        """
        if self._busy.is_set():
            self._sel_log("translate_sticker: busy, skipped")
            return
        self.post(self.window.show_sticker, text or "", "⏳ 翻译中…")
        self._translate_to_sticker(
            text, note=note, target=target, explicit=(target is not None))


    # ---- 划词翻译（按键触发：Ctrl+Shift+D → 主线程取词 → 弹贴图） ----
    # 取词经 self.post(after(0)) 在主线程执行：用户松开热键、贴图尚未弹出，
    # 焦点仍在目标软件，可读到屏幕任意处新选中文本；贴图窗口去掉 lift() 不夺焦点。
    # 3.4.3 起取消「鼠标钩子自动划词」（左键松开 / Alt+右键 常驻监听）——
    # 钩子方案带来误触、busy 冲突、与系统右键菜单打架等问题。
    # 现在只保留最直接的一条路：选中文字 → 按 Ctrl+Shift+D → 弹贴图。
    def _sel_log(self, msg: str) -> None:
        """划词链路诊断日志（winocr.selection 域）。

        远程定位取词/翻译层问题用：取词来源/长度/动作全记录。
        默认 DEBUG 静默；WINOCR_DEBUG=1 时落盘 selection.log。
        """
        _sel_log_static(msg, logging.DEBUG)

    def do_tts_read(self, text: str = None) -> None:
        """朗读。text 为空时按「用户当下最可能想听哪段」挑：选中 → 译文 → 原文。

        再次触发（热键/按钮）时若正在朗读，则视为「停止」——
        朗读没有停止手段是很折磨人的（读了 3000 字停不下来）。
        """
        tts = self.app.services.get("tts")
        if tts is None:
            self.status("朗读服务不可用")
            return

        if text is None and tts.speaking():
            tts.stop()
            self.status("已停止朗读")
            return

        if text is None:
            text = (self.window.get_selected_text().strip()
                    or self.window.get_translation().strip()
                    or self.window.get_original().strip())
        text = (text or "").strip()
        if not text:
            self.status("没有可朗读的文本")
            return

        # on_status 来自 TTS 后台线程，必须 post 回主线程再动界面
        tts.speak(text, on_status=lambda m: self.post(lambda: self.status(m)))

    def do_cancel(self) -> None:
        """取消当前正在进行的任务（OCR / 翻译）。

        长任务（medium 档 OCR ~10s）期间用户无需苦等看门狗 30s 或重启：
        置位取消令牌 → 后台线程跑完后会丢弃结果（run_async 已检查 cancel_event）
        → 立刻释放 busy 锁让界面复活。已结束的任务不受影响。
        """
        if not self._busy.is_set():
            return
        self._cancel_event.set()
        self._release_busy()
        self.status("已取消")

    # ------------------------------------------------------------------
    def show_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def hide_window(self) -> None:
        """关闭按钮只隐藏，程序留在后台等热键（与 2.0 行为一致）。"""
        try:
            self.root.withdraw()
        except Exception:
            pass

    def _tray_quit(self) -> None:
        """托盘退出：先走 Tk 事件循环优雅退出，2 秒兜底强退。

        原 quit_cb=lambda: self.post(self.quit_app) 依赖 Tk 事件循环调度，
        若事件循环被模态对话框等阻塞，quit_app 永远不执行 → 进程残留。
        此方法起独立 daemon 线程做 2 秒倒计时，到时无论 quit_app 是否
        跑完都 os._exit(0) 强退。
        """
        try:
            self.post(self.quit_app)
        except Exception:
            pass
        def _force():
            time.sleep(2)
            _force_exit_venv_tree()
        threading.Thread(target=_force, daemon=True).start()

    def quit_app(self) -> None:
        """退出整个程序。幂等：全局库与应用内绑定可能同时触发，重复调用安全。

        流程：tts.stop → tray.stop（join 托盘线程）→ app.shutdown → Tk 销毁 →
        兜底 ``os._exit(0)`` 确保进程真退出（防止 pystray / keyboard 等非 daemon
        线程残留拖住主线程结束 → 之前「右键退出但进程残留」的根因）。
        """
        if getattr(self, "_exiting", False):
            return
        self._exiting = True
        try:
            tts = self.app.services.get("tts")
            if tts is not None:
                tts.stop()                  # 别让声音在进程退出后还响着
        except Exception:
            pass
        try:
            tray = getattr(self, "_tray", None)
            if tray is not None:
                tray.stop()                 # 内部已 join 托盘线程（≤2s）
        except Exception:
            pass
        try:
            self.app.shutdown()
        finally:
            try:
                self.root.quit()
                self.root.destroy()
            except Exception:
                pass
            # 兜底：任何残留的非 daemon 线程（pystray 旧版本/keyboard/第三方），
            # 一律强退；先做完 stop/cleanup 再 _exit 是安全的。
            # 若本进程是被 run.bat 的 .venv shim 拉起，_force_exit_venv_tree()
            # 会连父 shim 一起 taskkill，保证下次启动无需 stop_winocr 杀进程。
            try:
                _force_exit_venv_tree()
            except Exception:
                try:
                    _os._exit(0)
                except Exception:
                    pass
