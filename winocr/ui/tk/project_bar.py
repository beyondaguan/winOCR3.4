# -*- coding: utf-8 -*-
"""项目栏相关 UI：流式换行容器 + 项目标签页栏 + 新建/管理项目对话框。

本模块只做表现层，所有状态变更都转交给 ``App.projects``（ProjectManager）：
  - FlowFrame：整组换行的流式布局，解决 Tk pack 不换行导致窄窗按钮被推出可视区；
  - ProjectTabBar：顶部标签栏，点标签切换、点 × 关闭（默认不可关）、「+」新建；
  - open_manage_projects：独立入口，列出全部项目（含已关），重开/改名/设译向/真删；
  - open_new_project_dialog：新建项目（名 + 可选译向）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import theme
from .dialogs import _center, _ScrollableFrame


# ======================================================================
# 流式换行容器：把多个「组」整组换行（组不被拆散）
# ======================================================================
class FlowFrame(tk.Frame):
    """整组换行的流式布局容器。

    把多个「组」(子 Frame) 交给它，监听自身 ``<Configure>``，按当前可用宽度
    把放不下的组整体换到下一行（组内按钮不拆散）。这解决了 Tk ``pack`` 不换行、
    窗口一窄右侧按钮就被推出可视区的问题。

    用法::

        f = FlowFrame(parent); f.pack(fill=tk.X)
        g = ttk.Frame(f)                 # 一个组
        ttk.Button(g, ...).pack(side=tk.LEFT)   # 组内按钮用 pack(side=LEFT)
        f.add_group(g)
    """

    def __init__(self, master, padx: int = 4, pady: int = 2, **kw):
        super().__init__(master, **kw)
        self._padx = padx
        self._pady = pady
        self._groups = []
        self.bind("<Configure>", lambda e: self.relayout())

    def add_group(self, frame: tk.Widget) -> tk.Widget:
        """登记一个「组」（其内部按钮用 ``pack(side=LEFT)`` 排列）。"""
        self._groups.append(frame)
        return frame

    def relayout(self) -> None:
        """按当前宽度把各组摆到合适行（组整体不拆，放不下才换下一行）。"""
        if not self._groups:
            return
        try:
            avail = self.winfo_width()
            if avail <= 1:                      # 尚未真实映射，等下一次 Configure
                return
            self.update_idletasks()
            x = self._padx
            y = self._pady
            row_h = 0
            for g in self._groups:
                g.update_idletasks()
                gw = g.winfo_reqwidth()
                gh = g.winfo_reqheight()
                row_h = max(row_h, gh)
                if x + gw > avail and x > self._padx:
                    # 当前行放不下 → 换行
                    x = self._padx
                    y += row_h + self._pady
                    row_h = gh
                g.place(x=x, y=y)
                x += gw + self._padx
            total_h = y + row_h + self._pady
            if abs(self.winfo_height() - total_h) >= 1:
                self.config(height=total_h)
        except Exception:
            pass


# ======================================================================
# 项目标签页栏
# ======================================================================
class ProjectTabBar:
    """顶部项目标签页栏（自绘 Label 标签 + × 关闭）。

    点击标签 → 切换项目；点 × → 关闭标签（仅隐藏，数据保留，默认项目无 ×）。
    「+」→ 新建项目对话框。刷新由 MainWindow 在 ``on_change`` 回调里触发。
    """

    def __init__(self, master, window):
        self.window = window
        self.app = window.app
        self.frame = ttk.Frame(master, padding=(6, 4))
        self.refresh()

    def refresh(self) -> None:
        for w in list(self.frame.children.values()):
            w.destroy()
        self._tabs = {}
        pm = self.app.projects
        cur = pm.current_id
        for p in pm.open_projects():
            is_cur = p.id == cur
            bg = theme.ACCENT if is_cur else theme.CANVAS_BG
            fg = "white" if is_cur else theme.TEXT_MAIN
            # 用 Frame 做容器，文字和 × 并排，避免 Label 内嵌子部件导致文字被压
            tab = tk.Frame(
                self.frame, bg=bg, cursor="hand2",
                highlightbackground=theme.BORDER, highlightthickness=1)
            inner = tk.Frame(tab, bg=bg)
            inner.pack(padx=12, pady=4)
            name_lbl = tk.Label(
                inner, text=p.name, font=theme.UI_FONT,
                bg=bg, fg=fg, cursor="hand2")
            name_lbl.pack(side=tk.LEFT)
            if p.id != "default":
                x = tk.Label(
                    inner, text="✕", font=theme.UI_FONT,
                    bg=bg, fg=fg, cursor="hand2", padx=6)
                x.pack(side=tk.LEFT)
                x.bind("<Button-1>", lambda e, pid=p.id: self._close(pid))
            # 整体点击 = 切换；点 × 已被上面单独绑定冒泡阻断
            for w in (tab, inner, name_lbl):
                w.bind("<Button-1>", lambda e, pid=p.id: self._switch(pid))
            tab.pack(side=tk.LEFT, padx=3, pady=2)
            self._tabs[p.id] = tab
        add = ttk.Button(
            self.frame, text="+", padding=(10, 4),
            command=lambda: open_new_project_dialog(self.window))
        add.pack(side=tk.LEFT, padx=3, pady=2)

    def _switch(self, pid: str) -> None:
        try:
            self.app.projects.switch(pid)
        except Exception as e:
            self.window.set_status(f"切换项目失败: {e}")

    def _close(self, pid: str) -> None:
        try:
            self.app.projects.close_tab(pid)
        except Exception as e:
            self.window.set_status(f"关闭标签失败: {e}")


# ======================================================================
# 新建项目对话框
# ======================================================================
def open_new_project_dialog(window) -> None:
    """新建项目：填名称 + 可选译向（留空 = 继承全局），创建后立即切换。"""
    app = window.app
    root = window.root

    win = tk.Toplevel(root)
    win.title("新建项目")
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()

    body = ttk.Frame(win, padding=14)
    body.pack(fill=tk.BOTH, expand=True)

    ttk.Label(body, text="项目名称").grid(row=0, column=0, columnspan=2,
                                         sticky=tk.W, pady=(0, 4))
    name_var = tk.StringVar()
    name_ent = ttk.Entry(body, textvariable=name_var, width=24)
    name_ent.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
    name_ent.focus_set()

    from ...core.types import Lang
    lang_codes = [l.value for l in Lang]
    ttk.Label(body, text="翻译目标（可选，留空 = 继承全局设置）").grid(
        row=2, column=0, columnspan=2, sticky=tk.W, pady=(0, 4))
    tgt_var = tk.StringVar(value="（继承全局）")
    tgt_combo = ttk.Combobox(
        body, textvariable=tgt_var, state="readonly", width=24,
        values=["（继承全局）"] + [f"{c} — {Lang.label(c)}" for c in lang_codes])
    tgt_combo.current(0)
    tgt_combo.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 12))

    def _ok():
        name = name_var.get().strip()
        tgt_raw = tgt_var.get().strip()
        target = ""
        if tgt_raw and tgt_raw != "（继承全局）":
            target = tgt_raw.split(" — ")[0].strip()
        try:
            proj = app.projects.add(name, target)
        except Exception as e:
            messagebox.showwarning("新建失败", str(e), parent=win)
            return
        win.destroy()
        try:
            window.set_status(f"已新建项目「{proj.name}」并切换")
        except Exception:
            pass

    bar = ttk.Frame(win, padding=(14, 0, 14, 14))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "创建", _ok).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)

    name_ent.bind("<Return>", lambda e: _ok())
    win.bind("<Escape>", lambda e: win.destroy())
    _center(win, root)


# ======================================================================
# 管理项目对话框（真删入口）
# ======================================================================
def _pick_target(window, current: str) -> str:
    """小窗选译向，返回 lang code 或 ''（继承全局）。"""
    from ...core.types import Lang
    lang_codes = [l.value for l in Lang]

    win = tk.Toplevel(window.root)
    win.title("设置译向")
    win.resizable(False, False)
    win.transient(window.root)
    win.grab_set()

    ttk.Label(win, text="翻译目标（可选，留空 = 继承全局）",
              padding=(12, 8)).pack(anchor=tk.W)
    opts = ["（继承全局）"] + [f"{c} — {Lang.label(c)}" for c in lang_codes]
    var = tk.StringVar()
    if current and current in lang_codes:
        var.set(f"{current} — {Lang.label(current)}")
    else:
        var.set("（继承全局）")
    combo = ttk.Combobox(win, textvariable=var, state="readonly", width=26,
                         values=opts)
    combo.pack(padx=12, pady=(0, 10))

    res = {"code": None}

    def _ok():
        val = var.get().strip()
        res["code"] = "" if val == "（继承全局）" else val.split(" — ")[0].strip()
        win.destroy()

    bar = ttk.Frame(win, padding=(12, 0, 12, 12))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "确定", _ok).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)
    _center(win, window.root)
    win.wait_window(win)
    return res["code"] or ""


def open_manage_projects(window) -> None:
    """管理项目：列出全部（含已关），可重开 / 改名 / 设译向 / 真删。

    默认项目不可删、不可关；关闭标签仅隐藏（数据保留），真删在此处进行。
    """
    app = window.app
    pm = app.projects
    root = window.root

    win = tk.Toplevel(root)
    win.title("管理项目")
    win.geometry("500x440")
    win.minsize(420, 320)
    win.transient(root)

    top = ttk.Frame(win, padding=(10, 8))
    top.pack(fill=tk.X)
    ttk.Button(top, text="＋ 新建项目",
               command=lambda: open_new_project_dialog(window)).pack(side=tk.LEFT)
    ttk.Label(top, text="关闭标签仅隐藏（数据保留）；彻底删除在右侧「删除」",
              foreground=theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(10, 0))

    sf = _ScrollableFrame(win)
    sf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    def _rebuild():
        for w in list(sf.body.winfo_children()):
            w.destroy()
        cur = pm.current_id
        for p in pm.config.projects:
            row = ttk.Frame(sf.body, padding=(2, 4))
            row.pack(fill=tk.X, pady=2)
            name_text = p.name + ("（当前）" if p.id == cur else "")
            ttk.Label(row, text=name_text,
                      font=theme.UI_FONT_BOLD if p.id == cur else theme.UI_FONT
                      ).pack(side=tk.LEFT)
            ttk.Label(row, text="[打开]" if p.open else "[已关闭]",
                      foreground=theme.TEXT_MUTED, width=8
                      ).pack(side=tk.LEFT, padx=(6, 0))
            tgt = p.translate_target or "继承全局"
            ttk.Label(row, text=f"译向:{tgt}", foreground=theme.TEXT_MUTED,
                      width=16).pack(side=tk.LEFT, padx=(4, 0))

            if not p.open:
                ttk.Button(row, text="重开", width=5,
                           command=lambda pid=p.id: (pm.reopen(pid), _rebuild())
                           ).pack(side=tk.RIGHT, padx=2)
            if p.id != "default":
                ttk.Button(row, text="改名", width=5,
                           command=lambda pid=p.id: _rename(pid)).pack(side=tk.RIGHT, padx=2)
                ttk.Button(row, text="设译向", width=7,
                           command=lambda pid=p.id: _set_target(pid)).pack(side=tk.RIGHT, padx=2)
                ttk.Button(row, text="删除", width=5,
                           command=lambda pid=p.id: _delete(pid)).pack(side=tk.RIGHT, padx=2)
            else:
                ttk.Label(row, text="默认项目", foreground=theme.TEXT_MUTED
                          ).pack(side=tk.RIGHT, padx=4)

    def _rename(pid):
        p = pm._by_id(pid)
        if p is None:
            return
        new = simpledialog.askstring("改名", "输入新名称：",
                                     initialvalue=p.name, parent=win)
        if new is None:
            return
        pm.rename(pid, new)
        _rebuild()

    def _set_target(pid):
        p = pm._by_id(pid)
        if p is None:
            return
        code = _pick_target(window, p.translate_target)
        pm.set_target(pid, code)
        _rebuild()

    def _delete(pid):
        p = pm._by_id(pid)
        if p is None:
            return
        if not messagebox.askyesno(
                "删除项目",
                f"确定彻底删除「{p.name}」吗？\n"
                "将移除其知识库记录、翻译历史与对话历史（不可恢复）。",
                parent=win):
            return
        pm.delete(pid)
        _rebuild()

    _rebuild()
    _center(win, root)
