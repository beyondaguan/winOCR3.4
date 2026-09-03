# -*- coding: utf-8 -*-
"""热键设置对话框。

从 dialogs_settings.py 拆分而来。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from ...services.hotkey import ACTIONS
from .dialogs_common import (
    _modal, _center, _record_key, _hotkey_status,
)


def open_hotkey_settings(window) -> None:
    app = window.app
    root = window.root
    cfg = app.config.hotkey

    win = _modal(root, "热键设置", "460x360")
    ttk.Label(win, text="点进输入框后直接按组合键即可录制", padding=(12, 10),
              foreground=theme.TEXT_MUTED).pack(anchor=tk.W)

    body = ttk.Frame(win, padding=(12, 0))
    body.pack(fill=tk.BOTH, expand=True)

    entries = {}
    for i, (action, label) in enumerate(ACTIONS.items()):
        ttk.Label(body, text=label + "：").grid(row=i, column=0, sticky=tk.W, pady=5)
        # 初始显示「实际生效的组合键」：用户覆盖 > 当前已注册（含胶囊默认）> 空，
        # 避免用户看到空白却不知道 ctrl+shift+a 正在生效（旧实现用 getattr(cfg,action)
        # 取到空串，既是显示 bug，也导致录制结果写不进 overrides 而永不生效）。
        if action == "quit":
            initial = cfg.quit
        else:
            ov = cfg.overrides or {}
            if action in ov and ov[action]:
                initial = ov[action]
            elif app.hotkeys is not None and action in app.hotkeys.registered:
                initial = app.hotkeys.registered[action]
            else:
                initial = ""
        var = tk.StringVar(value=initial)
        ent = ttk.Entry(body, textvariable=var, width=26)
        ent.grid(row=i, column=1, sticky=tk.W, pady=5, padx=(6, 6))
        ent.bind("<KeyPress>", lambda e, v=var: _record_key(e, v))
        ttk.Button(body, text="清除", width=5,
                   command=lambda v=var: v.set("")).grid(row=i, column=2)
        entries[action] = var

    enabled = tk.BooleanVar(value=cfg.enabled)
    ttk.Checkbutton(body, text="启用全局热键（关闭后仅窗口内快捷键可用）",
                    variable=enabled).grid(row=len(ACTIONS), column=0, columnspan=3,
                                           sticky=tk.W, pady=(10, 0))

    status = ttk.Label(win, text=_hotkey_status(app), foreground=theme.TEXT_MUTED,
                       padding=(12, 4), wraplength=430, justify=tk.LEFT)
    status.pack(fill=tk.X)

    def _save():
        # 修复：quit 是独立字段；其余动作必须写进 cfg.overrides，否则 reload() 永远
        # 回退胶囊默认（ctrl+shift+a），新录的 alt+q 不生效、且 setattr 的临时属性
        # 不被序列化 → 重启即丢失。清空某动作 = 从 overrides 移除，恢复胶囊默认。
        quit_combo = entries["quit"].get().strip().lower()
        seen = {}
        if quit_combo:
            seen[quit_combo] = "quit"
        overrides = dict(cfg.overrides or {})
        for action, var in entries.items():
            if action == "quit":
                continue
            combo = var.get().strip().lower()
            if not combo:
                overrides.pop(action, None)
                continue
            if combo in seen:
                messagebox.showwarning(
                    "热键冲突",
                    f"「{ACTIONS[action]}」与「{ACTIONS[seen[combo]]}」都用了 {combo}",
                    parent=win)
                return
            seen[combo] = action
            overrides[action] = combo
        cfg.overrides = overrides
        cfg.quit = quit_combo
        cfg.enabled = enabled.get()
        app.apply_config()                       # 立即落盘（含 overrides）
        ok = app.hotkeys.reload() if app.hotkeys else False
        summary = app.hotkeys.summary() if app.hotkeys else "不可用"
        window.set_status(("热键已更新 — " if ok else "热键已保存（") +
                          summary + ("" if ok else "）"))
        win.destroy()

    def _restore():
        from ...core.config import HotkeyConfig
        d = HotkeyConfig()
        for action, var in entries.items():
            # mask_translate 等动作来自胶囊默认（非 HotkeyConfig 字段），取不到用空串
            var.set(getattr(d, action, ""))
        enabled.set(d.enabled)

    bar = ttk.Frame(win, padding=(12, 8))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "保存并生效", _save).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)
    ttk.Button(bar, text="恢复默认", command=_restore).pack(side=tk.LEFT)

    _center(win, root)
