# -*- coding: utf-8 -*-
"""取词服务（功能域，独立于表现层）。

把「读取屏幕任意处选中文本」的完整链路从 Tk 适配器中抽出来，
让它在 console / 测试环境也能复用，且可被单测覆盖（这是 3.4 里
唯一能稳定单测的取词路径 —— 旧版逻辑全塞在 ui/tk/app.py，靠实机冒烟）。

取词链（运行在独立工作线程，确保目标软件仍持有焦点）：
  1) UIA 直读（干净、不碰剪贴板）；焦点控件自身无 TextPattern 时向上最多找 3 层祖先；
  2) 剪贴板兜底：注入 Ctrl+C + **轮询等待**（目标软件复制可能慢至数百 ms，
     固定单次等待常读到空 —— 用户反馈「直接划词失败、先 Ctrl+C 再热键成功」正是此因），
     备份/还原无损用户数据；
  3) WM_COPY 直发前台窗口（标准编辑控件可靠，网页等不一定响应）。

所有 Windows API 调用（uiautomation / SendInput / Win32 剪贴板）都做了防御，
缺库或异常时安全降级，绝不让取词线程崩掉影响上层。
"""
from __future__ import annotations

import ctypes
import threading
import time
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


def read_uia_selection(log: Callable[[str], None] = _default_log) -> str:
    """UIA 直读当前焦点控件的选中文本（不改剪贴板，最干净）。

    旧实现调了 ``Control.GetSelectionText()``，该 API 在本机 uiautomation 版本里
    **不存在**（AttributeError 被静默吞 → 永远返回空）。正确链路：
    GetPattern(TextPattern) → GetSelection() 得 TextRange 列表 → GetText(-1) 逐段取。

    部分软件（浏览器页面、PDF 查看器）把选区挂在焦点控件的**祖先**上，
    焦点控件自身没有 TextPattern —— 最多向上找 3 层祖先重试。
    """
    try:
        import uiautomation as auto
        control = auto.GetFocusedControl()
        if control is None:
            return ""
        node = control
        for _ in range(4):                       # 自身 + 最多 3 层祖先
            try:
                tp = node.GetPattern(auto.PatternId.TextPattern)
                if tp is not None:
                    ranges = tp.GetSelection()
                    if ranges:
                        text = "\n".join(
                            (r.GetText(-1) or "") for r in ranges).strip()
                        if text:
                            return text
            except Exception:
                pass
            try:
                node = node.GetParentControl()
            except Exception:
                break
        return ""
    except Exception as e:
        log("uia read error: %r" % (e,))
        return ""


def send_ctrl_c() -> None:
    """向当前前台窗口注入 Ctrl+C（复制选中文本），用 Win32 SendInput 直发。

    必须在独立工作线程调用（不能在 keyboard 监听线程内，否则与库自身钩子
    重入死锁）。直接投给前台窗口由其完成复制。

    关键：注入前先把【还按着的物理修饰键】发 KEYUP 释放。划词热键
    （如 Ctrl+Shift+D）按下瞬间触发回调，此时物理 Ctrl/Shift 尚未松开，
    直接注入 C 会组合成 Ctrl+Shift+C —— 目标软件不响应复制（Chrome 甚至
    弹 DevTools），用户表现为「必须手动 Ctrl+C 才能取到词」。程序化 KEYUP
    不影响用户物理键（松开时物理 KEYUP 照常到达），且用户即将松键，无需恢复。
    """
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12          # Alt
    VK_C = 0x43

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

    # 物理修饰键还按着的（热键按下瞬间必按着 Ctrl，多半还有 Shift）→ 先 KEYUP
    seq = []
    for vk in (VK_SHIFT, VK_CONTROL, VK_MENU):
        try:
            if u32.GetAsyncKeyState(vk) & 0x8000:
                seq.append((vk, KEYEVENTF_KEYUP))
        except Exception:
            pass
    seq += [
        (VK_CONTROL, 0),            # Ctrl down
        (VK_C, 0),                  # C down
        (VK_C, KEYEVENTF_KEYUP),    # C up
        (VK_CONTROL, KEYEVENTF_KEYUP),
    ]
    inputs = (INPUT * len(seq))()
    for i, (vk, flags) in enumerate(seq):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].ki.wVk = vk
        inputs[i].ki.dwFlags = flags
    u32.SendInput(len(seq), ctypes.byref(inputs), ctypes.sizeof(INPUT))


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


