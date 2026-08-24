# -*- coding: utf-8 -*-
"""Win32 热键兜底后端（RegisterHotKey + 消息泵）。

为什么需要它：主热键用的是 ``keyboard`` 库（基于底层钩子）。当 WinOCR 以**普通
用户**运行、而前台窗口是**管理员**进程时，UIPI（用户界面特权隔离）会拦截跨特权
的钩子/输入注入，导致 ``keyboard`` 库**完全失效** —— 划词/截图/朗读等全局热键
一个都不响应，且无任何降级。这里用 Win32 ``RegisterHotKey`` 直接注册系统级热键，
不受 UIPI 影响，作为兜底路径：主后端一个键都没注册成功时自动启用。

线程模型：在独立守护线程里跑 ``GetMessageW`` 消息泵；``RegisterHotKey`` 与泵必须在
同一条线程。命中热键（WM_HOTKEY）时按 id 找回 action 调用对应回调。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import ctypes
import threading
from typing import Callable, Dict, Optional

u32 = ctypes.windll.user32

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# 显式声明 ctypes 签名（64 位下不声明会按 c_int 截断句柄/返回值，导致注册静默失败）
u32.GetMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG), ctypes.wintypes.HWND,
                           ctypes.c_uint, ctypes.c_uint]
u32.GetMessageW.restype = ctypes.c_int
u32.TranslateMessage.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
u32.RegisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
u32.RegisterHotKey.restype = ctypes.c_int
u32.UnregisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
u32.UnregisterHotKey.restype = ctypes.c_int
u32.PostThreadMessageW.argtypes = [ctypes.wintypes.DWORD, ctypes.c_uint,
                                  ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
u32.PostThreadMessageW.restype = ctypes.c_int
u32.VkKeyScanW.argtypes = [ctypes.wintypes.WCHAR]
u32.VkKeyScanW.restype = ctypes.c_short

# 常见命名键 → VK 码（覆盖功能键 / 方向键 / 编辑键等单字符写不出的键）
_NAMED_VK = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "printscreen": 0x2C, "pause": 0x13, "capslock": 0x14,
}


def parse_combo(combo: str) -> Optional[tuple]:
    """把 'ctrl+shift+a' 解析为 (modifiers, vk)；解析失败返回 None。

    modifiers：ctrl/control → MOD_CONTROL，shift → MOD_SHIFT，
    alt/menu → MOD_ALT，win/super/meta → MOD_WIN（可叠加）。
    末段为键：单字符用 VkKeyScanW 取 VK；命名键查 _NAMED_VK 表。
    """
    if not combo or not combo.strip():
        return None
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key = parts[-1]
    mods = 0
    for m in parts[:-1]:
        if m in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif m == "shift":
            mods |= MOD_SHIFT
        elif m in ("alt", "menu"):
            mods |= MOD_ALT
        elif m in ("win", "super", "meta"):
            mods |= MOD_WIN
        else:
            return None
    if key in _NAMED_VK:
        vk = _NAMED_VK[key]
    elif len(key) == 1:
        vk = u32.VkKeyScanW(ctypes.c_wchar(key)) & 0xFF
        if not vk:
            return None
    else:
        return None
    return mods, vk


class Win32HotkeyBackend:
    """Win32 RegisterHotKey 后端。register() 成功至少注册一个键后启动消息泵线程。"""

    def __init__(self) -> None:
        self._ids: Dict[int, str] = {}          # 热键 id → action
        self._handlers: Dict[str, Callable] = {}
        self.registered: Dict[str, str] = {}     # action → 生效组合键
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def register(self, handlers: Dict[str, Callable],
                 combos: Dict[str, str]) -> bool:
        """combos: {action: 组合键串}。返回是否至少注册成功一个。"""
        self._handlers = handlers
        self.registered.clear()
        self._ids.clear()
        nid = 1
        for action, combo in combos.items():
            parsed = parse_combo(combo)
            if parsed is None:
                continue
            mods, vk = parsed
            if not u32.RegisterHotKey(None, nid, mods, vk):
                # 0 → 被占用 / 注册失败（如与其它全局热键冲突），跳过该键不中断其它
                continue
            self._ids[nid] = action
            self.registered[action] = combo
            nid += 1
        if self.registered:
            self._start_pump()
        return bool(self.registered)

    def _start_pump(self) -> None:
        self._running = True

        def pump() -> None:
            msg = ctypes.wintypes.MSG()
            while self._running:
                ret = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:          # WM_QUIT 或错误 → 退出泵
                    break
                if msg.message == WM_HOTKEY:
                    action = self._ids.get(msg.wParam)
                    handler = self._handlers.get(action) if action else None
                    if handler is not None:
                        try:
                            handler()
                        except Exception as e:
                            logger.warning("热键回调异常（%s）：%s", action, e)
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))

        self._thread = threading.Thread(
            target=pump, daemon=True, name="winocr-win32-hotkey")
        self._thread.start()

    def unregister_all(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            # 向泵线程邮递 WM_QUIT，让阻塞在 GetMessageW 的循环退出
            u32.PostThreadMessageW(self._thread.ident, WM_QUIT, 0, 0)
        for nid in list(self._ids.keys()):
            u32.UnregisterHotKey(None, nid)
        self._ids.clear()
        self.registered.clear()
