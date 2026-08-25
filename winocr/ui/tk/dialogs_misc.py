# -*- coding: utf-8 -*-
"""其他对话框：关于 / 插件 / 首次运行向导。

从 dialogs.py 拆分而来。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from ...services.hotkey import ACTIONS
from .dialogs_common import _center, _modal, _ScrollableFrame

def open_about(window) -> None:
    from ...version import __version__
    app = window.app
    win = _modal(window.root, "关于 WinOCR", "460x430")

    head = tk.Frame(win, bg=theme.ACCENT, height=70)
    head.pack(fill=tk.X)
    head.pack_propagate(False)
    tk.Label(head, text=f"WinOCR {__version__}", font=("Microsoft YaHei", 15, "bold"),
             bg=theme.ACCENT, fg="white").pack(anchor=tk.W, padx=14, pady=(12, 0))
    tk.Label(head, text="插件化架构 · 截图识字 · 离线翻译 · AI 解读",
             font=theme.UI_FONT_SMALL, bg=theme.ACCENT, fg=theme.ACCENT_SUBTITLE
             ).pack(anchor=tk.W, padx=14)

    body = ttk.Frame(win, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    txt = tk.Text(body, wrap=tk.WORD, font=theme.UI_FONT_SMALL, relief=tk.FLAT,
                  bg=theme.CANVAS_BG, height=14)
    txt.pack(fill=tk.BOTH, expand=True)

    lines = ["已装载的插件：\n"]
    axis_name = {"ocr": "OCR 引擎", "translate": "翻译引擎", "ai": "AI 提供方",
                 "capture": "捕获源", "attach": "附件解析", "persistence": "持久化"}
    for axis, items in app.describe().items():
        names = []
        for it in items:
            flag = {True: "✓", False: "✗", None: "·"}[it["available"]]
            names.append(f"{flag} {it['name']}")
        lines.append(f"  {axis_name.get(axis, axis)}: " + "  ".join(names))
    lines.append("\n✓ 可用   ✗ 缺依赖或未配置   · 未实例化")
    lines.append("\n扩展方式：往 services/<轴>/ 或用户插件目录丢一个 .py 文件即可，"
                 "程序本体无需改动。")
    txt.insert("1.0", "\n".join(lines))
    txt.configure(state=tk.DISABLED)

    ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 12))
    _center(win, window.root)


def open_plugins(window) -> None:
    """插件管理：按轴列出已发现插件，勾选 = 启用，取消 = 禁用（黑名单）。

    黑名单写入 AppConfig.plugin.blacklist，下次启动装配时被禁用的插件
    直接跳过（App._discover 过滤）。引擎实例在启动时装配，改动需重启生效。
    """
    app = window.app
    cfg = app.config
    bl = set(getattr(cfg.plugin, "blacklist", None) or [])

    win = _modal(window.root, "插件管理", "540x480")
    ttk.Label(win,
              text="取消勾选 = 禁用该插件（写入黑名单，下次启动不装配）\n"
                   "引擎实例在启动时装配，改动需重启程序后完全生效。",
              foreground=theme.TEXT_MUTED, wraplength=500, justify=tk.LEFT,
              padding=(12, 10)).pack(anchor=tk.W)

    body = _ScrollableFrame(win)
    body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

    axis_name = {"ocr": "OCR 引擎", "translate": "翻译引擎", "ai": "AI 提供方",
                 "capture": "捕获源", "attach": "附件解析", "persistence": "持久化"}
    _STATUS = {True: "✓ 可用", False: "✗ 不可用", None: "· 未实例化"}

    vars_ = {}
    row = 0
    for axis, items in app.describe().items():
        ttk.Label(body.body, text=axis_name.get(axis, axis),
                  font=theme.UI_FONT_BOLD).grid(row=row, column=0, sticky=tk.W,
                                                pady=(10, 2))
        row += 1
        for it in items:
            name = it["name"]
            var = tk.BooleanVar(value=name not in bl)
            vars_[name] = var
            ttk.Checkbutton(
                body.body,
                text=f"{it['display']}（{name}）  [{_STATUS.get(it['available'], '·')}]",
                variable=var).grid(row=row, column=0, sticky=tk.W, padx=(16, 0), pady=1)
            row += 1

    def _save():
        new_bl = [n for n, v in vars_.items() if not v.get()]
        try:
            cfg.plugin.blacklist = new_bl
            app.config.save()
        except Exception as e:
            messagebox.showwarning("保存失败", f"{e}", parent=win)
            return
        messagebox.showinfo("插件管理",
                            "已保存。\n引擎实例在启动时装配，完全生效需重启程序。",
                            parent=win)
        win.destroy()

    bar = ttk.Frame(win, padding=(12, 0, 12, 12))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "保存", _save).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)
    _center(win, window.root)


def open_first_run(window) -> None:
    """首次运行向导：填 AI 提供方 + API Key 即可开始用。

    仅在 ``ai.api_key`` 为空且此前从未配置过时由启动流程调用；
    已配置过（即便 key 失效）不打扰。点「完成」保存配置并重载服务。
    """
    app = window.app
    cfg = app.config
    root = window.root

    win = _modal(root, "欢迎使用 WinOCR", "520x420")
    head = tk.Frame(win, bg=theme.ACCENT, height=76)
    head.pack(fill=tk.X)
    head.pack_propagate(False)
    tk.Label(head, text="WinOCR — 截图识字 · 翻译 · AI", font=("Microsoft YaHei", 14, "bold"),
             bg=theme.ACCENT, fg="white").pack(anchor=tk.W, padx=16, pady=(12, 0))
    tk.Label(head, text="三步完成初始配置，之后用热键随时截图识别",
             font=theme.UI_FONT_SMALL, bg=theme.ACCENT,
             fg=theme.ACCENT_SUBTITLE).pack(anchor=tk.W, padx=16)

    body = ttk.Frame(win, padding=16)
    body.pack(fill=tk.BOTH, expand=True)

    ttk.Label(body, text="1. 选择 AI 提供方（用于对话 / 解读，可留空稍后设置）",
              font=theme.UI_FONT_BOLD).pack(anchor=tk.W)
    _AI_PROVIDERS = {
        "glm": "智谱 GLM（文本 + 视觉）",
        "openai_compat": "OpenAI 兼容（SiliconFlow / DeepSeek / vLLM 等）",
    }
    provider = tk.StringVar(value="glm")
    ttk.Combobox(body, textvariable=provider, state="readonly", width=44,
                 values=[f"{k} — {v}" for k, v in _AI_PROVIDERS.items()]
                 ).pack(anchor=tk.W, pady=(2, 10))

    ttk.Label(body, text="2. 填写 API Key（可选，留空则仅用本地 OCR + 离线翻译）",
              font=theme.UI_FONT_BOLD).pack(anchor=tk.W)
    key_var = tk.StringVar(value="")
    e_key = ttk.Entry(body, textvariable=key_var, width=46, show="•")
    e_key.pack(anchor=tk.W, pady=(2, 2))
    show_key = tk.BooleanVar(value=False)
    ttk.Checkbutton(body, text="显示", variable=show_key,
                    command=lambda: e_key.config(show="" if show_key.get() else "•")
                    ).pack(anchor=tk.W)
    ttk.Label(body, text="Base URL / 模型留空 = 用官方默认（可在「API 设置」中随时改）",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL,
              wraplength=460, justify=tk.LEFT).pack(anchor=tk.W, pady=(6, 0))

    ttk.Separator(body, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)
    ttk.Label(body, text="3. 常用热键（可在「热键设置」中修改）",
              font=theme.UI_FONT_BOLD).pack(anchor=tk.W)
    hotkeys = []
    from ...services.hotkey import ACTIONS
    _HK_LABELS = {"snap_translate": "截图识别并翻译", "translate_text": "翻译选中文字",
                  "clipboard_extract": "识别剪贴板图片", "cycle_engine": "切换翻译引擎"}
    for action, label in ACTIONS.items():
        combo = cfg.hotkey.resolved(action, "")
        if combo:
            hotkeys.append(f"  {combo}  →  {_HK_LABELS.get(action, label)}")
    ttk.Label(body, text="\n".join(hotkeys) if hotkeys else "  （未配置）",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL,
              justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

    def _finish():
        key = key_var.get().strip()
        if key:
            cfg.ai.provider = provider.get().split(" — ")[0].strip()
            cfg.ai.api_key = key
            cfg.ai.base_url = ""          # 官方默认
            cfg.ai.text_model = ""
            cfg.ai.vision_model = ""
            try:
                app.apply_config()
            except Exception as e:
                messagebox.showwarning("保存失败", str(e), parent=win)
                return
            try:
                window.refresh_engine_label()
            except Exception:
                pass
        win.destroy()
        try:
            window.set_status("首次配置完成 — 按默认热键开始使用")
        except Exception:
            pass

    bar = ttk.Frame(win, padding=(16, 0, 16, 14))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "完成", _finish).pack(side=tk.RIGHT)
    ttk.Button(bar, text="暂不配置", command=win.destroy).pack(side=tk.RIGHT, padx=6)
    _center(win, root)


