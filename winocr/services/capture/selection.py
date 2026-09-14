# -*- coding: utf-8 -*-
"""取词服务（功能域，独立于表现层）。

把「读取屏幕任意处选中文本」的完整链路从 Tk 适配器中抽出来，
让它在 console / 测试环境也能复用，且可被单测覆盖（这是 3.4 里
唯一能稳定单测的取词路径 —— 旧版逻辑全塞在 ui/tk/app.py，靠实机冒烟）。

取词链（运行在独立工作线程，确保目标软件仍持有焦点）：
  1) UIA 直读（干净、不碰剪贴板）；焦点控件自身无 TextPattern时向上最多找 3 层祖先，
     仍无则做有界后代扫描（部分应用焦点落在容器、选区挂在子节点）；
  2) 剪贴板兜底：**先等热键修饰键释放**再注入 Ctrl+C + **轮询等待**（组合键热键
     如 Ctrl+Shift+D 在末键按下瞬间触发回调，此刻用户仍按着 Ctrl/Shift，
     直接注入会被合成 Ctrl+Shift+C —— 浏览器里是打开 DevTools、多数程序等于
     复制失败，这正是「必须先手动 Ctrl+C 再按热键才成功」的根因；
     自动划词场景没有修饰键按下，wait_modifiers_released 立即返回零开销）；
     目标软件复制可能慢至数百 ms，固定单次等待常读到空，故轮询；
     备份/还原无损用户数据（成功与失败路径都还原）；
  3) WM_COPY 直发【焦点控件】（标准编辑控件可靠，网页等不一定响应）。

智能取词：按前台进程名选择最优策略（_APP_STRATEGY），失败自动降级
（_STRATEGY_CHAIN）。常见应用映射：浏览器/IDE/Office→uia，微信/QQ→clip，
winrar 等老旧程序→wmcopy。

所有 Windows API 调用（uiautomation / SendInput / Win32 剪贴板）都做了防御，
缺库或异常时安全降级，绝不让取词线程崩掉影响上层。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Dict, Optional, Tuple

import logging
import os

# 取词域日志：默认只留 WARNING+（生产安静），WINOCR_DEBUG=1 时把 DEBUG 诊断
# 落盘到 selection.log 供排障。取代原来「每次都无脑写文件」的 _sel_log_static。
_SEL_LOGGER = logging.getLogger("winocr.selection")


def _ensure_sel_logging() -> None:
    """一次性配置 winocr.selection 日志。幂等（只跑一次）。"""
    if getattr(_SEL_LOGGER, "_winocr_ready", False):
        return
    _SEL_LOGGER.propagate = False
    if os.environ.get("WINOCR_DEBUG"):
        # 全局 DEBUG：控制台可见（供 P2-10 等新日志）
        logging.basicConfig(
            level=logging.DEBUG,
            format="[%(asctime)s] %(name)s %(levelname)s %(message)s",
        )
        try:
            from ...core.paths import user_dir
            fh = logging.FileHandler(user_dir() / "selection.log", encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
            _SEL_LOGGER.addHandler(fh)
            _SEL_LOGGER.setLevel(logging.DEBUG)
        except Exception:
            _SEL_LOGGER.setLevel(logging.DEBUG)
    else:
        # 生产：吞掉所有 debug/info，只保留 warning+（取词诊断基本都属 debug）
        _SEL_LOGGER.setLevel(logging.WARNING)
    _SEL_LOGGER._winocr_ready = True


# 每线程的 UIA/COM 初始化标记：uiautomation 的 GetFocusedControl 依赖
# 当前线程已 CoInitialize；钩子线程与主线程是不同线程，各自初始化一次。
_uia_local = threading.local()


def _default_log(msg: str) -> None:
    """默认诊断日志。可被上层传入的 log 覆盖。经 winocr.selection 日志器，默认静默。"""
    _ensure_sel_logging()
    _SEL_LOGGER.debug(msg)


def ensure_uia() -> None:
    """在【当前线程】初始化 UIA/COM（每线程一次，按线程局部标记去重）。"""
    if getattr(_uia_local, "inited", False):
        return
    try:
        import uiautomation as auto
        auto.Initialize()
        _uia_local.inited = True
    except Exception:
        pass


def _text_from_pattern(node, auto) -> str:
    """从单个 UIA 控件读选中文本（TextPattern → GetSelection → GetText）。"""
    try:
        tp = node.GetPattern(auto.PatternId.TextPattern)
        if tp is not None:
            ranges = tp.GetSelection()
            if ranges:
                return "\n".join(
                    (r.GetText(-1) or "") for r in ranges).strip()
    except Exception:
        pass
    return ""


def read_uia_selection(log: Callable[[str], None] = _default_log) -> str:
    """UIA 直读当前焦点控件的选中文本（不改剪贴板，最干净）。

    旧实现调了 ``Control.GetSelectionText()``，该 API 在本机 uiautomation 版本里
    **不存在**（AttributeError 被静默吞 → 永远返回空）。正确链路：
    GetPattern(TextPattern) → GetSelection() 得 TextRange 列表 → GetText(-1) 逐段取。

    部分软件（浏览器页面、PDF 查看器）把选区挂在焦点控件的**祖先**上，
    焦点控件自身没有 TextPattern —— 最多向上找 3 层祖先重试；
    反过来也有应用把焦点落在容器上、选区 TextPattern 挂在**子孙**节点，
    祖先链全空时做一次有界后代扫描（限 24 个节点，防大 UI 树拖慢取词）。
    """
    try:
        import uiautomation as auto
        control = auto.GetFocusedControl()
        if control is None:
            return ""
        node = control
        for _ in range(4):                       # 自身 + 最多 3 层祖先
            text = _text_from_pattern(node, auto)
            if text:
                return text
            try:
                node = node.GetParentControl()
            except Exception:
                break
        # 有界后代扫描（BFS，先近后远）
        queue = [control]
        seen = 0
        while queue and seen < 24:
            parent = queue.pop(0)
            try:
                kids = parent.GetChildren()
            except Exception:
                continue
            for k in kids:
                seen += 1
                if seen > 24:
                    break
                text = _text_from_pattern(k, auto)
                if text:
                    return text
                queue.append(k)
        return ""
    except Exception as e:
        log("uia read error: %r" % (e,))
        return ""


# ---- SendInput 结构体（模块级定义：便于单测断言 x64 下 sizeof==40）----
# 关键：INPUT 必须按完整 union（含 MOUSEINPUT）定义，否则 64 位下
# ctypes.sizeof(INPUT)=32 ≠ 系统 sizeof(INPUT)=40，SendInput 以
# ERROR_INVALID_PARAMETER 静默失败 —— 注入从未生效，表现为「必须先手动
# Ctrl+C 再按热键才取到词」。
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]
    # 注意：不要用 property 别名转发 .ki —— property 返回的聚合视图
    # 写入会丢失（实测 x.ki.wVk=0x11 后缓冲仍为 0），必须显式 .union.ki


def send_ctrl_c() -> None:
    """向当前前台窗口注入 Ctrl+C（复制选中文本），用 Win32 SendInput 直发。

    必须在独立工作线程调用（不能在 keyboard 监听线程内，否则与库自身钩子
    重入死锁）。直接投给前台窗口由其完成复制。

    注入前的修饰键处理由调用方负责（wait_modifiers_released，见 capture 的
    clip 分支）；返回值非 4 时记入日志，杜绝 ERROR_INVALID_PARAMETER 再次静默。
    """
    u32 = ctypes.windll.user32
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_C = 0x43

    inputs = (_INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD; inputs[0].union.ki.wVk = VK_CONTROL
    inputs[1].type = INPUT_KEYBOARD; inputs[1].union.ki.wVk = VK_C
    inputs[2].type = INPUT_KEYBOARD; inputs[2].union.ki.wVk = VK_C
    inputs[2].union.ki.dwFlags = KEYEVENTF_KEYUP
    inputs[3].type = INPUT_KEYBOARD; inputs[3].union.ki.wVk = VK_CONTROL
    inputs[3].union.ki.dwFlags = KEYEVENTF_KEYUP
    sent = u32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(_INPUT))
    if sent != 4:
        _SEL_LOGGER.debug("send_ctrl_c: SendInput sent=%d (expected 4), size=%d",
                          sent, ctypes.sizeof(_INPUT))


def wait_modifiers_released(timeout: float = 1.0) -> bool:
    """等待物理修饰键（Ctrl/Shift/Alt/Win）全部松开，超时返回 False。

    热键是组合键（如 Ctrl+Shift+D），keyboard 库在末键按下瞬间即触发回调，
    此时用户手指仍按着修饰键。此刻注入 Ctrl+C 会被系统与按住的 Shift 合成
    Ctrl+Shift+C（浏览器打开 DevTools / 多数程序无效），表现为「注入复制
    不生效，先手动 Ctrl+C 再按热键才成功」。故注入前必须等释放。
    自动划词场景没有修饰键按下，首轮探测即返回 True，零开销。
    """
    u32 = ctypes.windll.user32
    _MOD_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)   # Shift/Ctrl/Alt/LWin/RWin
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not any(u32.GetAsyncKeyState(v) & 0x8000 for v in _MOD_VKS):
                return True
        except Exception:
            return True                           # 探测失败不阻塞取词
        time.sleep(0.02)
    return False


def poll_clipboard_text(reader, timeout: float = 1.0,
                        interval: float = 0.1) -> str:
    """轮询读剪贴板文本直到超时（返回首段非空文本）。

    注入 Ctrl+C 后，目标软件把选中文本写进剪贴板可能有数百毫秒延迟（浏览器/
    Office 常见），固定 0.2s 单次等待经常读到空。轮询到 timeout 秒，每 interval
    秒读一次，兼容慢复制。
    """
    deadline = time.time() + timeout
    while True:
        try:
            new = reader()
        except Exception:
            new = ""
        if new and new.strip():
            return new.strip()
        if time.time() >= deadline:
            return ""
        time.sleep(interval)


# ---- GetGUIThreadInfo 结构体（WM_COPY 焦点控件解析用）----
class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def _resolve_copy_target(fg: int, thread_info_fn: Optional[Callable] = None) -> int:
    """解析 WM_COPY 的目标窗口（纯逻辑，可注入桩单测）。

    WM_COPY 必须发给真正持有焦点的子控件（编辑框）才可靠；发给顶层窗口
    大多数程序不处理（旧实现即如此，导致此兜底基本无效）。
    优先前台线程的 hwndFocus，其次 hwndActive，都拿不到退回顶层窗口。
    """
    if thread_info_fn is None:
        u32 = ctypes.windll.user32
        tid = u32.GetWindowThreadProcessId(fg, None)
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)

        def thread_info_fn(tid):
            return bool(tid) and bool(u32.GetGUIThreadInfo(tid, ctypes.byref(info))), info

    try:
        tid = ctypes.windll.user32.GetWindowThreadProcessId(fg, None)
        ok, info = thread_info_fn(tid)
        if ok:
            return info.hwndFocus or info.hwndActive or fg
    except Exception:
        pass
    return fg


def send_wm_copy() -> None:
    """向【焦点控件】直发 WM_COPY（复制选中文本）。

    标准编辑控件（记事本、Office、编辑器）响应可靠；网页 / 部分自绘控件
    不一定响应，故仅作为 Ctrl+C 注入失败后的第二轮兜底。
    """
    u32 = ctypes.windll.user32
    fg = u32.GetForegroundWindow()
    target = _resolve_copy_target(fg)
    u32.SendMessageW(target, 0x0301, 0, 0)   # WM_COPY


def foreground_title() -> str:
    """当前前台窗口标题（诊断用：确认注入 Ctrl+C 时焦点在哪个窗口）。"""
    try:
        u32 = ctypes.windll.user32
        h = u32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(h, buf, 256)
        return buf.value or f"<hwnd:{h}>"
    except Exception:
        return "<unknown>"


def foreground_app_name() -> str:
    """获取当前前台进程的可执行文件名（小写，如 'chrome.exe'）。

    用于智能取词：不同应用有不同的最佳取词策略，
    通过进程名选择最优方式可显著提升首次取词成功率。
    """
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

        hwnd = u32.GetForegroundWindow()
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = k32.OpenProcess(0x1000, False, pid.value)
        if not h_proc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            success = k32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size))
            if not success:
                return ""
            full_path = buf.value
            return full_path.split("\\")[-1].lower() if full_path else ""
        finally:
            k32.CloseHandle(h_proc)
    except Exception:
        return ""


class SelectionCapturer:
    """读取【当前屏幕任意处】选中文本。返回 (text, src)，src ∈ {uia, clip, wmcopy, none}。

    ``log`` 为诊断日志回调（默认写 selection.log）；``clipboard`` 为剪贴板后端，
    默认取 ``winocr.services.capture.clipboard``，测试时可注入桩对象。

    智能取词：
      - 根据前台应用自动选择最优取词策略
      - 策略失败时自动降级到下一个方式
      - 内置常见应用的最佳实践映射
    """

    # ---- 应用-策略映射表 ----
    # 取值含义:
    #   "uia"     - 优先 UIA 直读（浏览器/编辑器/Office 等）
    #   "clip"    - 优先剪贴板注入（微信/QQ/部分自绘控件）
    #   "wmcopy"  - 优先 WM_COPY（老旧程序/标准编辑控件）
    #   "auto"    - 自动按默认顺序尝试
    _APP_STRATEGY: Dict[str, str] = {
        # 浏览器类 - UIA 支持好
        "chrome.exe": "uia",
        "firefox.exe": "uia",
        "msedge.exe": "uia",
        "brave.exe": "uia",
        "opera.exe": "uia",
        "vivaldi.exe": "uia",

        # 编辑器/IDE - UIA 支持好
        "code.exe": "uia",           # VS Code
        "notepad.exe": "uia",
        "notepad++.exe": "uia",
        "sublime_text.exe": "uia",
        "atom.exe": "uia",
        "webstorm64.exe": "uia",
        "pycharm64.exe": "uia",
        "idea64.exe": "uia",

        # Office - UIA 支持好
        "winword.exe": "uia",
        "excel.exe": "uia",
        "powerpnt.exe": "uia",
        "outlook.exe": "uia",

        # PDF 阅读器
        "acrobat.exe": "uia",
        "acrobatrdc.exe": "uia",
        "foxitreader.exe": "uia",
        "sumatrapdf.exe": "uia",

        # 通讯软件 - 剪贴板更可靠
        "wechat.exe": "clip",
        "weixin.exe": "clip",
        "qq.exe": "clip",
        "tim.exe": "clip",
        "dingtalk.exe": "clip",
        "aliim.exe": "clip",
        "slack.exe": "clip",
        "telegram.exe": "clip",

        # 老旧/特殊程序 - WM_COPY 更可靠
        "winrar.exe": "wmcopy",
        "7zfm.exe": "wmcopy",
        "totalcmd64.exe": "wmcopy",
    }

    # 每个策略的尝试顺序（降级链）
    _STRATEGY_CHAIN: Dict[str, Tuple[str, ...]] = {
        "uia":    ("uia", "clip", "wmcopy"),
        "clip":   ("clip", "uia", "wmcopy"),
        "wmcopy": ("wmcopy", "clip", "uia"),
        "auto":   ("uia", "clip", "wmcopy"),
    }

    def __init__(self, log: Callable[[str], None] = None) -> None:
        self._log = log or _default_log
        # 运行时统计：各策略成功次数（用于诊断）
        self._stats: Dict[str, int] = {}

    def capture(self, clipboard=None, use_smart: bool = True) -> Tuple[str, str]:
        """取词入口。use_smart=True 时按应用策略优化顺序。"""
        ensure_uia()

        if use_smart:
            app = foreground_app_name()
            strategy = self._APP_STRATEGY.get(app, "auto")
            self._log("capture: app=%r strategy=%s" % (app, strategy))
            chain = self._STRATEGY_CHAIN.get(strategy, self._STRATEGY_CHAIN["auto"])
        else:
            chain = self._STRATEGY_CHAIN["auto"]

        # 按策略链依次尝试
        clipboard_backend = None
        saved = None
        has_backup = False

        def _restore_saved() -> None:
            """还原用户剪贴板（成功/失败路径都要，兑现「无损用户数据」）。"""
            if has_backup:
                try:
                    clipboard_backend.restore(saved)
                except Exception:
                    pass

        for i, method in enumerate(chain):
            if method == "uia":
                text = read_uia_selection(self._log)
                if text:
                    self._record_stat("uia")
                    return text, "uia"
                self._log("capture: uia empty (attempt %d)" % (i + 1,))

            elif method in ("clip", "wmcopy"):
                # 剪贴板方法共用初始化（只备份一次）
                if clipboard_backend is None:
                    if clipboard is None:
                        try:
                            from .clipboard import (
                                backup_clipboard, clear_clipboard,
                                restore_clipboard, _win_clipboard_text)
                            clipboard_backend = type(
                                "Clip", (),
                                {"backup": staticmethod(backup_clipboard),
                                 "clear": staticmethod(clear_clipboard),
                                 "restore": staticmethod(restore_clipboard),
                                 "read": staticmethod(_win_clipboard_text)})()
                        except Exception as e:
                            self._log("capture: clipboard import error %r" % (e,))
                            continue
                    else:
                        clipboard_backend = clipboard
                    saved = clipboard_backend.backup()
                    has_backup = saved is not None
                    # 备份后先清空：轮询只接受目标应用复制「新写入」的内容，
                    # 防止把用户旧剪贴板误当选取文本（假成功）
                    try:
                        _clear = getattr(clipboard_backend, "clear", None)
                        if _clear:
                            _clear()
                    except Exception:
                        pass

                try:
                    if method == "clip":
                        # 关键：等用户松开热键修饰键再注入，否则 Ctrl+C 被
                        # 合成 Ctrl+Shift+C（见 wait_modifiers_released 文档）
                        released = wait_modifiers_released(timeout=0.8)
                        self._log("capture: modifiers released=%s, fg=%r"
                                  % (released, foreground_title()))
                        send_ctrl_c()
                        text = poll_clipboard_text(clipboard_backend.read, timeout=1.0)
                        if text:
                            self._record_stat("clip")
                            _restore_saved()
                            return text, "clip"
                        self._log("capture: clip empty (attempt %d)" % (i + 1,))
                    else:  # wmcopy
                        wait_modifiers_released(timeout=0.8)
                        send_wm_copy()
                        self._log("capture: wmcopy sent to %r" % foreground_title())
                        text = poll_clipboard_text(clipboard_backend.read, timeout=0.8)
                        if text:
                            self._record_stat("wmcopy")
                            _restore_saved()
                            return text, "wmcopy"
                        self._log("capture: wmcopy empty (attempt %d)" % (i + 1,))
                except Exception as e:
                    self._log("capture: %s error %r" % (method, e))

        # 还原剪贴板
        _restore_saved()

        self._log("capture: all methods exhausted, app=%r" % foreground_app_name())
        return "", "none"

    def _record_stat(self, method: str) -> None:
        """记录策略成功次数"""
        self._stats[method] = self._stats.get(method, 0) + 1

    def get_stats(self) -> Dict[str, int]:
        """返回策略统计（调试/诊断用）"""
        return dict(self._stats)
