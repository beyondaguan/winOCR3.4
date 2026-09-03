# -*- coding: utf-8 -*-
"""屏幕框选遮罩（移植自 WinOCR2.0/screenshot.py）。

下划线开头 → 注册表会跳过它，它只是 screenshot.py 的实现细节，不是一个捕获源。

必须在 Tk 主线程调用。复用主 Tk 实例创建 Toplevel（而不是新建 Tk），
否则 Windows 上会出现两个事件循环互相抢焦点、遮罩关不掉的问题。
"""
from __future__ import annotations


class RegionSelector:
    """全屏半透明遮罩，鼠标拖拽框选。"""

    WINDOW_TITLE = "WinOCR-Screenshot"

    def __init__(self, parent, callback):
        import tkinter as tk

        self._tk = tk
        self.callback = callback
        self.start_x = 0
        self.start_y = 0
        self.rect = None
        self._done = False

        self.root = tk.Toplevel(parent)
        self.root.title(self.WINDOW_TITLE)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.3)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black", cursor="cross")

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bg="black")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self._finish(None))

    # -- 交互 --
    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        if self.rect:
            self.canvas.delete(self.rect)

    def _on_drag(self, event):
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, event.x, event.y,
            outline="#00ff88", width=2)

    def _on_release(self, event):
        x1, y1 = min(self.start_x, event.x), min(self.start_y, event.y)
        x2, y2 = max(self.start_x, event.x), max(self.start_y, event.y)
        bbox = (x1, y1, x2, y2)

        # 先隐藏遮罩并强制刷新，否则截到的会是遮罩本身
        self.root.withdraw()
        self.root.update()

        if x2 - x1 < 5 or y2 - y1 < 5:          # 误点，视为取消
            self._finish(None, None)
            return
        from PIL import ImageGrab
        self._finish(ImageGrab.grab(bbox=bbox), bbox)

    def _finish(self, img, bbox=None):
        if self._done:                           # 防重复回调
            return
        self._done = True
        try:
            self.root.destroy()
        except Exception:
            pass
        self.callback(img, bbox)

    def show(self):
        """显示并等待用户完成（wait_window 不阻塞外层事件循环）。"""
        self.root.update_idletasks()
        self.root.update()
        self._force_topmost()
        self.root.focus_force()
        self.canvas.focus_set()
        self.root.wait_window()

    def _force_topmost(self):
        """Windows 上强制置于最顶层，压过输入法候选框等 TOPMOST 窗口。"""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            # 64 位下 HWND 是 8 字节指针，不设 restype 会被 ctypes 截断成 32 位
            u32.FindWindowW.restype = wintypes.HWND
            u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            hwnd = u32.FindWindowW(None, self.WINDOW_TITLE)
            if hwnd:
                u32.SetWindowPos.argtypes = [
                    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT,
                ]
                # HWND_TOPMOST=-1, SWP_NOSIZE|SWP_NOMOVE|SWP_SHOWWINDOW
                u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass


def select_region(parent):
    """同步框选，返回 PIL.Image 或 None（用户取消）。"""
    img, _ = select_region_box(parent)
    return img


def select_region_box(parent):
    """同步框选，返回 (PIL.Image, (x1, y1, x2, y2)) 或 (None, None)。

    蒙版翻译等需要把浮层钉在选区屏幕坐标上的场景用这个变体。
    """
    holder = {}

    def _cb(img, bbox):
        holder["img"] = img
        holder["bbox"] = bbox

    RegionSelector(parent, _cb).show()
    return holder.get("img"), holder.get("bbox")