def send_wm_copy() -> None:
    """向当前前台窗口直发 WM_COPY（复制选中文本）。

    标准编辑控件（记事本、Office、编辑器）响应可靠；网页 / 部分自绘控件
    不一定响应，故仅作为 Ctrl+C 注入失败后的第二轮兜底。
    """
    u32 = ctypes.windll.user32
    h = u32.GetForegroundWindow()
    u32.SendMessageW(h, 0x0301, 0, 0)   # WM_COPY


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


class SelectionCapturer:
    """读取【当前屏幕任意处】选中文本。返回 (text, src)，src ∈ {uia, clip, wmcopy, none}。

    ``log`` 为诊断日志回调（默认写 selection.log）；``clipboard`` 为剪贴板后端，
    默认取 ``winocr.services.capture.clipboard``，测试时可注入桩对象。
    """

    def __init__(self, log: Callable[[str], None] = None) -> None:
        self._log = log or _default_log

    def capture(self, clipboard=None) -> Tuple[str, str]:
        ensure_uia()
        text = read_uia_selection(self._log)
        if text:
            return text, "uia"

        self._log("capture: uia empty, try clipboard fallback")
        if clipboard is None:
            try:
                from .clipboard import (
                    backup_clipboard, restore_clipboard, _win_clipboard_text,
                    clear_clipboard)
                clipboard = type(
                    "Clip", (),
                    {"backup": staticmethod(backup_clipboard),
                     "restore": staticmethod(restore_clipboard),
                     "read": staticmethod(_win_clipboard_text),
                     "clear": staticmethod(clear_clipboard)})()
            except Exception as e:
                self._log("capture: clipboard import error %r" % (e,))
                return "", "none"

        saved = clipboard.backup()              # None = 备份失败/本就空（无需还原）
        has_backup = saved is not None
        # 备份后清空剪贴板：让轮询只认【本次注入新写入的内容】。
        # 不清空时 poll 第一轮就会读到旧剪贴板内容 —— 取到上一次复制的旧词
        # （用户「手动 Ctrl+C 后热键才能取到词」正是误读旧内容的假成功），
        # 或与目标软件复制的内容相同而漏判。清空失败（旧桩无 clear）退化为旧行为。
        clear = getattr(clipboard, "clear", None)
        if callable(clear):
            try:
                clear()
            except Exception:
                pass
        try:
            # 诊断：注入前的前台窗口，确认 Ctrl+C 投给了谁（UIPI/焦点问题一眼可见）
            self._log("capture: fg before inject=%r" % foreground_title())
            send_ctrl_c()                       # 直发 SendInput，运行在独立线程，不触发监听线程死锁
            text = poll_clipboard_text(clipboard.read, timeout=1.0)
            if text:
                return text, "clip"
            # 第二轮：WM_COPY 直发前台窗口（标准编辑控件可靠）
            try:
                send_wm_copy()
                self._log("capture: wmcopy sent to %r" % foreground_title())
                text = poll_clipboard_text(clipboard.read, timeout=0.8)
                if text:
                    return text, "wmcopy"
            except Exception as e:
                self._log("capture: wmcopy error %r" % (e,))
            self._log("capture: inject+wmcopy both empty")
            return "", "none"
        except Exception as e:
            self._log("clip fallback error: %r" % (e,))
            return "", "none"
        finally:
            if has_backup:
                try:
                    clipboard.restore(saved)
                except Exception:
                    pass
