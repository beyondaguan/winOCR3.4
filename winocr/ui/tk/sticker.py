# -*- coding: utf-8 -*-
"""划词翻译小贴条 —— 选区/划词后弹出的轻量悬浮窗。

Snapaste 风格：无边框、置顶、可拖拽、可复制。纯 Tk 实现，不依赖任何
外部库。显示「原文 + 译文」两段，支持一键复制与置顶切换。

它不负责「怎么拿到选中文本」（那是选区监听/热键的事），只负责把
已经拿到的结果漂亮地弹出来 —— 这样手动划词、自动划词、选区蒙层
三种入口都能复用同一个贴条。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class StickerWindow(tk.Toplevel):
    """独立的小贴条窗口，跟随选区/划词结果弹出。"""

    def __init__(self, parent, ui=None) -> None:
        super().__init__(parent)
        self.ui = ui                          # 有 ui 才显示朗读按钮（朗读走 TTS 服务）
        self.title("WinOCR 划词")
        self.withdraw()                       # 初始隐藏，用时再弹
        self.overrideredirect(True)           # 无边框
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self._pinned = True
        self._drag_offset = (0, 0)
        self._current_original = ""      # 最近一次原文，供方向切换重译

        self._build()
        # 失焦不强制关闭：划词后用户可能还要复制/看，留给关闭按钮处理
        self.protocol("WM_DELETE_WINDOW", self.hide)

    # ==================================================================
    def _build(self) -> None:
        # 整窗细边框 + 浅灰底，模拟 SnowShot 卡片感
        self.config(bg=theme.BORDER, padx=1, pady=1)

        # ---- 标题栏（拖拽区） ----
        bar = tk.Frame(self, bg=theme.CARD_BG, height=26)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.bind("<ButtonPress-1>", self._on_drag_start)
        bar.bind("<B1-Motion>", self._on_drag_move)

        tk.Label(bar, text="🔍 划词翻译", bg=theme.CARD_BG,
                 fg=theme.ACCENT, font=theme.UI_FONT_BOLD,
                 padx=8).pack(side=tk.LEFT)

        self._pin_btn = tk.Label(bar, text="📌", bg=theme.CARD_BG,
                                 fg=theme.TEXT_MUTED, cursor="hand2", padx=4)
        self._pin_btn.pack(side=tk.RIGHT)
        self._pin_btn.bind("<Button-1>", lambda e: self._toggle_pin())
        self._pin_btn.bind("<Enter>", lambda e: self._pin_btn.config(fg=theme.ACCENT))
        self._pin_btn.bind("<Leave>", lambda e: self._pin_btn.config(
            fg=theme.ACCENT if self._pinned else theme.TEXT_MUTED))

        self._close_btn = tk.Label(bar, text="✕", bg=theme.CARD_BG,
                                   fg=theme.TEXT_MUTED, cursor="hand2", padx=6)
        self._close_btn.pack(side=tk.RIGHT)
        self._close_btn.bind("<Button-1>", lambda e: self.hide())
        self._close_btn.bind("<Enter>", lambda e: self._close_btn.config(fg=theme.DANGER))
        self._close_btn.bind("<Leave>", lambda e: self._close_btn.config(fg=theme.TEXT_MUTED))

        # ---- 原文 ----
        self._body = tk.Frame(self, bg=theme.CARD_BG)
        self._body.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        # 绑定拖拽到整个 body（标题栏太窄不好拖）
        self._body.bind("<ButtonPress-1>", self._on_drag_start)
        self._body.bind("<B1-Motion>", self._on_drag_move)

        f1 = tk.Frame(self._body, bg=theme.CARD_BG)
        f1.pack(fill=tk.X, padx=8, pady=(6, 2))
        tk.Label(f1, text="原文", bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                 font=theme.UI_FONT_SMALL).pack(side=tk.LEFT)
        self._orig = tk.Text(f1, height=3, wrap=tk.WORD, relief=tk.FLAT,
                             bd=0, bg=theme.CANVAS_BG, fg=theme.TEXT_MAIN,
                             font=theme.UI_FONT, padx=6, pady=4,
                             highlightthickness=0, state=tk.DISABLED)
        self._orig.pack(fill=tk.X, padx=8, pady=(0, 4))

        # ---- 译文 ----
        f2 = tk.Frame(self._body, bg=theme.CARD_BG)
        f2.pack(fill=tk.X, padx=8, pady=(2, 4))
        tk.Label(f2, text="译文", bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                 font=theme.UI_FONT_SMALL).pack(side=tk.LEFT)
        self._tran = tk.Text(f2, height=3, wrap=tk.WORD,
                             relief=tk.FLAT, bd=0, bg=theme.ASSIST_BG,
                             fg=theme.TEXT_MAIN, font=theme.UI_FONT,
                             padx=6, pady=4, highlightthickness=0,
                             state=tk.DISABLED,
                             highlightbackground=theme.ASSIST_BORDER)
        self._tran.pack(fill=tk.X, padx=8, pady=(0, 6))

        # ---- 底部按钮 ----
        foot = tk.Frame(self, bg=theme.CARD_BG)
        foot.pack(fill=tk.X, side=tk.TOP)
        theme.accent_button(foot, "复制原文", self._copy_orig,
                            font=theme.UI_FONT_SMALL, padx=8).pack(side=tk.LEFT, padx=8, pady=6)
        theme.accent_button(foot, "复制译文", self._copy_tran,
                            font=theme.UI_FONT_SMALL, padx=8).pack(side=tk.LEFT, pady=6)
        if self.ui is not None:
            ttk.Button(foot, text="🔊", width=3, command=self._read_aloud
                       ).pack(side=tk.LEFT, padx=(6, 0), pady=6)
        ttk.Button(foot, text="关闭", command=self.hide).pack(side=tk.RIGHT, padx=8, pady=6)

        # ---- 方向切换：仅当持有 ui（可重译）时显示 ----
        if self.ui is not None:
            dirf = tk.Frame(self, bg=theme.CARD_BG)
            dirf.pack(fill=tk.X, side=tk.TOP)
            tk.Label(dirf, text="方向", bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                     font=theme.UI_FONT_SMALL).pack(side=tk.LEFT, padx=8)
            ttk.Button(dirf, text="自动", width=5,
                       command=lambda: self._retranslate(None)).pack(side=tk.LEFT, padx=2, pady=4)
            ttk.Button(dirf, text="译中", width=5,
                       command=lambda: self._retranslate("zh-CN")).pack(side=tk.LEFT, padx=2)
            ttk.Button(dirf, text="译英", width=5,
                       command=lambda: self._retranslate("en")).pack(side=tk.LEFT, padx=2)

    # ==================================================================
    @staticmethod
    def _sanitize(text: str) -> str:
        """清洗无法安全塞进 Tk Text 的控制字符（尤其 \\x00，insert 会抛 TclError）。

        剪贴板/网页选区等外部文本常混入 NUL 或控制字符，直接 insert 会抛异常，
        外层 show_sticker 又有 try/except 静默吞掉 → 图贴永远停在占位。这里
        只去掉 C0 控制字符（保留 \\t \\n \\r），其余原样保留。
        """
        if not text:
            return text
        return "".join(
            ch for ch in text
            if ch in ("\t", "\n", "\r") or ord(ch) >= 0x20
        )

    def show(self, original: str, translation: str = "") -> None:
        """填充内容并显示到屏幕（默认贴在当前光标/选区附近）。"""
        self._current_original = original or ""
        original = self._sanitize(original or "")
        translation = self._sanitize(translation or "")
        was_visible = self.winfo_viewable()
        for box, txt in ((self._orig, original), (self._tran, translation)):
            box.config(state=tk.NORMAL)
            box.delete("1.0", tk.END)
            box.insert("1.0", txt)
            box.config(state=tk.DISABLED)
        # 自适应高度：原始内容行数驱动，封顶避免超屏
        self._orig.config(height=min(max(original.count("\n") + 1, 2), 12))
        self._tran.config(height=min(max(translation.count("\n") + 1, 2), 12))

        # 注意：绝不用 self.lift() —— lift() 会激活本窗口并抢走键盘焦点，
        # 导致「划词贴图弹出后，下一次取词读到的是 WinOCR 自己的空窗口、
        # 新选中文字不更新」。overrideredirect + -topmost 已保证置顶显示，
        # 且不夺焦点，用户可继续在其它软件里选词。
        self.deiconify()
        self.update_idletasks()
        try:
            self.attributes("-topmost", self._pinned)
        except Exception:
            pass
        # 仅首次弹出时定位到光标附近；**已显示时不再重定位**，否则「即时弹窗」后
        # 异步回填（或用户刚拖过位置）会被强制拽回光标处，造成跳动。
        if not was_visible:
            self._place_near_cursor()

    def hide(self) -> None:
        try:
            self.withdraw()
        except Exception:
            pass

    # ---- 拖拽 ----
    def _on_drag_start(self, event):
        self._drag_offset = (event.x_root - self.winfo_x(),
                             event.y_root - self.winfo_y())

    def _on_drag_move(self, event):
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.geometry(f"+{x}+{y}")

    # ---- 置顶切换 ----
    def _toggle_pin(self):
        self._pinned = not self._pinned
        try:
            self.attributes("-topmost", self._pinned)
        except Exception:
            pass
        self._pin_btn.config(fg=theme.ACCENT if self._pinned else theme.TEXT_MUTED)

    # ---- 复制 ----
    def _copy_orig(self):
        self._copy(self._orig.get("1.0", tk.END))

    def _copy_tran(self):
        self._copy(self._tran.get("1.0", tk.END))

    # ---- 朗读 ----
    def _read_aloud(self):
        """读译文；没译文就读原文（划词看外语单词时，多半想听译文/原词发音）。"""
        text = self._tran.get("1.0", tk.END).strip() \
            or self._orig.get("1.0", tk.END).strip()
        if not text:
            return
        try:
            self.ui.do_tts_read(text)
        except Exception:
            pass

    # ---- 方向切换重译 ----
    def _retranslate(self, target):
        """用户点了「自动/译中/译英」：用所选方向对当前原文重译。

        先把译文区置为「翻译中」，再把请求交给 ui（TkUi 走后台线程，
        结果经事件总线回填本贴条）。无 ui 或原文为空时静默忽略。
        """
        if self.ui is None:
            return
        orig = (self._current_original or "").strip()
        if not orig:
            return
        try:
            self.show(orig, "⏳ 翻译中…")
            self.ui.translate_sticker(orig, target)
        except Exception:
            pass

    def _copy(self, text: str):
        text = text.strip()
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._status_flash("已复制")
        except Exception:
            pass

    def _status_flash(self, msg: str):
        try:
            old = self._close_btn.cget("text")
            self._close_btn.config(text=msg)
            self.after(900, lambda: self._close_btn.config(text="✕"))
        except Exception:
            pass

    # ---- 放置到光标附近 ----
    def _place_near_cursor(self):
        try:
            x = self.winfo_pointerx() + 12
            y = self.winfo_pointery() + 12
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w = self.winfo_width()
            h = self.winfo_height()
            if x + w > sw:
                x = max(0, sw - w - 8)
            if y + h > sh:
                y = max(0, sh - h - 8)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass
