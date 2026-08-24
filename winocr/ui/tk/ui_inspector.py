# -*- coding: utf-8 -*-
"""界面取色定位器 —— 「这块难看的颜色，在设置里到底叫什么？」

痛点：外观页列了十几个角色（主色 / 卡片背景 / AI 气泡边框…），
用户看到界面上某块颜色不顺眼，却对不上是哪个角色，只能一个个试。

做法：把主窗口自身当参照物。用户在**本程序窗口**上点一下，
我们用 Tk 的 `winfo_containing()` 拿到那个坐标下的真实控件，
再按控件类型 + 它当前的颜色反查属于哪个主题角色，
最后回传 (角色 token, 该处像素色)。

为什么按控件反查而不是纯比色：纯比色会把「颜色恰好相同的两个角色」
搞混（例如浅色主题下 card_bg 与 input_bg 都是 #ffffff）。
控件类型能把这层歧义消掉：Text/Entry → input_bg，Frame → card_bg。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Tuple

from . import theme

# 控件类名 → 候选角色（按优先级）。类名取 widget.winfo_class()
_CLASS_ROLES = {
    "Text": ("input_bg",),
    "Entry": ("input_bg",),
    "TEntry": ("input_bg",),
    "TCombobox": ("input_bg",),
    "Listbox": ("input_bg",),
    "Canvas": ("canvas_bg",),
    "Frame": ("card_bg",),
    "TFrame": ("card_bg",),
    "Labelframe": ("card_bg",),
    "TLabelframe": ("card_bg",),
    "Toplevel": ("card_bg",),
    "Tk": ("card_bg",),
    "TNotebook": ("card_bg",),
    "Label": ("text_main",),
    "TLabel": ("text_main",),
    "Button": ("accent",),
    "TButton": ("card_bg",),
}


def _norm(color: str) -> str:
    """把 Tk 认的任意颜色名/#rgb 规范成小写 #rrggbb。"""
    c = (color or "").strip()
    if c.startswith("#") and len(c) == 7:
        return c.lower()
    return c.lower()


def _widget_color(w) -> str:
    """取控件"最能代表它"的颜色：优先背景，Label 类取前景。"""
    cls = w.winfo_class()
    keys = ("foreground", "fg") if cls in ("Label", "TLabel") else ("background", "bg")
    for k in keys:
        try:
            v = w.cget(k)
            if v:
                return _norm(str(v))
        except Exception:
            continue
    # ttk 控件 cget 常拿不到色，退回问 style
    try:
        from tkinter import ttk
        style = ttk.Style(w)
        opt = "foreground" if cls in ("TLabel",) else "background"
        v = style.lookup(cls, opt)
        if v:
            return _norm(str(v))
    except Exception:
        pass
    return ""


def _match_role(w, color: str) -> Optional[str]:
    """控件 + 颜色 → 主题角色 token。先按颜色精确命中，再按控件类型兜底。"""
    cls = w.winfo_class()
    candidates = _CLASS_ROLES.get(cls, ())

    # 1) 该控件类型的候选角色里，有谁的当前色与取到的色一致 → 就是它
    for tok in candidates:
        if color and _norm(theme.ACTIVE.get(tok, "")) == color:
            return tok
    # 2) 全局找颜色完全相同的角色（能命中 accent / border / 气泡色等）
    if color:
        for tok in theme.TOKENS:
            if _norm(theme.ACTIVE.get(tok, "")) == color:
                return tok
    # 3) 颜色对不上（可能被自定义或系统绘制），按控件类型给个最合理的
    return candidates[0] if candidates else None


def _pixel_at(x: int, y: int) -> str:
    """抓屏取单像素。PIL 缺失时返回空串（此时只靠控件类型判角色）。"""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(x, y, x + 1, y + 1))
        r, g, b = img.convert("RGB").getpixel((0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return ""


def inspect_ui_role(window, on_pick: Callable[[Optional[Tuple[str, str]]], None]) -> None:
    """进入定位模式：主窗口浮到最前，等用户点一下界面上的某块区域。

    on_pick 收到 (token, "#rrggbb")；用户按 Esc / 右键取消时收到 None。
    """
    root = window.root
    prev_status = ""
    try:
        prev_status = window.status_label.cget("text")
    except Exception:
        pass

    try:
        root.deiconify()
        root.lift()
        root.focus_force()
    except Exception:
        pass
    window.set_status("定位模式：点一下界面上你想改的区域（Esc / 右键取消）")

    state = {"done": False}

    # 记录进入前的全局绑定（TkUi 在 run() 里 bind_all 了 Esc=隐藏窗口等），
    # _finish 时**恢复**而不是 unbind_all —— unbind_all 会把程序的全局快捷键
    # 一并清掉，一次取色之后 Esc 隐藏主窗口就永久失效了（真实踩过）。
    prev_binds = {}
    for seq in ("<Button-1>", "<Button-3>", "<Escape>"):
        try:
            prev_binds[seq] = root.bind_all(seq)
        except Exception:
            prev_binds[seq] = None

    def _finish(result) -> None:
        if state["done"]:
            return
        state["done"] = True
        for seq, prev in prev_binds.items():
            try:
                if prev:
                    root.bind_all(seq, prev)     # 恢复原有绑定链
                else:
                    root.unbind_all(seq)          # 原本无绑定，清掉本次的
            except Exception:
                pass
        try:
            root.config(cursor="")
        except Exception:
            pass
        try:
            window.set_status(prev_status or "就绪")
        except Exception:
            pass
        try:
            on_pick(result)
        except Exception:
            pass

    def _on_click(event):
        w = event.widget
        try:
            # 点在 root 上时用坐标反查最里层控件，拿到的角色才准
            hit = root.winfo_containing(event.x_root, event.y_root)
            if hit is not None:
                w = hit
        except Exception:
            pass
        color = _widget_color(w) or _pixel_at(event.x_root, event.y_root)
        if not color.startswith("#"):
            color = _pixel_at(event.x_root, event.y_root) or "#ffffff"
        tok = _match_role(w, color)
        _finish((tok, color) if tok else None)
        return "break"

    try:
        root.config(cursor="crosshair")
    except Exception:
        pass
    root.bind_all("<Button-1>", _on_click)
    root.bind_all("<Button-3>", lambda e: _finish(None))
    root.bind_all("<Escape>", lambda e: _finish(None))
