# -*- coding: utf-8 -*-
"""对话框公共工具：居中 / 模态 / 滚动容器 / 热键录制。

从 dialogs.py 拆分而来，供 settings/data/misc 复用。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_MOD_ORDER = ["ctrl", "alt", "shift", "win"]
_IGNORE_KEYS = {"control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r",
                "win_l", "win_r", "super_l", "super_r", "caps_lock", "??"}



_IGNORE_KEYS = {"control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r",
                "win_l", "win_r", "super_l", "super_r", "caps_lock", "??"}



def _post_to_ui(app, fn) -> None:
    """后台线程回写 Tk 部件的唯一通道：统一走 ``ui.post``（root.after）。

    项目铁律「非主线程不碰 Tk、回写一律过 post」——连接测试是后台线程，
    直接 ``label.after`` 虽在多数 Windows 上能用，但非官方保证。
    app 未挂 UI（纯逻辑/测试）时兜底直接执行。
    """
    ui = getattr(app, "ui", None)
    if ui is not None and hasattr(ui, "post"):
        ui.post(fn)
        return
    try:
        fn()
    except Exception:
        pass


def _center(win, parent) -> None:
    win.update_idletasks()
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 3}")
    except Exception:
        pass


def _modal(parent, title: str, size: str) -> tk.Toplevel:
    win = tk.Toplevel(parent)
    win.title(title)
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    return win


class _ScrollableFrame(ttk.Frame):
    """带纵向滚动条的容器（Canvas + Scrollbar + 内部 body）。

    用法：sf = _ScrollableFrame(parent); sf.pack(...)；把内容 build 到 ``sf.body``。
    滚轮滚动只在指针位于容器内时生效（bind_all 覆盖子控件）；
    Combobox / Spinbox 及下拉弹层（独立顶层窗口）上不抢滚轮，交给原生行为。
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=12)
        self._win_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    # ---- 布局回调 ----
    def _on_body_configure(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e) -> None:
        self.canvas.itemconfigure(self._win_id, width=e.width)

    # ---- 滚轮（bind_all：指针在容器内任意子控件上都能滚页面）----
    def _bind_wheel(self, _e=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _e=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, e) -> None:
        w = e.widget
        try:
            if w.winfo_toplevel() is not self.winfo_toplevel():
                return          # 下拉弹层等独立窗口，交给原生
            if isinstance(w, (ttk.Combobox, ttk.Spinbox)):
                return          # 自带滚轮行为的控件不抢
        except Exception:
            pass
        self.canvas.yview_scroll(-int(e.delta / 120), "units")


def _record_key(event, var) -> str:
    key = event.keysym.lower()
    if key in _IGNORE_KEYS:
        return "break"
    mods = []
    if event.state & 0x4:
        mods.append("ctrl")
    if event.state & 0x20000 or event.state & 0x8:
        mods.append("alt")
    if event.state & 0x1:
        mods.append("shift")
    mods.sort(key=_MOD_ORDER.index)
    name = {"escape": "esc", "return": "enter", "prior": "page up",
            "next": "page down"}.get(key, key)
    var.set("+".join(mods + [name]))
    return "break"


def _hotkey_status(app) -> str:
    if app.hotkeys is None:
        return ""
    if app.hotkeys.errors:
        return "⚠ " + "；".join(app.hotkeys.errors[:2])
    return "当前生效：" + app.hotkeys.summary()


