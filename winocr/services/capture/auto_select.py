# -*- coding: utf-8 -*-
"""自动划词：低级鼠标钩子（WH_MOUSE_LL）检测「划选松开 / 双击取词」手势。

历史教训（CHANGELOG 3.11.x）：旧 selection_monitor「独立泵线程收不到回调」的
根因是 ctypes 64 位句柄/回调签名截断（SetWindowsHookExW 返回的 HHOOK 被按
c_int 截断、proc 签名缺显式声明）。本模块显式声明全部 restype/argtypes 根除。

线程模型：
  - 泵线程：SetWindowsHookExW 装钩 + GetMessageW 消息泵（钩子回调在该线程
    被系统重入调用）；stop() 用 PostThreadMessageW(WM_QUIT) 退出泵，随后在
    同线程 UnhookWindowsHookEx（沿用 win32_hotkey.py 的模式）。
  - 工作线程：钩子回调只做手势判定 + 三个布尔检查 + 入队（不 sleep 不 import
    不打日志，杜绝拖慢全系统鼠标）；延时等待与 on_gesture 回调在工作线程执行。

防误触（3.4.3 撤销自动划词的主因逐一防护）：
  - 位移阈值：按下→抬起位移 < 阈值判为单击，不触发；
  - 排除自身窗口：前台窗口属于本进程 PID 时忽略（避免设置框里划选自触发）；
  - busy 检查：翻译在途时忽略；
  - 右键事件一律不碰（不影响右键菜单）；
  - 双击为程序合成判定：WH_MOUSE_LL 收不到系统合成的 WM_LBUTTONDBLCLK，
    需按 GetDoubleClickTime + 双击矩形自行判定第二次按下。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes  # 显式导入：缺了 MSG 等类型直接 AttributeError
import logging
import os
import queue
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

# ---- 鼠标消息 ----
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WH_MOUSE_LL = 14
WM_QUIT = 0x0012

# 显式 64 位签名（根因修复：不声明会按 c_int 截断 HHOOK/返回值，回调静默失联）
_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
u32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, _HOOKPROC, ctypes.wintypes.HMODULE, ctypes.wintypes.DWORD]
u32.SetWindowsHookExW.restype = ctypes.c_void_p          # HHOOK
u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
u32.UnhookWindowsHookEx.restype = ctypes.c_int
u32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
u32.CallNextHookEx.restype = ctypes.c_ssize_t            # LRESULT
u32.GetMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG), ctypes.wintypes.HWND,
    ctypes.c_uint, ctypes.c_uint]
u32.GetMessageW.restype = ctypes.c_int
u32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
u32.PostThreadMessageW.restype = ctypes.c_int
k32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
k32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
u32.GetDoubleClickTime.argtypes = []
u32.GetDoubleClickTime.restype = ctypes.c_uint
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.GetSystemMetrics.restype = ctypes.c_int

SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", _POINT), ("mouseData", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD), ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG))]


def _foreground_pid() -> int:
    """前台窗口所属进程 PID（取不到返回 0）。"""
    try:
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = ctypes.wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _dblclick_params() -> tuple:
    """系统双击参数 (毫秒, 矩形宽 px, 矩形高 px)。探测失败用保守默认。"""
    try:
        return u32.GetDoubleClickTime() or 500, \
            u32.GetSystemMetrics(SM_CXDOUBLECLK) or 4, \
            u32.GetSystemMetrics(SM_CYDOUBLECLK) or 4
    except Exception:
        return 500, 4, 4


class GestureDetector:
    """划选手势状态机（纯逻辑，可单测）。

    on_event 返回手势名：
      - "drag"    ：按下→抬起位移 ≥ 阈值（划选完成）
      - "dblclick"：第二次按下落在系统双击窗口内（词选中，发生在按下瞬间）
      - None      ：单击 / 移动 / 右键等不触发
    """

    def __init__(self, threshold_px: int = 10,
                 now_fn: Callable[[], float] = time.monotonic,
                 dbl_params_fn: Callable[[], tuple] = _dblclick_params) -> None:
        self._threshold = max(1, int(threshold_px))
        self._now = now_fn
        self._dbl_params = dbl_params_fn
        self.reset()

    def reset(self) -> None:
        self._down_xy: Optional[tuple] = None    # 本次按下的起点
        self._last_up: Optional[tuple] = None    # (x, y, t) 上次抬起

    def on_event(self, msg: int, x: int, y: int,
                 now: Optional[float] = None) -> Optional[str]:
        t = self._now() if now is None else now
        if msg == WM_LBUTTONDOWN:
            # 双击判定：距上次抬起时间/位置都在系统双击窗口内
            if self._last_up is not None:
                lx, ly, lt = self._last_up
                win_ms, win_w, win_h = self._dbl_params()
                if (t - lt) * 1000.0 <= win_ms \
                        and abs(x - lx) <= win_w and abs(y - ly) <= win_h:
                    self._down_xy = (x, y)       # 双击的第二下也记起点（防拖选二次触发）
                    return "dblclick"
            self._down_xy = (x, y)
            return None
        if msg == WM_LBUTTONUP:
            self._last_up = (x, y, t)
            if self._down_xy is not None:
                dx, dy = self._down_xy
                self._down_xy = None
                if max(abs(x - dx), abs(y - dy)) >= self._threshold:
                    return "drag"
            return None
        return None                              # 移动/右键/其它：一律不触发


class AutoSelectionHook:
    """WH_MOUSE_LL 全局鼠标钩子。start() 装钩，stop() 幂等卸钩。

    on_gesture(kind) 在工作线程被调用（kind ∈ {"drag", "dblclick"}）；
    enabled_fn/busy_fn/dblclick_fn/delay_ms_fn 实时读配置（改设置立即生效，
    无需重启钩子）。
    """

    def __init__(
        self,
        on_gesture: Callable[[str], None],
        *,
        log: Optional[Callable[[str], None]] = None,
        enabled_fn: Callable[[], bool],
        busy_fn: Callable[[], bool],
        dblclick_fn: Callable[[], bool],
        delay_ms_fn: Callable[[], int],
        threshold_px: int = 10,
        own_pid_fn: Callable[[], int] = _foreground_pid,
    ) -> None:
        self._on_gesture = on_gesture
        self._log = log or (lambda m: logger.debug(m))
        self._enabled_fn = enabled_fn
        self._busy_fn = busy_fn
        self._dblclick_fn = dblclick_fn
        self._delay_ms_fn = delay_ms_fn
        self._own_pid_fn = own_pid_fn
        self._detector = GestureDetector(threshold_px=threshold_px)
        self._proc = _HOOKPROC(self._hook_cb)    # 引用必须常驻，否则回调被 GC 失联
        self._hook: Optional[int] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=8)
        self._ready = threading.Event()

    # ---- 生命周期 ----
    def is_running(self) -> bool:
        """钩子是否已装上且泵线程存活（用于配置热同步判断）。"""
        return self._hook is not None and self._pump_thread is not None \
            and self._pump_thread.is_alive()

    def start(self) -> bool:
        """装钩 + 启动泵/工作线程。已运行时幂等返回 True。"""
        if self.is_running():
            return True
        if self._pump_thread is not None and self._pump_thread.is_alive():
            self.stop()      # 上次装钩失败泵还挂着：先清场再重启
        self._detector.reset()
        self._q = queue.Queue(maxsize=8)
        self._ready.clear()
        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="winocr-autosel-worker")
        self._worker_thread.start()
        self._pump_thread = threading.Thread(
            target=self._pump, daemon=True, name="winocr-autosel-pump")
        self._pump_thread.start()
        # 泵线程装钩是异步的：等结果（失败降级为纯热键模式）
        if not self._ready.wait(timeout=2.0):
            self._log("auto-select: hook install timeout")
            return False
        return self._hook is not None

    def stop(self) -> None:
        """卸钩并停线程。幂等：未启动 / 已停止均为安全 no-op。"""
        if self._pump_thread is not None and self._pump_thread.is_alive():
            try:
                u32.PostThreadMessageW(self._pump_thread.ident, WM_QUIT, 0, 0)
            except Exception:
                pass
            self._pump_thread.join(timeout=2.0)
        self._pump_thread = None
        self._hook = None
        try:
            self._q.put_nowait(None)             # 哨兵：worker 退出
        except queue.Full:
            pass
        if self._worker_thread is not None and self._worker_thread is not threading.current_thread():
            self._worker_thread.join(timeout=2.0)
        self._worker_thread = None

    # ---- 内部 ----
    def _pump(self) -> None:
        """泵线程：装钩 + GetMessageW 循环；WM_QUIT 后同线程卸钩。"""
        try:
            hmod = k32.GetModuleHandleW(None)
            self._hook = u32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, hmod, 0)
        except Exception as e:
            self._log("auto-select: SetWindowsHookExW error %r" % (e,))
            self._hook = None
        finally:
            self._ready.set()
        if self._hook is None:
            self._log("auto-select: hook install failed, degrade to hotkey-only")
            return
        msg = ctypes.wintypes.MSG()
        while True:
            ret = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:            # WM_QUIT 或错误 → 退出
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        try:
            if self._hook:
                u32.UnhookWindowsHookEx(self._hook)
        except Exception:
            pass
        self._hook = None

    def _hook_cb(self, n_code: int, w_param: int, l_param: int) -> int:
        """钩子回调：只做算术 + 布尔检查 + 入队，任何路径都尽快放行。"""
        try:
            if n_code >= 0 and w_param in (WM_LBUTTONDOWN, WM_LBUTTONUP,
                                           WM_LBUTTONDBLCLK):
                info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                kind = self._detector.on_event(w_param, info.pt.x, info.pt.y)
                if kind and self._should_fire():
                    try:
                        self._q.put_nowait(kind)
                    except queue.Full:
                        pass                     # 队列满说明用户连划多段，丢新保旧
        except Exception:
            pass
        return u32.CallNextHookEx(None, n_code, w_param, l_param)

    def _should_fire(self) -> bool:
        """手势命中后的三个快速闸门（全部通过才入队）。"""
        if self._hook is None or not self._enabled_fn():
            return False
        if self._busy_fn():
            return False
        # 排除自身窗口：设置框/主窗口里划选不触发
        return self._own_pid_fn() != os.getpid()

    def _worker(self) -> None:
        """工作线程：等选区稳定（delay_ms）后在受控上下文回调 on_gesture。"""
        while True:
            kind = self._q.get()
            if kind is None:                     # stop() 哨兵
                return
            try:
                if not self._enabled_fn():
                    continue
                if kind == "dblclick" and not self._dblclick_fn():
                    continue
                time.sleep(max(0, self._delay_ms_fn()) / 1000.0)
                if not self._enabled_fn() or self._busy_fn():
                    continue                     # 延时期间开关/状态可能已变
                self._on_gesture(kind)
            except Exception as e:
                self._log("auto-select: on_gesture error %r" % (e,))
