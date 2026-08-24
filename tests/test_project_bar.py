# -*- coding: utf-8 -*-
"""项目栏 / 流式换行 的 UI 契约测试。

不进人机交互，只验证「能不能建起来、换行与标签栏契约对不对」。
无图形环境（CI）时自动跳过。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

try:
    from winocr.ui.tk import TkUi
    from winocr.ui.tk.main_window import MainWindow
except Exception:  # pragma: no cover
    TkUi = MainWindow = None

pytestmark = pytest.mark.skipif(
    TkUi is None or MainWindow is None, reason="UI 模块不可用")

from conftest import display_available

pytestmark = pytest.mark.skipif(not display_available(), reason="无图形环境")


def _make_ui():
    from winocr.core import App
    app = App().build()
    ui = TkUi()
    ui.bind(app)
    ui.root = tk.Tk()
    ui.root.withdraw()
    ui._setup_style()
    ui.window = MainWindow(ui)
    ui._wire_events()
    return app, ui


def _tab_labels(w):
    """数标签栏里的项目标签数（每个标签现在是 Frame 容器 → 内嵌 Frame → 含名称 Label）。"""
    bar = w.project_tab_bar.frame
    tabs = []
    for c in bar.children.values():
        if isinstance(c, ttk.Button):
            continue
        if not isinstance(c, tk.Frame):
            continue
        # 递归找名字 Label（tab→inner→name_lbl/✕）
        def _walk(node):
            yield node
            for ch in node.children.values():
                yield from _walk(ch)
        has_name = any(
            isinstance(n, tk.Label) and n.cget("text") not in ("✕",)
            for n in _walk(c)
        )
        if has_name:
            tabs.append(c)
    return tabs


def test_project_bar_built_and_reflects_open_projects():
    app, ui = _make_ui()
    try:
        w = ui.window
        from winocr.ui.tk.project_bar import FlowFrame
        assert isinstance(w.merged_band, FlowFrame)
        assert hasattr(w, "project_tab_bar")
        assert hasattr(w, "simple_flow") and hasattr(w, "advanced_flow")
        # 标签数 == 打开项目数（默认仅 1 个）
        assert len(_tab_labels(w)) == len(app.projects.open_projects())
        # 「+」新建按钮存在
        adds = [c for c in w.project_tab_bar.frame.children.values()
                if isinstance(c, ttk.Button) and c.cget("text") == "+"]
        assert adds, "缺少 + 新建按钮"
    finally:
        ui.root.destroy()


def test_merged_band_has_manage_projects_and_snap():
    app, ui = _make_ui()
    try:
        from winocr.ui.tk import project_bar
        # 合并窄带里的「管理项目」按钮 -> 触发对话框构建不抛异常
        project_bar.open_manage_projects(ui.window)
        tops = [c for c in ui.root.winfo_children() if isinstance(c, tk.Toplevel)]
        assert tops, "管理项目对话框未弹出"
        for t in tops:
            t.grab_release()
            t.destroy()
    finally:
        ui.root.destroy()


def test_add_and_close_tab_refreshes_bar(monkeypatch):
    app, ui = _make_ui()
    try:
        # 避免把测试项目写进用户真实 config.toml
        monkeypatch.setattr(app.config, "save", lambda: None)
        w = ui.window
        before = len(app.projects.open_projects())
        proj = app.projects.add("病历编码")
        assert app.projects.current_id == proj.id
        w.project_tab_bar.refresh()
        assert len(_tab_labels(w)) == before + 1

        # 关闭（仅隐藏，数据不删）
        app.projects.close_tab(proj.id)
        assert proj.id not in [p.id for p in app.projects.open_projects()]
        w.project_tab_bar.refresh()
        assert len(_tab_labels(w)) == before

        # 默认项目不可关
        app.projects.close_tab("default")
        assert "default" in [p.id for p in app.projects.open_projects()]
    finally:
        ui.root.destroy()


def test_flowframe_wraps_when_narrow():
    root = tk.Tk()
    root.deiconify()          # 需真实窗口（几何才会被 Tk 计算/回报）
    try:
        from winocr.ui.tk.project_bar import FlowFrame
        ff = FlowFrame(root)
        ff.pack(fill=tk.X)
        for i in range(4):
            g = tk.Frame(ff)
            for j in range(3):
                tk.Button(g, text=f"b{i}{j}").pack(side=tk.LEFT)
            ff.add_group(g)
        root.update_idletasks()

        # 用固定组尺寸，避免依赖真实字体/布局带来的抖动
        GW, GH = 80, 24
        for g in ff._groups:
            g.winfo_reqwidth = lambda: GW
            g.winfo_reqheight = lambda: GH

        # 宽窗：4 个组一行容纳
        ff.winfo_width = lambda: 600
        ff.relayout()
        single_row_h = ff.winfo_height()
        assert single_row_h <= GH + 2 * ff._pady + 1

        # 窄窗：每组宽 80 + padx，放不下两个 → 换行成多行
        ff.winfo_width = lambda: 90
        ff.relayout()
        narrow_h = ff.winfo_height()
        assert narrow_h > single_row_h, "窄窗未触发整组换行"
        # 所有组都被 place（x >= 0）
        assert all(g.winfo_x() >= 0 for g in ff._groups)
    finally:
        root.destroy()
