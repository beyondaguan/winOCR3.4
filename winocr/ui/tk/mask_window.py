# -*- coding: utf-8 -*-
"""蒙版翻译浮层 —— 框选区域后钉在原地的半透明遮蔽窗。

职责拆分（3.4.20 评审驱动重构）：
- MaskWindow       ：窗口生命周期 + 事件调度（拖拽/方向/刷新/关闭）+ 翻译编排(_kick)
- MaskRenderer     ：Canvas 渲染（整行原位覆盖 / 字号自适应 / 分行排版）
- mask_segment     ：点阵分析（LineSegmenter，纯函数 + NumPy 向量化）
- TranslationCache ：译文缓存（mask_cache.py，LRU + 线程锁）

设计要点：
- 置顶、无边框、半透明（MASK_ALPHA，默认 0.6）
- 宽度=选区宽；高度严格=选区高（"框多大出多大"，绝不撑大，否则 _grab_region 循环撑高）
- 不轮询：仅 Ctrl+Shift+N / ⟳ / 方向切换 / 『随拖』开启时重译
- ESC 关闭 / Ctrl+C 复制：鼠标悬停蒙版内生效（不夺焦点，靠 60ms 按键轮询，不抓屏）
- OCR+翻译走后台线程，结果经 ui.post 回写，忙守卫避免叠加；译文缓存命中省 API 延迟
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk

from PIL import ImageGrab

from . import theme
from .mask_const import BAR_H, MASK_ALPHA, FONT_FAMILY, MASK_BG, MASK_BAR_BG
from .mask_cache import TranslationCache
from .mask_render import MaskRenderer

logger = logging.getLogger(__name__)

# ---- 全局按键 / 鼠标状态查询（ctypes，零额外依赖）----
# 蒙版不夺焦点：键盘事件发给用户焦点所在的窗口，Tk bind 根本收不到。
# ESC 关闭 / Ctrl+C 复制因此用「鼠标悬停在蒙版内 + GetAsyncKeyState 轮询」实现：
# 仅当指针位于本蒙版矩形内时快捷键才生效，绝不干扰用户正在操作的其它软件。
import ctypes

_VK_ESCAPE = 0x1B
_VK_CONTROL = 0x11
_VK_C = 0x43
KEY_POLL_MS = 60           # 轮询间隔（仅查按键/鼠标状态，不抓屏无闪烁，开销可忽略）

try:
    _user32 = ctypes.windll.user32

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def _key_down(vk: int) -> bool:
        """虚拟键当前是否按住（测试可 monkeypatch 本模块级函数）。"""
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

    def _cursor_xy() -> tuple[int, int]:
        """鼠标屏幕坐标（测试可 monkeypatch 本模块级函数）。"""
        pt = _POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            return (pt.x, pt.y)
        return (-1, -1)
except Exception:            # 非 Windows / ctypes 不可用：快捷键静默禁用
    def _key_down(vk: int) -> bool:
        return False

    def _cursor_xy() -> tuple[int, int]:
        return (-1, -1)


class MaskWindow:
    """封装一个蒙版浮层（内部持有 Toplevel）。"""

    def __init__(self, parent: "tk.Misc", bbox: tuple[int, int, int, int],
                 pipeline: "Pipeline", ui: "UIManager",
                 target: str | None = None) -> None:
        self._tk = tk
        self._bbox = bbox
        self._pipeline = pipeline
        self._ui = ui
        self._target = target          # None=自动, "zh-CN", "en"
        self._alive = True
        self._busy = False
        self._first_done = False       # 首次优先剪贴板文本
        self._drag_active = False       # 是否处于拖拽中（用于排除标题栏按钮误触）
        self._drag_offset = (0, 0)      # 拖拽起点偏移（提前初始化，避免 AttributeError）
        self._drag_retranslate = False  # 标题栏『随拖』开关：拖动结束是否自动重译（默认关）
        self._last_img = None          # 最近一次抓取的选区截图（点阵投影切小框用）
        self._current_text = ""
        self._cache = TranslationCache()   # 译文缓存：LRU + 锁，替代无限增长的裸 dict
        self._pending_kick = False      # 忙时到达的刷新请求（排队，收尾后自动补跑）
        self._status = None            # 状态栏（_build 中创建）；None 时跳过状态更新
        self._orig_bbox = bbox         # 固定原始选区：OCR 抓图用
        # _kick 抽出实例方法后，worker 需要的临时入参（避免闭包捕获，便于单测）
        self._kick_clip_text = ""
        self._kick_target = None
        self._last_translation = ""   # 最近渲染内容（Ctrl+C 复制"所见即所得"）
        self._esc_prev = False        # ESC 按下沿检测（防重复触发）
        self._copy_prev = False       # Ctrl+C 按下沿检测（防重复触发）
        self._last_ocr = None         # 最近一次 OCR 结果（字号滑条即时重渲染用）
        self._font_scale = 1.0        # 译文字号倍率（设置面板滑条，0.6~1.4）
        self._settings_btn = None     # 标题栏「⚙」（开关设置面板）
        self._panel = None            # 蒙版框外设置小面板（遮蔽/字号滑条）
        self._panel_visible = False
        self._last_engine = ""        # 最近一次翻译所用引擎（面板指示：argos=离线兜底）
        self._engine_label = None     # 设置面板里的引擎指示 Label

        x1, y1, x2, y2 = bbox
        self._bx, self._by = x1, y1
        self._bw = max(x2 - x1, 40)
        self._bh = max(y2 - y1, 40)

        self.root = tk.Toplevel(parent)
        self.root.title("WinOCR 蒙版翻译")
        self.root.withdraw()
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.root.attributes("-alpha", MASK_ALPHA)   # 半透明遮住原文（MASK_ALPHA 可调）
        self.root.geometry(f"{self._bw}x{self._bh}+{self._bx}+{self._by}")

        # DPI 校准：把「OCR 行高 px」换算成「Tk 字号 pt」，让译文与原图文字 1:1 等大。
        try:
            import tkinter.font as tkfont
            cal = tkfont.Font(root=self.root, family=FONT_FAMILY, size=100)
            L = cal.metrics("linespace")
            self._px_per_pt = (float(L) / 100.0) if L else 1.0
        except Exception:
            self._px_per_pt = 1.0

        self._build()
        self._build_settings_panel()   # 蒙版框外小面板：遮蔽 / 字号滑条
        self._renderer = MaskRenderer(self)   # 渲染委托
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.deiconify()
        self.root.update_idletasks()

        # 注册到 UI 的活动蒙版列表：全局热键「刷新蒙版」借此找到并强制刷新。
        ui = self._ui
        if ui is not None:
            mw_list = getattr(ui, "_mask_windows", None)
            if mw_list is not None:
                mw_list.append(self)

        self._watch_keys()   # ESC 关闭 / Ctrl+C 复制：悬停生效的轻量按键轮询
        self._kick()       # 首次翻译；之后仅由快捷键 Ctrl+Shift+N 刷新（不轮询，避免蒙版闪烁）

    # ==================================================================
    def _build(self) -> None:
        tk = self._tk
        # ---- 标题栏（拖拽 + 方向 + 刷新 + 关闭）----
        bar = tk.Frame(self.root, bg=MASK_BAR_BG, height=BAR_H)
        bar.pack(fill=tk.X, side=tk.TOP)
        # 拖拽绑定到整个 Toplevel（而非标题栏 bar 的 Frame）：Tk 默认 bindtags 中，
        # 子控件事件只冒泡到所在 Toplevel，不会冒泡到直接父 Frame；若只绑在 bar 上，
        # 在标题栏按钮/文字等子控件上按下、或鼠标移出 22px 高的 bar 时，B1-Motion
        # 收不到 → 拖动卡死或根本不启动。绑在 root 覆盖全窗口，整窗可拖。
        self.root.bind("<ButtonPress-1>", self._on_drag_start)
        self.root.bind("<B1-Motion>", self._on_drag_move)
        self.root.bind("<ButtonRelease-1>", self._on_drag_end)

        self._dir_btn = tk.Label(bar, text=self._dir_label(), bg=MASK_BAR_BG,
                                 fg=theme.ACCENT, cursor="hand2", padx=6,
                                 font=theme.UI_FONT_SMALL)
        self._dir_btn.pack(side=tk.LEFT)
        self._dir_btn.bind("<Button-1>", lambda e: self._cycle_dir())
        self._dir_btn.bind("<Enter>", lambda e: self._dir_btn.config(fg=theme.TEXT_MAIN))
        self._dir_btn.bind("<Leave>", lambda e: self._dir_btn.config(fg=theme.ACCENT))

        self._refresh_btn = tk.Label(bar, text="⟳", bg=MASK_BAR_BG,
                                     fg=theme.TEXT_MUTED, cursor="hand2", padx=4)
        self._refresh_btn.pack(side=tk.LEFT)
        self._refresh_btn.bind("<Button-1>", lambda e: self._kick())
        self._refresh_btn.bind("<Enter>", lambda e: self._refresh_btn.config(fg=theme.ACCENT))
        self._refresh_btn.bind("<Leave>", lambda e: self._refresh_btn.config(fg=theme.TEXT_MUTED))

        # 「随拖」开关：点亮 → 拖动结束自动重译；灰 → 仅移动不重译（默认，避免反复重译）
        self._drag_rt_btn = tk.Label(bar, text="随拖", bg=MASK_BAR_BG,
                                     fg=theme.TEXT_MUTED, cursor="hand2", padx=4,
                                     font=theme.UI_FONT_SMALL)
        self._drag_rt_btn.pack(side=tk.LEFT)
        self._drag_rt_btn.bind("<Button-1>", lambda e: self._toggle_drag_retranslate())
        self._drag_rt_btn.bind("<Enter>", lambda e: self._drag_rt_btn.config(fg=theme.ACCENT))
        self._drag_rt_btn.bind("<Leave>", lambda e: self._refresh_drag_rt_btn())

        self._status = tk.Label(bar, text="", bg=MASK_BAR_BG, fg=theme.TEXT_MUTED,
                                font=theme.UI_FONT_SMALL, anchor="w")
        self._status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        self._close_btn = tk.Label(bar, text="✕", bg=MASK_BAR_BG,
                                   fg=theme.TEXT_MUTED, cursor="hand2", padx=6)
        self._close_btn.pack(side=tk.RIGHT)
        self._close_btn.bind("<Button-1>", lambda e: self.close())
        self._close_btn.bind("<Enter>", lambda e: self._close_btn.config(fg=theme.DANGER))
        self._close_btn.bind("<Leave>", lambda e: self._close_btn.config(fg=theme.TEXT_MUTED))

        # 「⚙」设置面板开关：点亮 → 蒙版外设置面板可见；灰 → 隐藏
        self._settings_btn = tk.Label(bar, text="⚙", bg=MASK_BAR_BG,
                                      fg=theme.TEXT_MUTED, cursor="hand2", padx=4,
                                      font=theme.UI_FONT_SMALL)
        self._settings_btn.pack(side=tk.RIGHT)
        self._settings_btn.bind("<Button-1>", lambda e: self._toggle_settings_panel())
        self._settings_btn.bind("<Enter>", lambda e: self._refresh_settings_btn())
        self._settings_btn.bind("<Leave>", lambda e: self._refresh_settings_btn())

        # ---- 译文区：Canvas 绝对定位（支持按 OCR 原坐标逐字原位回填）----
        # Canvas 局部坐标 (0,0) = 窗口左上 = 选区左上；_render 把 OCR 框坐标直接映射过来，
        # 实现「原位覆盖」且消除 Text 逐行累加的纵向漂移。bar 用 pack 占顶，Canvas 填满其下，
        # bar 浮于 Canvas 之上兼作拖拽手柄。
        self._cv = tk.Canvas(self.root, bg=MASK_BG,
                             highlightthickness=0, relief=tk.FLAT)
        self._cv.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))

    # ==================================================================
    # ==================================================================
    # 蒙版外设置小面板：遮蔽（透明度）/ 字号 滑条
    # ==================================================================
    def _build_settings_panel(self) -> None:
        """在蒙版窗口外建一个独立小面板（置顶、无边框），放遮蔽/字号滑条。

        面板独立于蒙版（不随拖拽移动、不遮选区），初始吸附蒙版右侧，
        屏幕放不下时缩到左侧；标题栏「⚙」可随时开/关。
        """
        tk = self._tk
        p = tk.Toplevel(self.root)
        p.title("蒙版设置")
        p.overrideredirect(True)
        try:
            p.attributes("-topmost", True)
        except Exception:
            pass
        p.configure(bg=MASK_BAR_BG)
        self._panel = p
        self._panel_visible = True

        head = tk.Frame(p, bg=theme.ACCENT)
        head.pack(fill=tk.X)
        tk.Label(head, text="蒙版设置", bg=theme.ACCENT, fg="#ffffff",
                 font=theme.UI_FONT_SMALL, padx=6).pack(side=tk.LEFT)
        panel_close = tk.Label(head, text="✕", bg=theme.ACCENT, fg="#ffffff",
                               cursor="hand2", padx=6, font=theme.UI_FONT_SMALL)
        panel_close.pack(side=tk.RIGHT)
        panel_close.bind("<Button-1>", lambda e: self._toggle_settings_panel())

        body = tk.Frame(p, bg=MASK_BAR_BG, padx=6, pady=4)
        body.pack(fill=tk.X)

        def _row(label_text, var, lo, hi, cmd):
            row = tk.Frame(body, bg=MASK_BAR_BG)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label_text, bg=MASK_BAR_BG, fg=theme.TEXT_MAIN,
                     font=theme.UI_FONT_SMALL, width=4).pack(side=tk.LEFT)
            tk.Scale(row, from_=lo, to=hi, resolution=0.05, orient=tk.HORIZONTAL,
                     variable=var, showvalue=False, command=cmd,
                     bg=MASK_BAR_BG, fg=theme.TEXT_MUTED, highlightthickness=0,
                     sliderrelief=tk.FLAT, length=118, bd=0)\
                .pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 遮蔽：0.3（很透）~ 1.0（完全不透），即时改整窗 alpha
        self._alpha_var = tk.DoubleVar(value=MASK_ALPHA)
        _row("遮蔽", self._alpha_var, 0.3, 1.0, self._set_alpha)
        # 字号：0.6× ~ 1.4×，即时重渲染
        self._fs_var = tk.DoubleVar(value=1.0)
        _row("字号", self._fs_var, 0.6, 1.4, self._set_font_scale)

        # 引擎指示：一眼看出差译文是不是 argos 本地离线兜底（红=离线/失败，蓝=在线）
        erow = tk.Frame(body, bg=MASK_BAR_BG)
        erow.pack(fill=tk.X, pady=(4, 0))
        tk.Label(erow, text="引擎", bg=MASK_BAR_BG, fg=theme.TEXT_MAIN,
                 font=theme.UI_FONT_SMALL, width=4).pack(side=tk.LEFT)
        self._engine_label = tk.Label(erow, text="—", bg=MASK_BAR_BG,
                                      fg=theme.TEXT_MUTED,
                                      font=theme.UI_FONT_SMALL, anchor="w")
        self._engine_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._place_panel()

    def _place_panel(self) -> None:
        """面板初始位置：蒙版右侧；屏幕放不下则缩到左侧（不遮选区、不出屏）。"""
        p = self._panel
        if p is None:
            return
        try:
            p.update_idletasks()
            pw, ph = p.winfo_reqwidth(), p.winfo_reqheight()
            sw = self.root.winfo_screenwidth()
        except Exception:
            return
        px = self._bx + self._bw + 8
        if sw and px + pw > sw - 10:
            px = max(10, self._bx - pw - 8)
        p.geometry(f"{pw}x{ph}+{px}+{self._by}")

    def _toggle_settings_panel(self) -> None:
        if self._panel is None:
            return
        if self._panel_visible:
            self._panel.withdraw()
            self._panel_visible = False
        else:
            self._place_panel()
            self._panel.deiconify()
            self._panel_visible = True
        self._refresh_settings_btn()

    def _refresh_settings_btn(self) -> None:
        if self._settings_btn is None:
            return
        self._settings_btn.config(
            fg=theme.ACCENT if self._panel_visible else theme.TEXT_MUTED)

    def _set_alpha(self, v) -> None:
        """遮蔽滑条：即时改整窗透明度。注意 Tk 整窗 alpha 连文字一起透。"""
        try:
            self.root.attributes("-alpha", float(v))
        except Exception:
            pass

    def _set_font_scale(self, v) -> None:
        """字号滑条：更新倍率并即时重渲染当前内容（不重新 OCR/翻译）。"""
        self._font_scale = float(v)
        self._rerender()

    # ---- 引擎状态指示（面板「引擎」行）----
    def _report_engine(self, name: str) -> None:
        """后台线程调用：记录最近一次翻译引擎并调度回主线程刷新指示。"""
        self._last_engine = name or ""
        try:
            self._post(self._refresh_engine_label)
        except Exception:
            pass

    def _refresh_engine_label(self) -> None:
        lab = getattr(self, "_engine_label", None)
        if lab is None:
            return
        try:
            if not lab.winfo_exists():
                return
        except Exception:
            return
        name = self._last_engine or ""
        low = name.lower()
        if not name:
            lab.config(text="—", fg=theme.TEXT_MUTED)
        elif "argos" in low or "失败" in low or "全部失败" in low:
            # argos=本地离线兜底 / 失败：差译文主因，红字提示去设置配/切在线引擎
            lab.config(text=name, fg=theme.DANGER)
        else:
            lab.config(text=name, fg=theme.ACCENT)

    def _rerender(self) -> None:
        """用最近一次 OCR/译文结果重绘（字号倍率即时生效）。"""
        if self._alive and self._last_ocr is not None:
            try:
                self._renderer.render(self._last_ocr, self._last_translation)
            except Exception:
                logger.debug("蒙版滑条重渲染失败", exc_info=True)

    def _dir_label(self) -> str:
        if self._target is None:
            return "自动"
        if self._target == "en":
            return "中→英"
        if self._target == "zh-CN":
            return "英→中"
        return str(self._target)

    def _cycle_dir(self) -> None:
        if self._target is None:
            self._target = "en"
        elif self._target == "en":
            self._target = "zh-CN"
        else:
            self._target = None
        self._dir_btn.config(text=self._dir_label())
        self._kick()

    # ---- 后台翻译 ----
    def _set_status(self, text) -> None:
        st = getattr(self, "_status", None)
        if st is None:
            return
        if self._ui is not None:
            self._ui.post(lambda: st.config(text=text))
        else:
            st.config(text=text)

    def _post(self, fn) -> None:
        """把回调调度回主线程（UI 存在时走 ui.post，否则直接执行）。"""
        if self._ui is not None:
            self._ui.post(fn)
        else:
            fn()

    def _kick(self) -> None:
        """请求一次刷新（OCR + 翻译）。

        忙时【不】静默丢弃：OCR 1-2s + 翻译 API 1-3s 期间用户按快捷键若毫无
        反应、连个提示都没有，体验上就是"卡死"。故改为记下待办，当前任务
        收尾后由 _drain_pending 自动补跑最后一次。

        worker / on_done / on_error 抽为实例方法（见下），降低 _kick 嵌套层级。
        """
        if not self._alive:
            return
        if self._busy:
            self._pending_kick = True
            self._set_status("⟳ 排队中…")
            return
        self._busy = True
        self._pending_kick = False
        first = not self._first_done
        self._first_done = True
        self._kick_clip_text = self._clipboard_text() if first else ""
        self._kick_target = self._target
        self._pipeline.run_async(self._worker, on_done=self._on_done, on_error=self._on_error)

    def _worker(self):
        """后台线程：OCR 当前选区 + 翻译；命中缓存直接复用。结果由 on_done 回写。"""
        pipeline = self._pipeline
        eng = pipeline.services.get("ocr")
        clip_text = self._kick_clip_text
        target = self._kick_target
        if clip_text:
            # 剪贴板场景：行几何不可知，包成"伪 OcrResult"传给渲染层（渲染层会回退到单字号）
            from winocr.core.types import OcrResult
            ocr_result = OcrResult(text=clip_text)
        else:
            # 抓真实选区：必须先隐藏蒙版，否则会 OCR 到译文自身（半透明蒙版 +
            # 下方原文）导致「翻页不刷新 / 无限重译」。hide=True 会 withdraw
            # 蒙版一帧再 grab，截到的是纯原文。
            img = self._grab_region(hide=True)
            self._last_img = img        # 供 _render 投影切小框
            if eng is None:
                return (None, "（未配置 OCR 引擎）")
            ocr = eng.recognize(img)
            ocr_result = ocr
        ocr_text = (ocr_result.text if ocr_result else "") or ""
        # 翻译缓存：相同原文直接复用译文，省掉翻译 API 的网络延迟
        cached = self._cache.get(ocr_text)
        if cached is not None:
            return (ocr_result, cached)
        if not ocr_text.strip():
            return (ocr_result, "（未能识别文本）")
        # 流式补偿：OCR 完成即刻显示原文 + 状态「翻译中」，译文到达再替换，
        # 消除空白等待感（参考 Translumo 的延迟补偿机制）。
        self._post(lambda: self._render(ocr_result, ocr_text))
        self._set_status("⟳ 翻译中…")
        # silent=True：蒙版有自己的渲染目标，若照常广播事件会覆盖主界面
        # 译文框、改主界面状态栏，甚至触发"翻译后自动朗读"。
        tr = pipeline.translate(ocr_text, target,
                                explicit=(target is not None),
                                silent=True)
        txt = tr.text or ""
        self._cache.put(ocr_text, txt)
        self._report_engine(getattr(tr, "engine", "") or "")   # 面板「引擎」指示
        return (ocr_result, txt)

    def _on_done(self, result) -> None:
        self._busy = False
        try:
            if not self._alive or result is None:
                return
            ocr_result, translation = result
            self._current_text = (ocr_result.text if ocr_result else "") or ""
            self._set_status("")
            self._post(lambda: self._render(ocr_result, translation))
        finally:
            self._drain_pending()

    def _on_error(self, e) -> None:
        self._busy = False
        logger.warning("蒙版翻译失败: %s", e)
        self._set_status("⚠ 失败")
        self._report_engine("(全部失败)")
        self._drain_pending()

    def _drain_pending(self) -> None:
        """任务收尾后补跑排队中的刷新请求（只跑最后一次，中间的丢弃）。

        在后台线程被调用（on_done/on_error 都在工作线程），这里只做置位判断
        和起新线程，不碰 Tk，安全。
        """
        if not (self._pending_kick and self._alive):
            return
        self._pending_kick = False
        self._kick()

    def _clipboard_text(self) -> str:
        try:
            import pyperclip
            t = (pyperclip.paste() or "").strip()
            if t:
                return t
        except Exception:
            pass
        try:
            from winocr.services.capture.clipboard import _win_clipboard_text
            t = (_win_clipboard_text() or "").strip()
            if t:
                return t
        except Exception:
            pass
        return ""

    def _grab_region(self, hide: bool = True):
        """抓取原始选区屏幕内容（线程安全版）。

        _worker 在后台线程运行，但 Tk 调用（withdraw/update/deiconify）只允许
        在主线程执行——旧实现在 worker 里直触 Tk，违反「非主线程不得碰 Tk」
        铁律，正是蒙版「偶发卡死」的隐患。这里把 隐藏→抓屏→恢复 整体投递回
        主线程执行，worker 阻塞等待结果（带超时兜底：主线程被模态对话框阻塞
        时放弃抓屏返回 None，绝不拖死 worker）。
        """
        ui = self._ui
        ui_thread = getattr(ui, "_ui_thread", None) if ui is not None else None
        if ui is None or ui_thread is None or ui_thread is threading.current_thread():
            return self._grab_region_main(hide)
        box = {}
        done = threading.Event()

        def _do() -> None:
            try:
                box["img"] = self._grab_region_main(hide)
            finally:
                done.set()

        ui.post(_do)
        done.wait(timeout=5.0)
        return box.get("img")

    def _grab_region_main(self, hide: bool = True):
        """主线程内执行：临时隐藏蒙版 → ImageGrab 抓屏 → 恢复蒙版。

        hide=True（实际 OCR）：先临时隐藏蒙版，否则会把蒙版自身（半透明译文）
        截进去，OCR 识别到译文而非原文 → 翻页/滚动后翻译永远不变（"不刷新"）。
        为避免蒙版闪烁，本类不轮询，仅用户触发时才抓取，故一律 hide=True。

        注：hide 分支用 update()（非 update_idletasks()）——必须 flush withdraw 让蒙版
        真正从屏幕消失，ImageGrab 才能截到纯原文；update_idletasks() 不保证 WM 状态
        已生效，会复发"翻页不刷新"。
        """
        if hide:
            try:
                self.root.withdraw()
                self.root.update()
            except Exception:
                logger.debug("蒙版隐藏失败（继续抓取）", exc_info=True)
        try:
            return ImageGrab.grab(bbox=self._orig_bbox)
        finally:
            if hide:
                try:
                    self.root.deiconify()
                    self.root.update()
                except Exception:
                    logger.debug("蒙版恢复失败", exc_info=True)

    # ------------------------------------------------------------------
    # 渲染委托：实际绘制在 MaskRenderer（mask_render.py）
    # ------------------------------------------------------------------
    def _render(self, ocr_result, translation, img=None) -> None:
        """委托 MaskRenderer 渲染（整行原位覆盖）；同步记录内容供 Ctrl+C 所见即所得复制。"""
        self._last_translation = translation or ""
        self._last_ocr = ocr_result
        self._renderer.render(ocr_result, translation, img)

    # ---- ESC 关闭 / Ctrl+C 复制（鼠标悬停在蒙版上时生效）----
    def _watch_keys(self) -> None:
        """60ms 轻量轮询：只查按键/鼠标状态，不抓屏、无闪烁。_alive=False 自动停。"""
        if not self._alive:
            return
        self.root.after(KEY_POLL_MS, self._watch_keys)
        try:
            self._poll_keys()
        except Exception:
            pass

    def _poll_keys(self) -> None:
        """沿触发：ESC → 关闭；Ctrl+C → 复制。仅当鼠标悬停在本蒙版矩形内生效。"""
        x, y = _cursor_xy()
        over = (self._bx <= x < self._bx + self._bw
                and self._by <= y < self._by + self._bh)
        if not over:
            self._esc_prev = False
            self._copy_prev = False
            return
        esc = _key_down(_VK_ESCAPE)
        if esc and not self._esc_prev:
            self.close()
            return
        self._esc_prev = esc
        ctrl_c = _key_down(_VK_CONTROL) and _key_down(_VK_C)
        if ctrl_c and not self._copy_prev:
            self._copy_content()
        self._copy_prev = ctrl_c

    def _copy_content(self) -> None:
        """Ctrl+C：复制蒙版当前显示内容（所见即所得——译文；未翻译完成时为原文）。"""
        text = (self._last_translation or self._current_text or "").strip()
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("✓ 已复制")
            self.root.after(1500, lambda: self._set_status(""))
        except Exception:
            logger.debug("蒙版复制到剪贴板失败", exc_info=True)

    # ---- 刷新 ----
    # 不轮询、不自动重译：否则每 1.2s withdraw 蒙版抓真实选区会闪烁，
    # 且拖动/滚动时会造成反复重译。仅由全局快捷键 Ctrl+Shift+N（蒙版刷新）
    # 调用 _kick() 触发重译，或用户点击标题栏 ⟳ / 切换方向后刷新；
    # 拖拽移动窗口不再自动重译。

    # ---- 拖拽 / 关闭 ----
    def _on_drag_start(self, event) -> None:
        # 标题栏按钮（方向 / 刷新 / 关闭 / 随拖）各有自己的 <Button-1> 动作，不触发拖拽，
        # 否则点击按钮会顺带启动拖拽导致窗体误移动。
        if event.widget in (self._dir_btn, self._refresh_btn, self._close_btn,
                             self._drag_rt_btn, self._settings_btn):
            self._drag_active = False
            return
        self._drag_active = True
        self._drag_offset = (event.x_root - self._bx,
                             event.y_root - self._by)

    def _on_drag_move(self, event) -> None:
        if not self._drag_active:
            return
        self._bx = event.x_root - self._drag_offset[0]
        self._by = event.y_root - self._drag_offset[1]
        self.root.geometry(f"+{self._bx}+{self._by}")
        self._orig_bbox = (self._bx, self._by,
                           self._bx + self._bw, self._by + self._bh)

    def _on_drag_end(self, event) -> None:
        if not self._drag_active:
            return
        self._drag_active = False
        # 仅当开启『随拖重译』开关时才在拖动结束自动重译；否则仅清理拖拽状态，
        # 由用户按 Ctrl+Shift+N / 标题栏 ⟳ / 方向切换主动重译（默认关闭，避免反复重译）。
        if self._drag_retranslate:
            self._kick()

    def _toggle_drag_retranslate(self) -> None:
        """标题栏『随拖』开关：开 → 拖动结束自动重译；关 → 需手动刷新。"""
        self._drag_retranslate = not self._drag_retranslate
        self._refresh_drag_rt_btn()

    def _refresh_drag_rt_btn(self) -> None:
        if not getattr(self, "_drag_rt_btn", None):
            return
        self._drag_rt_btn.config(
            fg=theme.ACCENT if self._drag_retranslate else theme.TEXT_MUTED)

    def close(self) -> None:
        self._alive = False
        ui = self._ui
        if ui is not None:
            mw_list = getattr(ui, "_mask_windows", None)
            if mw_list is not None:
                try:
                    mw_list.remove(self)
                except ValueError:
                    pass
        try:
            self.root.destroy()
        except Exception:
            pass
        if self._panel is not None:
            try:
                self._panel.destroy()
            except Exception:
                pass
