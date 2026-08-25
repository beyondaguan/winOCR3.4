# -*- coding: utf-8 -*-
"""ttk 控件配色 — 从 app.py 拆出的纯函数（无 TkUi 依赖）。

深色模式必须换掉 vista 主题：vista/xpnative 的按钮、下拉框、滚动条
是系统绘制的位图，**无法染色**，深色下会留一片刺眼的浅色控件。
clam 是纯 Tk 绘制，什么都能改，所以深色走 clam、浅色继续用 vista
（原生观感更好）。
"""
from __future__ import annotations

from tkinter import ttk

from . import theme


def apply_ttk_style(root, dark: bool) -> None:
    """给 ttk 原生控件上色。root 为 Tk 根窗口。"""
    try:
        style = ttk.Style(root)
        names = style.theme_names()
        if not dark:
            if "vista" in names:
                style.theme_use("vista")
            # 浅色不强改控件底色：vista 原生观感本身就对，只统一字号
            style.configure(".", font=theme.UI_FONT)
            return

        if "clam" in names:
            style.theme_use("clam")

        bg = theme.CARD_BG            # 面板/窗口底
        raised = theme.INPUT_BG       # 按钮/下拉等"凸起"控件底（比面板略亮）
        trough = theme.CANVAS_BG      # 滚动条凹槽/进度条底（最暗）
        fg = theme.TEXT_MAIN
        border = theme.BORDER
        inp = theme.INPUT_BG
        root.configure(bg=bg)

        style.configure(".", background=bg, foreground=fg,
                        fieldbackground=inp, bordercolor=border,
                        font=theme.UI_FONT)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg,
                        bordercolor=border)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TRadiobutton", background=bg, foreground=fg)
        style.configure("TButton", background=raised, foreground=fg,
                        bordercolor=border, focuscolor=border)
        style.map("TButton",
                  background=[("active", theme.ACCENT_HOVER),
                              ("disabled", bg)],
                  foreground=[("active", "white"),
                              ("disabled", theme.TEXT_MUTED)])
        style.configure("TEntry", fieldbackground=inp, foreground=fg,
                        insertcolor=fg, bordercolor=border)
        style.configure("TSpinbox", fieldbackground=inp, foreground=fg,
                        background=raised, arrowcolor=fg, bordercolor=border)
        style.configure("TCombobox", fieldbackground=inp, foreground=fg,
                        background=raised, arrowcolor=fg, bordercolor=border)
        style.map("TCombobox", fieldbackground=[("readonly", inp)],
                  foreground=[("readonly", fg)])
        # 下拉列表是 Tk 原生 Listbox，只能用 option 数据库改
        root.option_add("*TCombobox*Listbox.background", inp)
        root.option_add("*TCombobox*Listbox.foreground", fg)
        root.option_add("*TCombobox*Listbox.selectBackground",
                        theme.ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", "white")
        style.configure("TNotebook", background=bg, bordercolor=border)
        style.configure("TNotebook.Tab", background=trough, foreground=fg,
                        padding=(10, 4))
        style.map("TNotebook.Tab",
                  background=[("selected", bg)],
                  foreground=[("selected", theme.ACCENT)])
        style.configure("TPanedwindow", background=bg)
        style.configure("Vertical.TScrollbar", background=raised,
                        troughcolor=trough, bordercolor=border, arrowcolor=fg)
        style.configure("Horizontal.TScrollbar", background=raised,
                        troughcolor=trough, bordercolor=border, arrowcolor=fg)
        style.configure("Horizontal.TProgressbar", background=theme.ACCENT,
                        troughcolor=trough, bordercolor=border)
        style.configure("TSeparator", background=border)
    except Exception:
        pass
