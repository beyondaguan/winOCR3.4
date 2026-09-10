# -*- coding: utf-8 -*-
"""AI 对话面板 — 气泡式界面 + 多模态附件。

相对 2.0 的三处实质修正：
  1. 状态从模块级全局（_chatting / _chat_attachments / _chat_gen …）收进实例，
     再也不会出现「global 忘了声明 → 面板整个失效」那类 bug；
  2. 附件解析放后台线程并即时反馈，选一个大 PDF 不再卡死界面；
  3. 上下文来源显式可控（勾选「带上识别原文 / 最近截图」），
     不像 2.0 那样偷偷把上一张截图塞进每一轮请求。
"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from . import theme
from ...core.types import Attachment, ChatMessage

PLACEHOLDER = "问点什么…（Enter 发送，Shift+Enter 换行，Ctrl+V 可粘贴图片）"

# AI 请求超过此秒数仍无响应 → 自动释放 _busy 标志，让用户能继续发新消息
# （硅基流动免费档冷启动实测偶发 60~97s 卡顿，超过连接自身 timeout 后才会抛错；
# 但抛错/正常完成路径若因线程异常丢失，_busy 会永久占用——加此兜底）
_BUSY_TIMEOUT = 120.0

ICONS = {"image": "🖼", "pdf": "📕", "doc": "📘", "xlsx": "📗", "text": "📄"}

FILE_TYPES = [
    (
        "所有支持",
        "*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp "
        "*.pdf *.doc *.docx *.xls *.xlsx *.csv "
        "*.txt *.md *.json *.xml *.html *.py *.log",
    ),
    ("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp"),
    ("PDF", "*.pdf"),
    ("Word", "*.doc *.docx"),
    ("Excel / CSV", "*.xls *.xlsx *.csv"),
    ("文本 / 代码", "*.txt *.md *.json *.xml *.html *.py *.log"),
]


class ChatPanel:
    def __init__(self, parent, ui, window) -> None:
        self.ui = ui
        self.app = ui.app
        self.root = ui.root
        self.window = window

        self.attachments: List[Attachment] = []
        self.pending: List[str] = []  # 正在后台解析的文件名
        self.bubbles: List[tk.Label] = []
        self.rows: List[ttk.Frame] = []
        self.thinking: Optional[ttk.Frame] = None
        self._busy = threading.Event()
        self._busy_since = 0.0  # _busy.set() 的时间戳；超时强制清零用
        self._gen = 0  # 清空对话后丢弃在途回复
        self._stream_widget: Optional[tk.Text] = None  # 正在打字机输出的 Text
        self._stream_after: Optional[str] = None  # after 任务 id
        self._stream_idx: int = 0
        self._stream_text: str = ""
        self._stream_gen: int = (
            0  # 当前流式任务对应的 gen，防止旧任务在 gen 变更后继续跑
        )

        self.frame = ttk.Frame(parent)
        self._build()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build(self) -> None:
        self._build_header()
        self._build_options()
        self._build_input()  # 先占底部，避免被消息区挤出可视范围
        self._build_attachments()
        self._build_canvas()
        self._greet()

    def _build_header(self) -> None:
        h = tk.Frame(self.frame, bg=theme.ACCENT, height=46)
        h.pack(fill=tk.X, side=tk.TOP)
        h.pack_propagate(False)
        tk.Label(
            h, text="🤖", font=("Microsoft YaHei", 17), bg=theme.ACCENT, fg="white"
        ).pack(side=tk.LEFT, padx=(10, 6))
        box = tk.Frame(h, bg=theme.ACCENT)
        box.pack(side=tk.LEFT, fill=tk.Y, pady=4)
        tk.Label(
            box, text="WinOCR AI", font=theme.TITLE_FONT, bg=theme.ACCENT, fg="white"
        ).pack(anchor=tk.W)
        self.sub_label = tk.Label(
            box,
            text="解读识别结果 · 看图 · 读文档",
            font=theme.UI_FONT_SMALL,
            bg=theme.ACCENT,
            fg=theme.ACCENT_SUBTITLE,
        )
        self.sub_label.pack(anchor=tk.W)
        tk.Button(
            h,
            text="收起",
            command=self.window.toggle_chat,
            bg=theme.ACCENT,
            fg="white",
            activebackground=theme.ACCENT_HOVER,
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            font=theme.UI_FONT_SMALL,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=8)

    def _build_options(self) -> None:
        bar = ttk.Frame(self.frame)
        bar.pack(fill=tk.X, padx=6, pady=(4, 2))

        self.var_use_text = tk.BooleanVar(value=True)
        self.var_use_shot = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="带上识别原文", variable=self.var_use_text).pack(
            side=tk.LEFT
        )
        ttk.Checkbutton(bar, text="带上最近截图", variable=self.var_use_shot).pack(
            side=tk.LEFT, padx=(10, 0)
        )

        ttk.Button(bar, text="清空对话", width=9, command=self.clear_history).pack(
            side=tk.RIGHT
        )

    def _build_attachments(self) -> None:
        wrap = ttk.Frame(self.frame)
        wrap.pack(fill=tk.X, side=tk.BOTTOM, padx=6, pady=(2, 0))
        top = ttk.Frame(wrap)
        top.pack(fill=tk.X)
        ttk.Label(top, text="📎 附件", foreground=theme.TEXT_MUTED).pack(side=tk.LEFT)
        ttk.Button(top, text="添加文件", width=9, command=self.open_file_dialog).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self.attach_inner = ttk.Frame(wrap)
        self.attach_inner.pack(fill=tk.X, pady=(2, 0))
        self._render_chips()

    def _build_input(self) -> None:
        box = ttk.Frame(self.frame, padding=(6, 4))
        box.pack(fill=tk.X, side=tk.BOTTOM)

        self.btn_send = theme.accent_button(box, "发送", self.send, width=6, padx=0)
        self.btn_send.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))

        holder = ttk.Frame(box)
        holder.pack(fill=tk.BOTH, side=tk.LEFT, expand=True)
        self.input = tk.Text(
            holder,
            height=2,
            wrap=tk.WORD,
            font=theme.UI_FONT,
            bg=theme.INPUT_BG,
            fg=theme.TEXT_HINT,
            relief=tk.FLAT,
            bd=1,
            highlightbackground=theme.INPUT_BORDER,
            highlightthickness=1,
            padx=6,
            pady=4,
        )
        self.input.pack(fill=tk.BOTH, expand=True)
        self.input.insert("1.0", PLACEHOLDER)

        self.input.bind("<FocusIn>", self._on_focus_in)
        self.input.bind("<FocusOut>", self._on_focus_out)
        self.input.bind("<Return>", lambda e: (self.send(), "break")[1])
        self.input.bind("<Shift-Return>", lambda e: None)
        self.input.bind("<Control-Return>", lambda e: (self.send(), "break")[1])
        self.input.bind("<Control-v>", self._on_paste)
        self.input.bind("<Control-V>", self._on_paste)

        self._enable_drop()

    def _build_canvas(self) -> None:
        wrap = ttk.Frame(self.frame)
        wrap.pack(fill=tk.BOTH, expand=True, padx=6)

        self.canvas = tk.Canvas(wrap, highlightthickness=0, bg=theme.CANVAS_BG)
        sb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner = tk.Frame(self.canvas, bg=theme.CANVAS_BG)
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        # 记住 item id：2.0 用 find_withtag("window") 取不到（那是类型不是 tag），
        # 结果内帧宽度永远不跟随窗口，长文本被裁切。
        self.inner_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor=tk.NW
        )

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind(
            "<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        )
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _greet(self) -> None:
        ai = self.app.services.get("ai")
        if ai is None or not ai.available():
            self.add_bubble(
                "system",
                "AI 未就绪：请在「API 设置」里填入 GLM API Key。\n"
                "（智谱开放平台 glm-4-flash / glm-4v-flash 有免费额度）",
            )
        else:
            self.add_bubble(
                "assistant",
                "识别完成后可以直接问我，比如「翻译成中文」「这段代码干嘛的」"
                "「总结这份 PDF」。图片、PDF、Word、Excel 都能拖进来。",
            )

    # ==================================================================
    # 气泡
    # ==================================================================
    def _wrap_len(self, width: Optional[int] = None) -> int:
        if width is None:
            try:
                width = self.canvas.winfo_width()
            except Exception:
                width = 0
        if not width or width <= 1:
            return 460
        return max(160, int(width * 0.76))

    def _on_canvas_resize(self, event) -> None:
        try:
            self.canvas.itemconfig(self.inner_id, width=event.width)
        except Exception:
            return
        wrap = self._wrap_len(event.width)
        for b in self.bubbles:
            # b 可能是 Text（user）或 Frame（assistant/system border）
            # 先尝试 Text：不重设 width（Text 随父 Frame 撑满），只重算 height
            try:
                b.update_idletasks()
                h = b.count("1.0", "end-1c", "displaylines")
                n = h[0] if isinstance(h, tuple) else h
                b.config(height=max(2, int(n)))
                continue
            except Exception:
                pass
            # 再尝试 Label（旧数据兼容）：调 wraplength
            try:
                b.config(wraplength=wrap)
                continue
            except Exception:
                pass
            # b 是 Frame → 递归找内部 Text 重算 height
            try:
                for child in b.winfo_children():
                    try:
                        child.update_idletasks()
                        h = child.count("1.0", "end-1c", "displaylines")
                        n = h[0] if isinstance(h, tuple) else h
                        child.config(height=max(2, int(n)))
                        break
                    except Exception:
                        pass
                    try:
                        child.config(wraplength=wrap)
                        break
                    except Exception:
                        pass
                continue
            except Exception:
                pass

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass
        return "break"

    def add_bubble(self, role: str, text: str) -> None:
        """只能在主线程调用；后台线程请走 ui.post。"""
        colors = theme.BUBBLE.get(role, theme.BUBBLE["system"])
        outer = tk.Frame(self.inner, bg=theme.CANVAS_BG)
        outer.pack(fill=tk.X, pady=3, padx=4)

        common = dict(
            wraplength=self._wrap_len(),
            justify=tk.LEFT,
            font=theme.UI_FONT,
            bg=colors["bg"],
            fg=colors["fg"],
            padx=theme.BUBBLE_PAD,
            pady=theme.BUBBLE_PAD,
            relief=tk.FLAT,
            bd=0,
        )
        if role == "user":
            # user 消息也用 Text（只读），支持选择和复制
            border = tk.Frame(outer, bg=colors["border"], bd=0)
            border.pack(anchor=tk.E, padx=4, fill=tk.X)
            txt = tk.Text(
                border,
                wrap=tk.WORD,
                width=self._wrap_len(),
                font=theme.UI_FONT,
                bg=colors["bg"],
                fg=colors["fg"],
                padx=theme.BUBBLE_PAD,
                pady=theme.BUBBLE_PAD,
                bd=0,
                highlightthickness=0,
                relief=tk.FLAT,
                takefocus=1,
            )
            txt.insert("1.0", text)

            def _readonly_key_u(e, t=txt):
                if e.state & 0x4:
                    return
                if e.keysym in (
                    "Left",
                    "Right",
                    "Up",
                    "Down",
                    "Home",
                    "End",
                    "Prior",
                    "Next",
                ):
                    return
                return "break"

            txt.bind("<KeyPress>", _readonly_key_u)
            txt.bind("<Control-a>", lambda e: (self._select_all_text(txt), "break")[1])
            txt.update_idletasks()
            h = txt.count("1.0", "end-1c", "displaylines")
            n = h[0] if isinstance(h, tuple) else h
            txt.config(height=max(2, int(n)))
            txt.pack(fill=tk.X, expand=True)
            txt.bind("<Button-3>", lambda e, t=text: self._copy_text(t))
            bubble = txt
        else:
            row = tk.Frame(outer, bg=theme.CANVAS_BG)
            row.pack(anchor=tk.W, fill=tk.X)
            if role == "assistant":
                tk.Label(
                    row,
                    text="🤖",
                    font=("Microsoft YaHei", 13),
                    bg=theme.CANVAS_BG,
                    fg=theme.ACCENT,
                ).pack(side=tk.LEFT, padx=(0, 4), anchor=tk.N)
            # 1px 边框用外层 Frame 模拟；Text 用「只读但可选可复制」：
            # 不要 state=DISABLED（那会连选择/复制一起禁掉），而是 NORMAL +
            # 拦截普通编辑事件（注意：不能绑 "<Key>"——它会连带拦截 Ctrl+C/Ctrl+A）。
            # border 不设 width：固定宽度在窄 canvas 下会让气泡被裁到画布外、用户看不到
            # 也点不到；让 border 自然撑满 row Frame（row.fill=X 已设），Text 在 border 内
            # 撑满（fill=X expand=True），wrap=WORD 按 canvas 实际像素换行。
            border = tk.Frame(row, bg=colors["border"], bd=0)
            border.pack(side=tk.LEFT, anchor=tk.W, padx=4, fill=tk.X, expand=True)
            txt = tk.Text(
                border,
                wrap=tk.WORD,
                font=theme.UI_FONT,
                bg=colors["bg"],
                fg=colors["fg"],
                padx=theme.BUBBLE_PAD,
                pady=theme.BUBBLE_PAD,
                bd=0,
                highlightthickness=0,
                relief=tk.FLAT,
                takefocus=1,
            )
            # assistant / system 消息渲染简化 Markdown
            self._render_markdown(txt, text)

            def _readonly_key(e, t=txt):
                # Control 修饰 → 放行（让 Ctrl+C / Ctrl+A / Ctrl+X 正常工作）
                if e.state & 0x4:
                    return
                # 方向键 / Home/End/PageUp/Down → 放行（光标定位）
                if e.keysym in (
                    "Left",
                    "Right",
                    "Up",
                    "Down",
                    "Home",
                    "End",
                    "Prior",
                    "Next",
                ):
                    return
                # 其它（BackSpace/Delete/字符输入/Enter/Tab/Ctrl+V 粘贴）→ 拦截
                return "break"

            txt.bind("<KeyPress>", _readonly_key)
            txt.bind("<Control-a>", lambda e: (self._select_all_text(txt), "break")[1])
            txt.update_idletasks()
            # 用 displaylines 获取 wrap 后的真实显示行数（逻辑行数在单行超长时不准）
            h = txt.count("1.0", "end-1c", "displaylines")
            n = h[0] if isinstance(h, tuple) else h
            txt.config(height=max(2, int(n)))
            txt.pack(fill=tk.X, expand=True)
            txt.bind("<Button-3>", lambda e, t=text: self._copy_text(t))
            bubble = border

        self.rows.append(outer)
        self.bubbles.append(bubble)
        self._scroll_bottom()

    # ==================================================================
    # Markdown 简化渲染（Text tag）
    # ==================================================================
    _MD_RE_HEADING = __import__("re").compile(r"^(#{1,6})\s+(.*)$")
    _MD_RE_BOLD = __import__("re").compile(r"\*\*(.+?)\*\*")
    _MD_RE_CODE = __import__("re").compile(r"`([^`]+)`")

    def _render_markdown(self, txt: tk.Text, text: str) -> None:
        """用 tk.Text tag 渲染 # 标题、**粗体**、`` `代码` ``。"""
        txt.delete("1.0", tk.END)
        font_name = (
            theme.UI_FONT[0]
            if isinstance(theme.UI_FONT, tuple)
            else theme.UI_FONT.actual("family")
        )
        font_size = (
            theme.UI_FONT[1]
            if isinstance(theme.UI_FONT, tuple)
            else theme.UI_FONT.actual("size")
        )

        # 预定义 tag 样式
        txt.tag_configure("md_h", font=(font_name, font_size + 4, "bold"))
        txt.tag_configure("md_b", font=(font_name, font_size, "bold"))
        code_bg = theme.BUBBLE.get("system", {}).get("bg", "#f0f0f0")
        txt.tag_configure("md_code", font=("Consolas", font_size), background=code_bg)

        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i > 0:
                txt.insert(tk.END, "\n")
            # 标题行：整行标记
            m = self._MD_RE_HEADING.match(line)
            if m:
                txt.insert(tk.END, m.group(2), ("md_h",))
                continue
            # 普通行：分段解析粗体和代码
            self._render_md_inline(txt, line)

    def _render_md_inline(self, txt: tk.Text, line: str) -> None:
        """对单行文本解析 **粗体** 和 `代码`，按顺序拼接。"""
        pos = 0
        # 合并所有 token 位置
        tokens = []
        for m in self._MD_RE_BOLD.finditer(line):
            tokens.append((m.start(), m.end(), "b", m.group(1)))
        for m in self._MD_RE_CODE.finditer(line):
            tokens.append((m.start(), m.end(), "c", m.group(1)))
        if not tokens:
            txt.insert(tk.END, line)
            return
        # 按起始位置排序，去重重叠
        tokens.sort(key=lambda x: x[0])
        seen = set()
        filtered = []
        for s, e, kind, content in tokens:
            if any(s < ex and e > sx for sx, ex in seen):
                continue
            seen.add((s, e))
            filtered.append((s, e, kind, content))
        # 拼接
        for s, e, kind, content in filtered:
            if pos < s:
                txt.insert(tk.END, line[pos:s])
            tag = "md_b" if kind == "b" else "md_code"
            txt.insert(tk.END, content, (tag,))
            pos = e
        if pos < len(line):
            txt.insert(tk.END, line[pos:])

    @staticmethod
    def _select_all_text(txt: tk.Text) -> str:
        """AI 回复 Text 的全选（Ctrl+A）。"""
        try:
            txt.tag_add(tk.SEL, "1.0", tk.END)
            txt.mark_set(tk.INSERT, "1.0")
            txt.see(tk.INSERT)
        except Exception:
            pass
        return "break"

    def _copy_text(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.window.set_status("已复制这条回复")
        except Exception:
            pass

    def _scroll_bottom(self) -> None:
        try:
            self.inner.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _show_thinking(self) -> None:
        if self.thinking is not None:
            return
        outer = tk.Frame(self.inner, bg=theme.CANVAS_BG)
        outer.pack(fill=tk.X, pady=3, padx=4)
        row = tk.Frame(outer, bg=theme.CANVAS_BG)
        row.pack(anchor=tk.W, fill=tk.X)
        tk.Label(
            row,
            text="🤖",
            font=("Microsoft YaHei", 13),
            bg=theme.CANVAS_BG,
            fg=theme.ACCENT,
        ).pack(side=tk.LEFT, padx=(0, 4), anchor=tk.N)
        tk.Label(
            row,
            text="WinOCR 正在思考…",
            font=theme.UI_FONT,
            bg=theme.ASSIST_BG,
            fg=theme.TEXT_MUTED,
            padx=theme.BUBBLE_PAD,
            pady=theme.BUBBLE_PAD,
            relief=tk.FLAT,
            bd=0,
            highlightbackground=theme.ASSIST_BORDER,
            highlightthickness=1,
        ).pack(side=tk.LEFT, anchor=tk.W, padx=4)
        self.thinking = outer
        self._scroll_bottom()

    def _hide_thinking(self) -> None:
        if self.thinking is not None:
            try:
                self.thinking.destroy()
            except Exception:
                pass
        self.thinking = None

    # ==================================================================
    # 附件
    # ==================================================================
    def _render_chips(self) -> None:
        for w in list(self.attach_inner.children.values()):
            try:
                w.destroy()
            except Exception:
                pass
        if not self.attachments and not self.pending:
            ttk.Label(
                self.attach_inner,
                text="（可粘贴 / 拖拽图片或文档，也可点「添加文件」）",
                foreground=theme.TEXT_HINT,
                font=theme.UI_FONT_SMALL,
            ).pack(anchor=tk.W)
            return
        row = ttk.Frame(self.attach_inner)
        row.pack(fill=tk.X)
        for idx, att in enumerate(self.attachments):
            chip = ttk.Frame(row, relief=tk.SOLID, borderwidth=1)
            chip.pack(side=tk.LEFT, padx=2, pady=2)
            label = f"{ICONS.get(att.kind, '📎')} {att.display_name}"
            if att.error:
                label += " ⚠"
            lbl = ttk.Label(chip, text=label)
            lbl.pack(side=tk.LEFT, padx=(4, 1))
            if att.error:
                self._tooltip(lbl, att.error)
            ttk.Button(
                chip, text="×", width=2, command=lambda i=idx: self.remove_attachment(i)
            ).pack(side=tk.LEFT, padx=(1, 2))
        for name in self.pending:
            chip = ttk.Frame(row, relief=tk.SOLID, borderwidth=1)
            chip.pack(side=tk.LEFT, padx=2, pady=2)
            ttk.Label(chip, text=f"⏳ {name}", foreground=theme.TEXT_MUTED).pack(
                side=tk.LEFT, padx=4
            )

    @staticmethod
    def _tooltip(widget, text: str) -> None:
        tip = {"win": None}

        def show(_e):
            if tip["win"] is not None:
                return
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 2
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(
                win,
                text=text,
                bg="#ffffe0",
                fg="#333333",
                font=theme.UI_FONT_SMALL,
                relief=tk.SOLID,
                bd=1,
                padx=4,
                pady=2,
                wraplength=320,
                justify=tk.LEFT,
            ).pack()
            tip["win"] = win

        def hide(_e):
            if tip["win"] is not None:
                try:
                    tip["win"].destroy()
                except Exception:
                    pass
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def add_path(self, path: str) -> None:
        """添加文件附件。解析放后台，大 PDF 不再卡界面（2.0 是同步解析）。"""
        name = os.path.basename(path)
        if not os.path.isfile(path):
            self.window.set_status(f"文件不存在: {name}")
            return
        self.pending.append(name)
        self._render_chips()

        def _done(att: Attachment):
            self.ui.post(self._on_parsed, name, att)

        def _err(e):
            self.ui.post(
                self._on_parsed, name, Attachment(kind="text", path=path, error=str(e))
            )

        self.app.pipeline.run_async(
            self.app.pipeline.parse_attachment, path, on_done=_done, on_error=_err
        )

    def _on_parsed(self, name: str, att: Attachment) -> None:
        if not self._alive():
            return  # 面板已被 reload_ui 重建/销毁，静默丢弃在途回调
        if name in self.pending:
            self.pending.remove(name)
        self.attachments.append(att)
        self._render_chips()
        if att.error:
            self.window.set_status(f"附件 {att.display_name}: {att.error}")
        else:
            n = len(att.extracted_text)
            extra = f"，提取 {n} 字" if n else ""
            self.window.set_status(f"已添加附件: {att.display_name}{extra}")

    def add_image(self, image, name: str = "剪贴板图片") -> None:
        if image is None:
            return
        try:
            att = Attachment(kind="image", image=image.copy(), display_name=name)
        except Exception as e:
            self.window.set_status(f"图片附件失败: {e}")
            return
        self.attachments.append(att)
        self._render_chips()
        self.window.set_status(f"已添加图片附件: {name}")

    def remove_attachment(self, idx: int) -> None:
        if 0 <= idx < len(self.attachments):
            removed = self.attachments.pop(idx)
            self._render_chips()
            self.window.set_status(f"已移除附件: {removed.display_name}")

    def open_file_dialog(self) -> None:
        try:
            paths = filedialog.askopenfilenames(
                parent=self.root, title="选择要附加的文件", filetypes=FILE_TYPES
            )
        except Exception as e:
            self.window.set_status(f"选择文件失败: {e}")
            return
        for p in paths:
            self.add_path(p)

    def _on_paste(self, _event):
        """Ctrl+V：剪贴板是图片或文件路径就转成附件，否则走默认文本粘贴。"""
        try:
            from PIL import Image, ImageGrab

            data = ImageGrab.grabclipboard()
        except Exception:
            return None
        if data is None:
            return None
        if isinstance(data, Image.Image):
            self.add_image(data)
            return "break"
        if isinstance(data, (list, tuple)):
            added = False
            for p in data:
                if isinstance(p, str) and os.path.isfile(p):
                    self.add_path(p)
                    added = True
            if added:
                return "break"
        return None

    def _enable_drop(self) -> None:
        """Windows 拖放：优先 tkinterdnd2，退回原生 WM_DROPFILES。"""
        try:
            self.frame.drop_target_register("DND_Files")  # tkinterdnd2
            self.frame.dnd_bind("<<Drop>>", self._on_dnd)
            return
        except Exception:
            pass
        try:
            import ctypes
            from ctypes import wintypes

            # 64 位下 HWND 是 8 字节指针，不声明 argtypes 会被截断成 32 位
            ctypes.windll.shell32.DragAcceptFiles.argtypes = [
                wintypes.HWND,
                wintypes.BOOL,
            ]
            ctypes.windll.shell32.DragAcceptFiles(self.frame.winfo_id(), True)
            self.frame.bind("<WM_DROPFILES>", self._on_wm_drop)
        except Exception:
            pass

    def _on_dnd(self, event):
        for p in self.root.tk.splitlist(event.data):
            if os.path.isfile(p):
                self.add_path(p)

    def _on_wm_drop(self, event):
        if not getattr(event, "data", None):
            return
        for f in str(event.data).split("\x00"):
            f = f.strip().strip('"')
            if f and os.path.isfile(f):
                self.add_path(f)

    # ==================================================================
    # 发送
    # ==================================================================
    def _on_focus_in(self, _e):
        if self.input.get("1.0", "end-1c") == PLACEHOLDER:
            self.input.delete("1.0", tk.END)
            self.input.config(fg=theme.TEXT_MAIN)

    def _on_focus_out(self, _e):
        if not self.input.get("1.0", "end-1c").strip():
            self.input.delete("1.0", tk.END)
            self.input.insert("1.0", PLACEHOLDER)
            self.input.config(fg=theme.TEXT_HINT)

    def focus_input(self) -> None:
        try:
            self.input.focus_set()
        except Exception:
            pass

    def send(self) -> None:
        if self._busy.is_set():
            # 上一轮卡死超过 _BUSY_TIMEOUT（硅基流动偶发 97s 冷启动）→ 强制清零 + 提示用户
            if self._busy_since > 0 and time.time() - self._busy_since > _BUSY_TIMEOUT:
                self._busy.clear()
                self._busy_since = 0.0
                self._hide_thinking()
                theme.set_button_enabled(self.btn_send, True)
                self.window.set_status(
                    f"⚠ 上一轮 AI 请求超过 {_BUSY_TIMEOUT}s 未响应，已自动恢复，请重新发送"
                )
            else:
                self.window.set_status("AI 正在回复中，请稍候…")
                return

        ai = self.app.services.get("ai")
        if ai is None or not ai.available():
            messagebox.showinfo(
                "AI 未就绪", "请先在「API 设置」中填入 GLM API Key。", parent=self.root
            )
            return
        if self.pending:
            self.window.set_status(f"还有 {len(self.pending)} 个附件在解析中…")
            return

        raw = self.input.get("1.0", "end-1c").strip()
        text = "" if raw == PLACEHOLDER else raw

        msg = ChatMessage.from_attachments(text, self.attachments)
        # 强制中文回复：把指令塞进 context_blocks，provider 会把它拼到请求前面，
        # 但 UI 只显示 msg.text 不会暴露这条系统指令。
        msg.context_blocks.insert(
            0, "请用中文回答我所有问题。如果我的输入是英文，请先翻译成中文再回答。"
        )

        # 可选上下文（显式勾选，绝不偷偷夹带）
        if self.var_use_text.get() and self.window.last_text.strip():
            msg.attachments_text.append(f"【识别原文】\n{self.window.last_text}")
        if self.var_use_shot.get() and self.window.last_image is not None:
            if all(img is not self.window.last_image for img in msg.images):
                msg.images.append(self.window.last_image)

        if not text and not msg.images and not msg.attachments_text:
            self.window.set_status("先输入问题，或添加附件")
            return

        self.input.delete("1.0", tk.END)
        self.input.insert("1.0", PLACEHOLDER)
        self.input.config(fg=theme.TEXT_HINT)

        self.add_bubble("user", self._describe_sent(text, msg))
        self._show_thinking()
        self._busy.set()
        self._busy_since = time.time()
        theme.set_button_enabled(self.btn_send, False)
        self.window.set_status("AI 思考中…")

        self._gen += 1
        gen = self._gen

        def _done(reply: str):
            self.ui.post(self._on_reply, gen, reply)

        def _err(e):
            self.ui.post(self._on_reply_error, gen, str(e))

        self.app.pipeline.run_async(
            self.app.pipeline.chat, msg, on_done=_done, on_error=_err
        )

    def _describe_sent(self, text: str, msg: ChatMessage) -> str:
        bits = []
        if msg.images:
            bits.append(f"图片 x{len(msg.images)}")
        names = [a.display_name for a in self.attachments if not a.is_image]
        bits.extend(names)
        if self.var_use_text.get() and self.window.last_text.strip():
            bits.append("识别原文")
        tail = ("\n📎 " + "；".join(bits)) if bits else ""
        return (text or "（请分析以下内容）") + tail

    def _alive(self) -> bool:
        """面板控件是否仍存活（reload_ui 重建主窗口后旧实例已销毁）。"""
        try:
            return bool(self.frame.winfo_exists())
        except Exception:
            return False

    def _finish(self) -> None:
        self._busy.clear()
        theme.set_button_enabled(self.btn_send, True)
        self._hide_thinking()

    def _on_reply(self, gen: int, reply: str) -> None:
        if not self._alive():
            return  # 面板已被重建/销毁，静默丢弃在途 AI 回复
        if gen != self._gen:  # 已被「清空对话」或新消息作废
            self._finish()
            return
        # 先隐藏"思考中"，但保持 _busy（防止打字机期间用户发新消息）
        self._hide_thinking()
        # 用流式打字机效果逐字输出（_stream_tick 完成时才 _finish）
        self._stream_start(reply)
        self.attachments.clear()  # 附件一轮一用，避免下轮重复上传
        self._render_chips()

    def _stream_start(self, full_text: str) -> None:
        """创建空的 assistant 气泡，启动逐字打字机效果。

        安全规则：
        1. 启动新流式前必须 _stream_stop() 取消旧任务；
        2. 记录 gen，_stream_tick 中校验 gen 是否仍有效。
        """
        self._stream_stop()  # 取消任何在途的旧流式任务
        colors = theme.BUBBLE.get("assistant", theme.BUBBLE["system"])
        outer = tk.Frame(self.inner, bg=theme.CANVAS_BG)
        outer.pack(fill=tk.X, pady=3, padx=4)
        row = tk.Frame(outer, bg=theme.CANVAS_BG)
        row.pack(anchor=tk.W, fill=tk.X)
        tk.Label(
            row,
            text="🤖",
            font=("Microsoft YaHei", 13),
            bg=theme.CANVAS_BG,
            fg=theme.ACCENT,
        ).pack(side=tk.LEFT, padx=(0, 4), anchor=tk.N)
        border = tk.Frame(row, bg=colors["border"], bd=0)
        border.pack(side=tk.LEFT, anchor=tk.W, padx=4, fill=tk.X, expand=True)
        txt = tk.Text(
            border,
            wrap=tk.WORD,
            font=theme.UI_FONT,
            bg=colors["bg"],
            fg=colors["fg"],
            padx=theme.BUBBLE_PAD,
            pady=theme.BUBBLE_PAD,
            bd=0,
            highlightthickness=0,
            relief=tk.FLAT,
            takefocus=1,
        )

        # 只读绑定
        def _ro(e):
            if e.state & 0x4:
                return
            if e.keysym in (
                "Left",
                "Right",
                "Up",
                "Down",
                "Home",
                "End",
                "Prior",
                "Next",
            ):
                return
            return "break"

        txt.bind("<KeyPress>", _ro)
        txt.bind("<Control-a>", lambda e: (self._select_all_text(txt), "break")[1])
        txt.pack(fill=tk.X, expand=True)
        txt.bind("<Button-3>", lambda e, t=full_text: self._copy_text(t))
        # 记录
        self.rows.append(outer)
        self.bubbles.append(border)
        self._stream_widget = txt
        self._stream_idx = 0
        self._stream_text = full_text
        self._stream_gen = self._gen  # 绑定到当前 gen
        self._stream_after = self.root.after(8, self._stream_tick)
        self._scroll_bottom()

    def _stream_tick(self) -> None:
        """打字机逐字追加。每次执行前校验 gen 是否仍有效。"""
        if self._stream_widget is None or not self._alive():
            return
        # gen 已变更（清空对话 / 新消息）→ 丢弃旧流式任务
        if self._stream_gen != self._gen:
            self._stream_stop()
            return
        txt = self._stream_widget
        full = self._stream_text
        idx = self._stream_idx
        if idx >= len(full):
            # 全部输出完毕，做最终渲染和清理
            txt.delete("1.0", tk.END)
            self._render_markdown(txt, full)
            txt.update_idletasks()
            try:
                h = txt.count("1.0", "end-1c", "displaylines")
                n = h[0] if isinstance(h, tuple) else h
                txt.config(height=max(2, int(n)))
            except Exception:
                pass
            self._stream_widget = None
            self._stream_after = None
            self._finish()  # 释放 _busy + 恢复发送按钮
            self.window.set_status(f"✓ AI 已回复（{len(full)} 字）")
            return
        # 每次追加 3 个字符（加速感知）
        chunk = full[idx : idx + 3]
        txt.insert(tk.END, chunk)
        self._stream_idx = idx + 3
        # 自动滚动
        self._scroll_bottom()
        self._stream_after = self.root.after(8, self._stream_tick)

    def _on_reply_error(self, gen: int, err: str) -> None:
        if not self._alive():
            return  # 面板已被重建/销毁，静默丢弃在途错误回调
        self._finish()
        if gen != self._gen:
            return
        low = err.lower()
        if "429" in low or "concurrency" in low:
            err = "AI 并发超限，请稍后重试。"
        elif "网络不可达" in err:
            err += "\n（检查代理 / 防火墙，或改用离线的 argos 翻译）"
        self.add_bubble("system", f"出错了：{err}")
        # 强制画布滚到底（让错误气泡可见）
        try:
            self.canvas.update_idletasks()
            self.canvas.yview_moveto(1.0)
        except Exception:
            pass
        self.window.set_status(f"⚠ AI 出错：{err.splitlines()[0][:60]}")

    # ==================================================================
    def _stream_stop(self) -> None:
        """取消在途打字机效果，防止内存泄漏。"""
        if self._stream_after is not None:
            try:
                self.root.after_cancel(self._stream_after)
            except Exception:
                pass
            self._stream_after = None
        self._stream_widget = None

    def clear_history(self) -> None:
        self._stream_stop()
        self._gen += 1  # 作废在途请求
        self._hide_thinking()
        ai = self.app.services.get("ai")
        if ai is not None and hasattr(ai, "clear_history"):
            try:
                ai.clear_history()
            except Exception:
                pass
        for row in self.rows:
            try:
                row.destroy()
            except Exception:
                pass
        self.rows.clear()
        self.bubbles.clear()
        self.attachments.clear()
        self.pending.clear()
        self._render_chips()
        try:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
        except Exception:
            pass
        self._busy.clear()
        theme.set_button_enabled(self.btn_send, True)
        self.window.set_status("对话历史已清空")
