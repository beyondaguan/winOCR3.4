# -*- coding: utf-8 -*-
"""屏幕取色器 —— 全屏放大镜，点一下取屏幕上任意像素的颜色。

与旧版界面的 ScreenColorPicker 等价：主窗口盖一层半透明遮罩，
鼠标处放大显示，左键取色、右键/Esc 取消，结果以 #RRGGBB 回传。
纯 Tk + PIL 实现，不依赖任何外部库（PIL 已是 OCR 依赖）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import TclError

_ZOOM = 8                 # 放大镜放大倍率
_R = 28                   # 放大镜半径（像素）


def pick_screen_color(callback) -> None:
    """弹出取色覆盖层；取色成功回传 #RRGGBB，取消/失败回传 None。

    callback 在主线程执行（嵌套 mainloop 结束后立刻调用），可安全更新 UI。
    """
    try:
        from PIL import ImageGrab
    except Exception:
        callback(None)
        return

    root = tk.Toplevel()
    root.overrideredirect(True)
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.35)
    root.configure(bg="black")
    root.attributes("-topmost", True)

    lbl = tk.Label(root, text="在屏幕任意位置左键取色 · 右键/Esc 取消",
                   fg="white", bg="#000000", font=("Microsoft YaHei", 12))
    lbl.pack(side=tk.BOTTOM, pady=20)

    canvas = tk.Canvas(root, width=_R * 2, height=_R * 2,
                       highlightthickness=1, highlightbackground="white",
                       bg="white")
    canvas.place(x=0, y=0)

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    def _hex(px) -> str:
        try:
            r, g, b = px[:3]
        except Exception:
            return None
        return f"#{r:02x}{g:02x}{b:02x}"

    def _preview() -> None:
        try:
            x = root.winfo_pointerx()
            y = root.winfo_pointery()
        except TclError:
            return
        canvas.place(x=min(max(x + 16, 0), sw - _R * 2),
                     y=min(max(y + 16, 0), sh - _R * 2))
        try:
            shot = ImageGrab.grab((x - _R // _ZOOM, y - _R // _ZOOM,
                                   x + _R // _ZOOM, y + _R // _ZOOM))
            img = shot.resize((_R * 2, _R * 2))
            from PIL import ImageTk
            canvas.photo = ImageTk.PhotoImage(img)
            canvas.delete("all")
            canvas.create_image(0, 0, anchor=tk.NW, image=canvas.photo)
            canvas.create_line(_R, 0, _R, _R * 2, fill="red")
            canvas.create_line(0, _R, _R * 2, _R, fill="red")
        except Exception:
            pass
        root.after(40, _preview)

    def _finish(hexv):
        try:
            root.destroy()
        except Exception:
            pass
        callback(hexv)

    def _on_click(_e):
        x, y = root.winfo_pointerx(), root.winfo_pointery()
        try:
            root.withdraw()                      # 先藏遮罩，避免取到半透明层
            shot = ImageGrab.grab((x, y, x + 1, y + 1))
            hexv = _hex(shot.getpixel((0, 0)))
        except Exception:
            hexv = None
        _finish(hexv)

    root.bind("<Button-1>", _on_click)
    root.bind("<Button-3>", lambda _e: _finish(None))
    root.bind("<Escape>", lambda _e: _finish(None))
    root.focus_force()
    root.after(40, _preview)
    root.grab_set()
    root.mainloop()
