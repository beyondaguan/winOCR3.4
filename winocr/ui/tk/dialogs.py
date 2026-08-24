# -*- coding: utf-8 -*-
"""设置对话框：热键 / API 与引擎 / 关于。

2.0 的两个设置窗口各自把值写回不同的 .py 文件（hotkey_settings.py、api_settings.py），
写法是「重新生成一份源码」——既不安全也容易写坏。
3.0 统一：改内存里的 AppConfig → app.apply_config() 一次性生效并落盘 TOML。

3.4（功能视角，无平台账号）：每个功能（AI 对话 / 大模型翻译 / 云端视觉 OCR）在自己的
设置页里直接持有完整的一套连接参数（地址 / 密钥 / 模型 / 采样 / 限流），不再有「平台账号」
中间层，也不再按连接名引用——三个功能各自独立成一套，互不串 Key、互不串地址。
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from ...services.hotkey import ACTIONS
from ...services.translate.glm import GlmEngine

_MOD_ORDER = ["ctrl", "alt", "shift", "win"]
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


# ======================================================================
# 热键设置
# ======================================================================
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
            var.set(getattr(d, action))
        enabled.set(d.enabled)

    bar = ttk.Frame(win, padding=(12, 8))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "保存并生效", _save).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)
    ttk.Button(bar, text="恢复默认", command=_restore).pack(side=tk.LEFT)

    _center(win, root)


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


# ======================================================================
# API 与引擎设置（连接抽象版）
# ======================================================================
def open_api_settings(window) -> None:
    """API 与引擎设置 — 功能视角（无平台账号层）。

    每个功能（AI 对话 / 大模型翻译 / 云端视觉 OCR）在自己的设置页里直接填
    一套完整连接参数（地址 / 密钥 / 模型 / 采样 / 限流），不再有「平台账号」
    中间层，也不再按连接名互相引用。
    """
    app = window.app
    root = window.root
    cfg = app.config

    win = _modal(root, "API 与引擎设置", "680x660")
    win.resizable(True, True)          # 窗口可拉大；内容超一屏时用页内滚动条
    win.minsize(560, 400)

    # 设置内搜索（P2-1）：输入关键词，自动跳到第一个匹配页签并提示命中数
    search_row = ttk.Frame(win, padding=(10, 8, 10, 0))
    search_row.pack(fill=tk.X)
    ttk.Label(search_row, text="搜索设置：").pack(side=tk.LEFT)
    search_var = tk.StringVar()
    ttk.Entry(search_row, textvariable=search_var, width=28
              ).pack(side=tk.LEFT, padx=(0, 6))
    search_hint = ttk.Label(search_row, text="", foreground=theme.TEXT_MUTED)
    search_hint.pack(side=tk.LEFT)

    nb = ttk.Notebook(win)
    nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    from tkinter import simpledialog

    # ---------------- 连接参数编辑辅助 ----------------
    def _lim_row(parent, r, label, var, note=""):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky=tk.W, pady=(4, 0))
        ttk.Entry(parent, textvariable=var, width=8).grid(row=r, column=1,
                                                          sticky=tk.W, padx=(6, 0))
        if note:
            ttk.Label(parent, text=note, foreground=theme.TEXT_MUTED,
                      font=theme.UI_FONT_SMALL).grid(row=r, column=2, sticky=tk.W,
                                                     padx=(10, 0))

    def _make_conn_editor(parent, obj, *, vision: bool = True) -> dict:
        """生成一组「地址 / 密钥 / 模型 / 采样 / 限流」控件，绑定到 obj 字段。

        返回 StringVar 字典，供 _save 写回。视觉相关字段（vision_*）仅当
        vision=True 时生成（AI 带图对话 / 云端 OCR 需要，翻译不需要）。
        """
        v = {
            "base_url": tk.StringVar(value=obj.base_url),
            "api_key": tk.StringVar(value=obj.api_key),
            "text_model": tk.StringVar(value=obj.text_model),
            "vision_model": tk.StringVar(value=obj.vision_model),
            "temperature": tk.StringVar(value=str(obj.temperature)),
            "top_p": tk.StringVar(value=str(obj.top_p)),
            "max_output_tokens": tk.StringVar(value=str(obj.max_output_tokens)),
            "max_context_tokens": tk.StringVar(value=str(obj.max_context_tokens)),
            "max_turns": tk.StringVar(value=str(obj.max_turns)),
            "timeout": tk.StringVar(value=str(obj.timeout)),
            "retry_attempts": tk.StringVar(value=str(obj.retry_attempts)),
            "retry_backoff": tk.StringVar(value=str(obj.retry_backoff)),
        }
        ttk.Label(parent, text="Base URL（留空 = 平台官方默认）"
                  ).grid(row=0, column=0, columnspan=4, sticky=tk.W)
        ttk.Entry(parent, textvariable=v["base_url"], width=52
                  ).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))
        ttk.Label(parent, text="例：https://open.bigmodel.cn/api/paas/v4/chat/completions\n"
                               "    https://api.siliconflow.cn/v1/chat/completions",
                  foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL, justify=tk.LEFT
                  ).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))

        ttk.Label(parent, text="API Key").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        e_key = ttk.Entry(parent, textvariable=v["api_key"], width=42, show="•")
        e_key.grid(row=4, column=0, columnspan=2, sticky=tk.W)
        show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="显示", variable=show_key,
                        command=lambda: e_key.config(show="" if show_key.get() else "•")
                        ).grid(row=4, column=2, sticky=tk.W, padx=(6, 0))

        ttk.Label(parent, text="文本模型（AI 对话 / 大模型翻译用）"
                  ).grid(row=5, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(parent, textvariable=v["text_model"], width=30
                  ).grid(row=6, column=0, sticky=tk.W)
        ttk.Label(parent, text="例：glm-4-flash / Qwen/Qwen2.5-Coder-7B-Instruct",
                  foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL
                  ).grid(row=6, column=1, columnspan=3, sticky=tk.W, padx=(8, 0))

        ttk.Label(parent, text="视觉模型（AI 带图对话 / 云端 OCR 用，留空跟随文本模型）"
                  ).grid(row=7, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(parent, textvariable=v["vision_model"], width=30
                  ).grid(row=8, column=0, sticky=tk.W)
        ttk.Label(parent, text="例：glm-4v-flash / GLM-4.1V-9B-Thinking",
                  foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL
                  ).grid(row=8, column=1, columnspan=3, sticky=tk.W, padx=(8, 0))

        lf_samp = ttk.LabelFrame(parent, text="采样参数", padding=8)
        lf_samp.grid(row=9, column=0, columnspan=4, sticky=tk.EW, pady=(10, 0))
        ttk.Label(lf_samp, text="Temperature").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(lf_samp, textvariable=v["temperature"], width=8
                  ).grid(row=0, column=1, sticky=tk.W, padx=(6, 16))
        ttk.Label(lf_samp, text="Top P").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(lf_samp, textvariable=v["top_p"], width=8
                  ).grid(row=0, column=3, sticky=tk.W, padx=(6, 0))
        if vision:
            ttk.Label(lf_samp, text="视觉 Temp（<0 不发送）").grid(row=1, column=0, sticky=tk.W)
            v["vision_temperature"] = tk.StringVar(value=str(obj.vision_temperature))
            ttk.Entry(lf_samp, textvariable=v["vision_temperature"], width=8
                      ).grid(row=1, column=1, sticky=tk.W, padx=(6, 16))
            ttk.Label(lf_samp, text="视觉 Top P（<0 不发送）").grid(row=1, column=2, sticky=tk.W)
            v["vision_top_p"] = tk.StringVar(value=str(obj.vision_top_p))
            ttk.Entry(lf_samp, textvariable=v["vision_top_p"], width=8
                      ).grid(row=1, column=3, sticky=tk.W, padx=(6, 0))
            ttk.Label(lf_samp, text="视觉最大输出 (token)").grid(row=2, column=0,
                                                                sticky=tk.W, pady=(6, 0))
            v["vision_max_output_tokens"] = tk.StringVar(
                value=str(obj.vision_max_output_tokens))
            ttk.Entry(lf_samp, textvariable=v["vision_max_output_tokens"], width=8
                      ).grid(row=2, column=1, sticky=tk.W, padx=(6, 16))

        lf_lim = ttk.LabelFrame(parent, text="限流 / 上下文保护", padding=8)
        lf_lim.grid(row=10, column=0, columnspan=4, sticky=tk.EW, pady=(8, 0))
        _lim_row(lf_lim, 0, "上下文窗口上限 (token)", v["max_context_tokens"],
                 "超长自动裁剪历史")
        _lim_row(lf_lim, 1, "每轮最大输出 (token)", v["max_output_tokens"])
        _lim_row(lf_lim, 2, "最大对话轮次", v["max_turns"], "超出只保留最近 N 轮")
        _lim_row(lf_lim, 3, "请求超时 (秒)", v["timeout"])
        _lim_row(lf_lim, 4, "429 重试次数", v["retry_attempts"])
        _lim_row(lf_lim, 5, "重试退避基数 (秒)", v["retry_backoff"], "指数增长")
        return v

    def _apply_conn_vars(obj, v: dict) -> None:
        """把 _make_conn_editor 返回的 StringVar 写回 obj（数字解析失败抛 ValueError）。"""
        obj.base_url = v["base_url"].get().strip()
        obj.api_key = v["api_key"].get().strip()
        obj.text_model = v["text_model"].get().strip()
        obj.vision_model = v["vision_model"].get().strip()
        obj.temperature = float(v["temperature"].get() or 0.7)
        obj.top_p = float(v["top_p"].get() or 0.9)
        obj.max_output_tokens = int(v["max_output_tokens"].get() or 0) or 2048
        obj.max_context_tokens = int(v["max_context_tokens"].get() or 0) or 32768
        obj.max_turns = int(v["max_turns"].get() or 0) or 12
        obj.timeout = int(v["timeout"].get() or 0) or 60
        obj.retry_attempts = int(v["retry_attempts"].get() or 0) or 3
        obj.retry_backoff = float(v["retry_backoff"].get() or 0) or 1.5
        if "vision_temperature" in v:
            obj.vision_temperature = float(v["vision_temperature"].get() or 0.0)
            obj.vision_top_p = float(v["vision_top_p"].get() or 0.0)
            obj.vision_max_output_tokens = int(v["vision_max_output_tokens"].get() or 0)

    # ================= 页签 1：AI 对话 =================
    p_ai = _ScrollableFrame(nb)
    nb.add(p_ai, text="AI 对话")
    ttk.Label(p_ai.body, text="本功能直接持有自己的连接参数（地址 / 密钥 / 模型 / 采样 / 限流），"
              "不引用其它功能。文本对话用「文本模型」，带图对话用「视觉模型」。",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL, wraplength=520,
              justify=tk.LEFT).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))
    ai_body = ttk.Frame(p_ai.body)
    ai_body.grid(row=1, column=0, columnspan=4, sticky=tk.EW)

    # AI 提供方选择（P2-2：解除「仅 glm」锁定，两实现共用同一套连接参数）
    ttk.Label(p_ai.body, text="AI 提供方").grid(row=2, column=0, sticky=tk.W)
    _AI_PROVIDERS = {
        "glm": "智谱 GLM（文本 + 视觉）",
        "openai_compat": "OpenAI 兼容（SiliconFlow / DeepSeek / vLLM 等）",
    }
    provider = tk.StringVar(value=cfg.ai.provider
                            if cfg.ai.provider in _AI_PROVIDERS else "glm")
    ttk.Combobox(p_ai.body, textvariable=provider, state="readonly", width=40,
                 values=[f"{k} — {v}" for k, v in _AI_PROVIDERS.items()]
                 ).grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))

    ai_test_lbl = ttk.Label(p_ai.body, text="", foreground=theme.TEXT_MUTED, wraplength=520,
                            justify=tk.LEFT)
    ai_test_lbl.grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
    ttk.Button(p_ai.body, text="测试文本对话", command=lambda: _test_ai(
        app, ai_v["api_key"].get(), ai_v["base_url"].get(),
        ai_v["text_model"].get(), ai_v["vision_model"].get(),
        ai_v["base_url"].get(), ai_v["api_key"].get(), "text", ai_test_lbl)
               ).grid(row=5, column=0, sticky=tk.W, pady=(4, 0))
    ttk.Button(p_ai.body, text="测试带图对话", command=lambda: _test_ai(
        app, ai_v["api_key"].get(), ai_v["base_url"].get(),
        ai_v["text_model"].get(), ai_v["vision_model"].get(),
        ai_v["base_url"].get(), ai_v["api_key"].get(), "vision", ai_test_lbl)
               ).grid(row=5, column=1, sticky=tk.W, pady=(4, 0))

    # ================= 页签 2：大模型翻译 =================
    p2 = _ScrollableFrame(nb)
    nb.add(p2, text="大模型翻译")

    disp = app.services.get("translate")
    engines = ["auto"] + list(disp.engines.keys()) if disp else ["auto"]
    labels = {"auto": "auto — 自动回退链"}
    if disp:
        for n in disp.engines:
            mark = "" if n in disp.available_engines() else "（不可用）"
            labels[n] = f"{n} — {disp.engine_display(n)}{mark}"

    ttk.Label(p2.body, text="翻译引擎").grid(row=0, column=0, sticky=tk.W)
    eng = tk.StringVar(value=labels.get(cfg.translate.engine, cfg.translate.engine))
    ttk.Combobox(p2.body, textvariable=eng, state="readonly", width=38,
                 values=[labels[n] for n in engines]).grid(row=1, column=0,
                                                           sticky=tk.W, pady=(2, 10))

    from ...core.types import Lang
    lang_codes = [l.value for l in Lang]
    ttk.Label(p2.body, text="默认译向").grid(row=2, column=0, sticky=tk.W)
    tgt = tk.StringVar(value=f"{cfg.translate.target} — {Lang.label(cfg.translate.target)}")
    ttk.Combobox(p2.body, textvariable=tgt, state="readonly", width=38,
                 values=[f"{c} — {Lang.label(c)}" for c in lang_codes]
                 ).grid(row=3, column=0, sticky=tk.W, pady=(2, 10))

    auto_t = tk.BooleanVar(value=cfg.translate.auto_translate)
    ttk.Checkbutton(p2.body, text="识别完成后自动翻译", variable=auto_t
                    ).grid(row=4, column=0, sticky=tk.W)

    offline = tk.BooleanVar(value=cfg.translate.offline_mode)
    ttk.Checkbutton(p2.body, text="离线护栏（离线模式下只走本地引擎，不发起网络请求）",
                    variable=offline).grid(row=5, column=0, sticky=tk.W)

    ttk.Label(p2.body, text="回退顺序（auto 模式下从左到右依次尝试）",
              foreground=theme.TEXT_MUTED).grid(row=6, column=0, sticky=tk.W, pady=(14, 2))
    order = tk.StringVar(value=", ".join(cfg.translate.fallback_order))
    ttk.Entry(p2.body, textvariable=order, width=42).grid(row=7, column=0, sticky=tk.W)
    ttk.Label(p2.body, text=f"可用引擎：{', '.join(disp.available_engines()) if disp else '无'}",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL
              ).grid(row=8, column=0, sticky=tk.W, pady=(6, 0))

    ttk.Separator(p2.body, orient=tk.HORIZONTAL).grid(row=9, column=0, sticky=tk.EW, pady=12)
    ttk.Label(p2.body, text="本功能直接用自己的一套连接参数（glm / SiliconFlow / 自建均可），"
                       "不再引用「平台账号」页。",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL, wraplength=520,
              justify=tk.LEFT).grid(row=10, column=0, columnspan=4, sticky=tk.W,
                                   pady=(0, 6))
    tr_body = ttk.Frame(p2.body)
    tr_body.grid(row=11, column=0, columnspan=4, sticky=tk.EW)
    tr_v = _make_conn_editor(tr_body, cfg.translate, vision=False)

    trans_test_lbl = ttk.Label(p2.body, text="", foreground=theme.TEXT_MUTED,
                               font=theme.UI_FONT_SMALL, wraplength=500, justify=tk.LEFT)
    trans_test_lbl.grid(row=12, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
    ttk.Button(p2.body, text="测试翻译连接",
               command=lambda: _test_translate(
                   app, tr_v["base_url"].get(), tr_v["text_model"].get(),
                   tr_v["api_key"].get(), trans_test_lbl)
               ).grid(row=13, column=0, sticky=tk.W, pady=(6, 0))

    eff_lbl = ttk.Label(p2.body, text="", foreground=theme.TEXT_MUTED,
                        font=theme.UI_FONT_SMALL, wraplength=500, justify=tk.LEFT)
    eff_lbl.grid(row=14, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))

    # ================= 页签 3：云端 OCR =================
    p3 = _ScrollableFrame(nb)
    nb.add(p3, text="云端 OCR")

    o = cfg.ocr
    ttk.Label(p3.body, text="OCR 引擎").grid(row=0, column=0, sticky=tk.W)
    ocr_eng = tk.StringVar(value=o.engine)
    ttk.Combobox(p3.body, textvariable=ocr_eng, state="readonly", width=38,
                 values=["rapidocr", "vision_ocr"]).grid(row=1, column=0,
                                                          sticky=tk.W, pady=(2, 10))

    ttk.Label(p3.body, text="本地 RapidOCR 设置",
              font=theme.UI_FONT_BOLD).grid(row=2, column=0, sticky=tk.W, pady=(4, 2))
    ttk.Label(p3.body, text="模型档位（越大越准也越慢）").grid(row=3, column=0, sticky=tk.W)
    mt = tk.StringVar(value=o.model_type)
    ttk.Combobox(p3.body, textvariable=mt, state="readonly", width=18,
                 values=["tiny", "small", "medium"]).grid(row=4, column=0,
                                                          sticky=tk.W, pady=(2, 6))
    pre = tk.BooleanVar(value=o.preprocess)
    ttk.Checkbutton(p3.body, text="智能预处理（小图放大 / 增强对比，代码截图自动保留原色）",
                    variable=pre).grid(row=5, column=0, sticky=tk.W)
    struct = tk.BooleanVar(value=o.structured)
    ttk.Checkbutton(p3.body, text="结构化输出（表格 / 版式结构化结果，供二次处理）",
                    variable=struct).grid(row=6, column=0, sticky=tk.W)
    auto_up = tk.BooleanVar(value=getattr(o, "auto_upgrade", True))
    ttk.Checkbutton(p3.body, text="低置信度自动升档重试（识别模糊时自动用更高精度档再试一次）",
                    variable=auto_up).grid(row=7, column=0, sticky=tk.W)

    # 各档位模型安装状态：未安装的档位标注出来，避免误以为用了该档
    tier_state = ttk.Label(p3.body, text="", foreground=theme.TEXT_MUTED,
                           font=theme.UI_FONT_SMALL)
    tier_state.grid(row=8, column=0, sticky=tk.W, pady=(0, 4))
    _ocr_svc = app.services.get("ocr")
    if _ocr_svc is not None and hasattr(_ocr_svc, "model_availability"):
        try:
            avail = _ocr_svc.model_availability()
            parts = [f"{t}={'✓' if ok else '✗未装'}" for t, ok in avail.items()]
            tier_state.config(text="本地模型：" + "  ".join(parts))
        except Exception:
            pass

    ttk.Separator(p3.body, orient=tk.HORIZONTAL).grid(row=9, column=0, sticky=tk.EW, pady=12)
    ttk.Label(p3.body, text="云端视觉 OCR（OpenAI 兼容视觉模型；留空地址/密钥 = 关闭，仅本地识别）",
              font=theme.UI_FONT_BOLD).grid(row=10, column=0, columnspan=4,
                                           sticky=tk.W, pady=(2, 4))
    ttk.Label(p3.body, text="本功能直接用自己的一套连接参数，不再引用「平台账号」页。",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL
              ).grid(row=11, column=0, columnspan=4, sticky=tk.W)
    ocr_body = ttk.Frame(p3.body)
    ocr_body.grid(row=12, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
    ocr_v = _make_conn_editor(ocr_body, cfg.ocr, vision=True)

    ocr_test_lbl = ttk.Label(p3.body, text="", foreground=theme.TEXT_MUTED,
                             font=theme.UI_FONT_SMALL, wraplength=500, justify=tk.LEFT)
    ocr_test_lbl.grid(row=13, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
    ttk.Button(p3.body, text="测试视觉 OCR 连接",
               command=lambda: _test_ocr(
                   app, ocr_v["base_url"].get(), ocr_v["vision_model"].get(),
                   ocr_v["api_key"].get(), ocr_test_lbl)
               ).grid(row=14, column=0, sticky=tk.W, pady=(6, 0))

    ocr_eff_lbl = ttk.Label(p3.body, text="", foreground=theme.TEXT_MUTED,
                            font=theme.UI_FONT_SMALL, wraplength=500, justify=tk.LEFT)
    ocr_eff_lbl.grid(row=15, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))

    def _refresh_ocr_eff(*_) -> None:
        has_key = bool(ocr_v["api_key"].get().strip())
        model = ocr_v["vision_model"].get().strip() or "（内置默认）"
        if ocr_eng.get() != "vision_ocr":
            ocr_eff_lbl.config(text="当前引擎为本地识别，未使用云端 OCR 参数。")
            return
        state = "已填写" if has_key else "未填写，云端 OCR 不可用"
        ocr_eff_lbl.config(
            text=f"当前生效：\n  视觉模型：{model}\n  API Key：{state}")
    ocr_eng.trace_add("write", _refresh_ocr_eff)
    _refresh_ocr_eff()

    ttk.Separator(p3.body, orient=tk.HORIZONTAL).grid(row=23, column=0, sticky=tk.EW, pady=12)
    from ...core.paths import config_path
    ttk.Label(p3.body, text=f"配置文件：{config_path()}", foreground=theme.TEXT_MUTED,
              font=theme.UI_FONT_SMALL, wraplength=500, justify=tk.LEFT
              ).grid(row=24, column=0, sticky=tk.W, pady=(14, 0))

    # ================= 页签 5：外观 / 界面 =================
    p4 = _ScrollableFrame(nb)
    nb.add(p4, text="外观")

    # 启动界面模式（simple / advanced）——与主题配色同页，方便整体调整外观
    ttk.Label(p4.body, text="启动界面模式").grid(row=0, column=4, sticky=tk.W, padx=(16, 0))
    mode = tk.StringVar(value=cfg.ui.mode)
    ttk.Combobox(p4.body, textvariable=mode, state="readonly", width=18,
                 values=["simple", "advanced"]).grid(row=1, column=4, sticky=tk.W,
                                                     pady=(2, 0))

    # 窗口尺寸：改 config 里 UiConfig.window_size（应用启动时读取），「记住当前」回填主窗大小
    ttk.Label(p4.body, text="窗口尺寸（宽x高）").grid(row=0, column=5, sticky=tk.W, padx=(16, 0))
    win_size = tk.StringVar(value=cfg.ui.window_size)
    ttk.Entry(p4.body, textvariable=win_size, width=11
              ).grid(row=1, column=5, sticky=tk.W, padx=(16, 0), pady=(2, 0))
    ttk.Button(p4.body, text="记住当前", width=8,
               command=lambda: win_size.set(root.geometry().split("+")[0])
               ).grid(row=1, column=6, sticky=tk.W, pady=(2, 0))

    from . import theme as _theme
    from .color_picker import pick_screen_color

    fam, mod = _theme.parse_theme(cfg.ui.theme)
    theme_fam = tk.StringVar(value=fam)
    theme_mode = tk.StringVar(value=mod)
    ttk.Label(p4.body, text="主题色").grid(row=0, column=0, sticky=tk.W)
    ttk.Combobox(p4.body, textvariable=theme_fam, state="readonly", width=18,
                 values=list(_theme.THEME_FAMILY_LABELS.keys())
                 ).grid(row=1, column=0, sticky=tk.W, pady=(2, 6))
    ttk.Label(p4.body, text="模式").grid(row=0, column=1, sticky=tk.W, padx=(16, 0))
    ttk.Radiobutton(p4.body, text="浅色", variable=theme_mode,
                    value="light").grid(row=1, column=1, sticky=tk.W, padx=(16, 0))
    ttk.Radiobutton(p4.body, text="深色", variable=theme_mode,
                    value="dark").grid(row=1, column=2, sticky=tk.W)

    # 界面字号：所有字体按这个基准派生（正文/标题/等宽同步放大）
    ttk.Label(p4.body, text="界面字号").grid(row=0, column=3, sticky=tk.W, padx=(16, 0))
    font_size = tk.IntVar(value=int(cfg.ui.font_size or 11))
    ttk.Spinbox(p4.body, from_=8, to=18, width=5, textvariable=font_size
                ).grid(row=1, column=3, sticky=tk.W, padx=(16, 0))

    ttk.Separator(p4.body, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=7,
                                                 sticky=tk.EW, pady=12)

    # 取色器收进「高级」折叠区：simple 模式默认收起（降低认知负担），
    # advanced 模式展开；配色可导出/导入（P1-3）
    picker = ttk.LabelFrame(p4.body, text="自定义配色（高级）", padding=(8, 6))
    picker.grid(row=3, column=0, columnspan=7, sticky=tk.EW)

    ttk.Label(picker, text="自定义配色（覆盖上面主题，留空 = 用主题默认）",
              font=theme.UI_FONT_BOLD).grid(row=0, column=0, columnspan=4,
                                            sticky=tk.W, pady=(0, 6))

    # 可自定义的角色：token → 中文名
    _ROLE_LABELS = {
        "accent": "主色", "accent_hover": "主色(悬停)", "border": "边框",
        "canvas_bg": "对话区背景", "card_bg": "卡片背景", "input_bg": "输入框背景",
        "text_main": "正文文字", "text_muted": "次要文字", "text_hint": "提示文字",
        "danger": "危险/删除", "assist_bg": "AI 气泡背景",
        "assist_border": "AI 气泡边框", "system_bg": "系统气泡背景",
        "system_border": "系统气泡边框",
    }
    overrides = dict(cfg.ui.theme_colors or {})
    swatch_btns = {}

    def _swatch_color(tok: str) -> str:
        return overrides.get(tok) or _theme.ACTIVE.get(tok, "#ffffff")

    def _pick_color(tok: str) -> None:
        from tkinter import colorchooser
        cur = _swatch_color(tok)
        rgb, hexv = colorchooser.askcolor(initialcolor=cur, parent=win)
        if not hexv:
            return
        overrides[tok] = hexv
        try:
            swatch_btns[tok].config(bg=hexv)
        except Exception:
            pass

    def _pick_screen(tok: str) -> None:
        def _cb(hexv):
            if not hexv:
                return
            overrides[tok] = hexv
            try:
                swatch_btns[tok].config(bg=hexv)
            except Exception:
                pass
        # 先收起设置页再取色，避免取到设置窗口自身
        win.withdraw()
        pick_screen_color(lambda h: (win.deiconify(), _cb(h)))

    from tkinter import colorchooser  # 供下方复用
    r = 1
    for tok, label in _ROLE_LABELS.items():
        ttk.Label(picker, text=label).grid(row=r, column=0, sticky=tk.W, pady=2)
        btn = tk.Button(picker, width=4, relief=tk.FLAT, bd=1,
                        bg=_swatch_color(tok), cursor="hand2")
        btn.grid(row=r, column=1, sticky=tk.W, padx=(8, 4), pady=2)
        swatch_btns[tok] = btn
        btn.config(command=lambda t=tok: _pick_color(t))
        ttk.Button(picker, text="屏幕取色", width=8,
                   command=lambda t=tok: _pick_screen(t)
                   ).grid(row=r, column=2, sticky=tk.W, padx=(0, 4), pady=2)
        r += 1

    def _reset_colors():
        overrides.clear()
        for tok in _ROLE_LABELS:
            try:
                swatch_btns[tok].config(bg=_theme.ACTIVE.get(tok, "#ffffff"))
            except Exception:
                pass

    def _export_colors():
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=win, title="导出配色", defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        import json
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in overrides.items()
                           if k in _theme.TOKENS}, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("导出配色", f"已导出 {len(overrides)} 项 → {path}",
                                parent=win)
        except Exception as e:
            messagebox.showwarning("导出失败", str(e), parent=win)

    def _import_colors():
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=win, title="导入配色", filetypes=[("JSON", "*.json")])
        if not path:
            return
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showwarning("导入失败", f"无法读取: {e}", parent=win)
            return
        if not isinstance(data, dict):
            messagebox.showwarning("导入失败", "文件格式不对：应为 {角色: 颜色} 的 JSON。",
                                   parent=win)
            return
        for k, v in data.items():
            if k in _theme.TOKENS and isinstance(v, str) and v.startswith("#"):
                overrides[k] = v
                try:
                    swatch_btns[k].config(bg=v)
                except Exception:
                    pass
        messagebox.showinfo("导入配色", f"已导入 {len(overrides)} 项配色。\n点「保存」后生效。",
                            parent=win)

    ttk.Button(picker, text="重置自定义配色", command=_reset_colors
               ).grid(row=r, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
    ttk.Button(picker, text="界面取色定位…", command=lambda: _open_inspector()
               ).grid(row=r, column=2, columnspan=2, sticky=tk.W, pady=(8, 0))
    ttk.Button(picker, text="导出配色", command=_export_colors
               ).grid(row=r + 1, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
    ttk.Button(picker, text="导入配色", command=_import_colors
               ).grid(row=r + 1, column=2, columnspan=2, sticky=tk.W, pady=(4, 0))

    def _open_inspector() -> None:
        """框选屏幕上任意区域 → 自动判断它对应哪个主题角色 → 回填该处颜色。

        用来解决「我看到某块颜色难看，但不知道它在设置里叫什么」。
        """
        from .ui_inspector import inspect_ui_role

        def _cb(res):
            win.deiconify()
            if not res:
                return
            tok, hexv = res
            if tok not in _ROLE_LABELS:
                messagebox.showinfo(
                    "取色结果",
                    f"取到颜色 {hexv}，但该区域不属于可自定义的角色。\n"
                    "可手动选一个最接近的角色再用「屏幕取色」。", parent=win)
                return
            overrides[tok] = hexv
            try:
                swatch_btns[tok].config(bg=hexv)
            except Exception:
                pass
            messagebox.showinfo(
                "已识别",
                f"该区域对应「{_ROLE_LABELS[tok]}」，已填入 {hexv}。\n"
                "点「保存」后生效。", parent=win)

        win.withdraw()
        inspect_ui_role(window, _cb)

    # simple 模式默认收起取色器（降低认知负担），advanced 展开
    def _sync_picker_visibility(*_) -> None:
        try:
            if mode.get() == "advanced":
                picker.grid()
            else:
                picker.grid_remove()
        except Exception:
            pass

    mode.trace_add("write", _sync_picker_visibility)
    _sync_picker_visibility()

    # ================= 朗读（TTS） =================
    p5 = _ScrollableFrame(nb)
    nb.add(p5, text="朗读")

    tts_cfg = cfg.tts
    tts_svc = app.services.get("tts")

    ttk.Label(p5.body, text="朗读引擎", font=theme.UI_FONT_BOLD
              ).grid(row=0, column=0, sticky=tk.W, pady=(0, 4))
    tts_engine = tk.StringVar(value=tts_cfg.engine or "auto")
    _ENGINE_LABELS = {
        "auto": "自动（在线优先，断网自动降级）",
        "edge": "Edge 在线语音（音质好，需联网）",
        "sapi": "系统语音（离线，机械音）",
    }
    for i, (val, lab) in enumerate(_ENGINE_LABELS.items()):
        ttk.Radiobutton(p5.body, text=lab, variable=tts_engine, value=val
                        ).grid(row=1 + i, column=0, columnspan=3, sticky=tk.W)

    ttk.Separator(p5.body, orient=tk.HORIZONTAL).grid(row=4, column=0, columnspan=3,
                                                 sticky=tk.EW, pady=10)

    ttk.Label(p5.body, text="音色（仅在线引擎生效）").grid(row=5, column=0, sticky=tk.W)
    _voices = tts_svc.list_voices() if tts_svc else []
    _voice_labels = [f"{lab}  ·  {vid}" for vid, lab in _voices]
    _label_to_id = {f"{lab}  ·  {vid}": vid for vid, lab in _voices}
    tts_voice = tk.StringVar()
    cur_voice = tts_cfg.voice or "zh-CN-XiaoxiaoNeural"
    tts_voice.set(next((l for l, v in _label_to_id.items() if v == cur_voice),
                       cur_voice))
    ttk.Combobox(p5.body, textvariable=tts_voice, state="readonly", width=42,
                 values=_voice_labels).grid(row=6, column=0, columnspan=3,
                                            sticky=tk.W, pady=(2, 8))

    ttk.Label(p5.body, text="语速（%）").grid(row=7, column=0, sticky=tk.W)
    tts_rate = tk.IntVar(value=int(tts_cfg.rate or 0))
    ttk.Spinbox(p5.body, from_=-50, to=100, increment=10, width=6,
                textvariable=tts_rate).grid(row=7, column=1, sticky=tk.W, padx=(8, 0))
    ttk.Label(p5.body, text="音量（%）").grid(row=8, column=0, sticky=tk.W, pady=(4, 0))
    tts_vol = tk.IntVar(value=int(tts_cfg.volume or 0))
    ttk.Spinbox(p5.body, from_=-50, to=50, increment=10, width=6,
                textvariable=tts_vol).grid(row=8, column=1, sticky=tk.W,
                                           padx=(8, 0), pady=(4, 0))

    tts_auto = tk.BooleanVar(value=bool(tts_cfg.auto_read))
    ttk.Checkbutton(p5.body, text="翻译完成后自动朗读译文", variable=tts_auto
                    ).grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

    def _test_tts() -> None:
        if tts_svc is None:
            tts_lbl.config(text="朗读服务不可用")
            return
        # 用页面上「当下选的值」试听，而不是已保存的值 —— 不然要先保存才能试
        import copy
        probe = copy.copy(tts_cfg)
        probe.engine = tts_engine.get()
        probe.voice = _label_to_id.get(tts_voice.get(), tts_voice.get())
        probe.rate = int(tts_rate.get() or 0)
        probe.volume = int(tts_vol.get() or 0)
        old, tts_svc.cfg = tts_svc.cfg, probe
        tts_lbl.config(text="正在试听…")

        def _restore(msg: str) -> None:
            tts_svc.cfg = old
            try:
                win.after(0, lambda: tts_lbl.config(text=msg))
            except Exception:
                pass

        tts_svc.speak("WinOCR 朗读测试，Hello from WinOCR.", on_status=_restore)

    ttk.Button(p5.body, text="试听", command=_test_tts
               ).grid(row=10, column=0, sticky=tk.W, pady=(12, 0))
    ttk.Button(p5.body, text="停止", command=lambda: tts_svc and tts_svc.stop()
               ).grid(row=10, column=1, sticky=tk.W, pady=(12, 0), padx=(8, 0))
    tts_lbl = ttk.Label(p5.body, text=(tts_svc.engine_display() if tts_svc
                                  else "朗读服务不可用"),
                        foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL)
    tts_lbl.grid(row=11, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

    ttk.Label(p5.body, text="提示：朗读热键 Ctrl+Shift+R；正在朗读时再按一次即停止。\n"
                       "在线音色需联网首次拉取；离线时自动改用系统语音。",
              foreground=theme.TEXT_MUTED, font=theme.UI_FONT_SMALL,
              justify=tk.LEFT).grid(row=12, column=0, columnspan=3,
                                    sticky=tk.W, pady=(10, 0))

    # ================= 翻译生效预览 =================
    def _refresh_eff(*_) -> None:
        eff = app.effective_translate_llm()
        eff_lbl.config(text=(f"当前生效（翻译功能自己的连接参数）：\n"
                             f"  模型：{eff['model'] or '（内置默认）'}\n"
                             f"  URL：{eff['url'] or '（内置默认）'}\n"
                             f"  API Key：{'已填写' if eff['has_key'] else '未填写，glm 引擎不可用'}"))
    _refresh_eff()

    # ---- 设置内搜索（P2-1）：遍历五个页签的 Label/Checkbutton 文本 ----
    _search_tabs = [p_ai, p2, p3, p4, p5]

    def _do_search(*_) -> None:
        q = (search_var.get() or "").strip().lower()
        if not q:
            search_hint.config(text="")
            return
        hits = []
        for idx, sf in enumerate(_search_tabs):
            try:
                for w in sf.body.winfo_children():
                    try:
                        txt = (w.cget("text") or "")
                    except Exception:
                        txt = ""
                    if txt and q in txt.lower():
                        hits.append((idx, txt))
            except Exception:
                continue
        if hits:
            first_idx, first_txt = hits[0]
            try:
                nb.select(first_idx)
            except Exception:
                pass
            search_hint.config(
                text=f"命中 {len(hits)} 项 → 已切到「{nb.tab(first_idx, 'text')}」页（{first_txt}）")
        else:
            search_hint.config(text="无匹配")

    search_var.trace_add("write", _do_search)

    # ================= 保存 =================
    def _save():
        try:
            # 每个功能页直接把页面上的连接参数写回各自配置（无「平台账号」中间层）
            _apply_conn_vars(cfg.ai, ai_v)
            _apply_conn_vars(cfg.translate, tr_v)
            _apply_conn_vars(cfg.ocr, ocr_v)
        except ValueError:
            messagebox.showwarning("参数错误", "采样 / 限流参数必须是数字", parent=win)
            return
        try:
            cfg.ai.provider = provider.get().split(" — ")[0].strip()
            t = cfg.translate
            t.engine = eng.get().split(" — ")[0].strip()
            t.target = tgt.get().split(" — ")[0].strip()
            t.auto_translate = auto_t.get()
            t.offline_mode = offline.get()
            parsed = [s.strip() for s in order.get().split(",") if s.strip()]
            if parsed:
                t.fallback_order = parsed

            o.engine = ocr_eng.get().strip()
            o.preprocess = pre.get()
            o.model_type = mt.get()
            o.structured = struct.get()
            o.auto_upgrade = auto_up.get()
        except ValueError:
            messagebox.showwarning("参数错误", "翻译 / OCR 参数必须是数字", parent=win)
            return
        _refresh_eff()

        cfg.ui.mode = mode.get()
        ws = win_size.get().strip()
        if ws:
            cfg.ui.window_size = ws

        # ---- 外观 / 配色 / 字号 ----
        prev_theme = cfg.ui.theme
        prev_colors = dict(cfg.ui.theme_colors or {})
        prev_font = int(cfg.ui.font_size or 11)
        cfg.ui.theme = f"{theme_fam.get()}:{theme_mode.get()}"
        cfg.ui.theme_colors = {k: v for k, v in overrides.items()
                               if k in _theme.TOKENS}
        try:
            cfg.ui.font_size = max(8, min(18, int(font_size.get() or 11)))
        except Exception:
            pass

        # ---- 朗读 ----
        cfg.tts.engine = tts_engine.get() or "auto"
        cfg.tts.voice = _label_to_id.get(tts_voice.get(), tts_voice.get()).strip()
        try:
            cfg.tts.rate = int(tts_rate.get() or 0)
            cfg.tts.volume = int(tts_vol.get() or 0)
        except Exception:
            pass
        cfg.tts.auto_read = bool(tts_auto.get())

        app.apply_config()                 # 一处生效：重新注入所有服务并落盘
        window.refresh_engine_label()
        window.apply_ui_mode(cfg.ui.mode)
        window.set_status("设置已保存并生效")

        theme_changed = (cfg.ui.theme != prev_theme
                         or dict(cfg.ui.theme_colors or {}) != prev_colors
                         or int(cfg.ui.font_size or 11) != prev_font)
        win.destroy()
        if theme_changed and getattr(app, "ui", None) is not None:
            root.after(20, lambda: app.ui.reload_ui())

    bar = ttk.Frame(win, padding=(12, 0, 12, 12))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "保存", lambda: _save()).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)

    _center(win, root)


# ======================================================================
# 连接测试（各自后台跑，结果回写 label）
# ======================================================================
def _test_ai(app, key, base_url, text_model, vision_model,
              vision_base_url, vision_key, which, label) -> None:
    key = (key or "").strip()
    if not key:
        label.config(text="请先填入 API Key", foreground="#c0392b")
        return
    label.config(text="测试中…", foreground=theme.TEXT_MUTED)

    def _work():
        from ...core.types import ChatMessage
        try:
            cls = app.discovered.get("ai", {}).get("glm")
            if cls is None:
                raise RuntimeError("未发现 GLM 提供方插件")
            probe = cls()                    # 临时实例，不污染主对话历史
            probe.set_api_key(key)
            probe.set_base_url(base_url)
            probe.set_models(text_model=text_model.strip(),
                             vision_model=vision_model.strip())
            probe.set_vision_config(base_url=vision_base_url.strip(),
                                    api_key=vision_key.strip(),
                                    model=vision_model.strip())
            if which == "vision":
                from PIL import Image
                img = Image.new("RGB", (64, 64), "white")
                reply = probe.chat(ChatMessage(
                    text="用两个字描述这张图：空白", images=[img]))
                model = probe.vision_client.cfg.model
                url = probe.vision_client.cfg.effective_url()
            else:
                reply = probe.chat(ChatMessage(text="回复两个字：可用"))
                model = probe.text_client.cfg.model
                url = probe.text_client.cfg.effective_url()
            msg = f"连接正常（{model}）:\n{url}\n{reply[:40]}"
            color = "#1e8e3e"
        except Exception as e:
            msg, color = f"连接失败: {e}", "#c0392b"

        def _show():
            try:
                label.config(text=msg, foreground=color)
            except Exception:
                pass
        _post_to_ui(app, _show)

    threading.Thread(target=_work, daemon=True, name="ai-test").start()


def _test_translate(app, base_url, model, api_key, label) -> None:
    kw = app._translate_llm_kwargs()
    if not (api_key or kw["glm_api_key"]):
        label.config(text="请先在「大模型翻译」页填写 API Key", foreground="#c0392b")
        return
    label.config(text="测试中…", foreground=theme.TEXT_MUTED)

    def _work():
        try:
            cls = app.discovered.get("translate", {}).get("glm")
            if cls is None:
                raise RuntimeError("未发现 GLM 翻译引擎")
            probe = cls()
            probe.set_config(
                glm_api_key=(api_key or "").strip() or kw["glm_api_key"],
                base_url=(base_url or "").strip() or kw["base_url"],
                model=(model or "").strip() or kw["model"],
                max_output_tokens=kw["max_output_tokens"],
                retry_attempts=kw["retry_attempts"],
                retry_backoff=kw["retry_backoff"],
            )
            if not probe.available():
                raise RuntimeError("翻译 API Key 仍为空")
            out = probe.translate("Hello, world.", "en", "zh-CN")
            msg = f"翻译正常: Hello, world. → {out}"
            color = "#1e8e3e"
        except Exception as e:
            msg, color = f"连接失败: {e}", "#c0392b"

        def _show():
            try:
                label.config(text=msg, foreground=color)
            except Exception:
                pass
        _post_to_ui(app, _show)

    threading.Thread(target=_work, daemon=True, name="translate-test").start()


def _test_ocr(app, base_url, model, api_key, label) -> None:
    """用「云端 OCR」页填的连接参数发真实测试请求。

    页面上填的参数优先；留空则回落到配置里已存的值。
    """
    kw = app._cloud_ocr_kwargs()
    api_key = (api_key or "").strip() or kw["api_key"]
    base_url = (base_url or "").strip() or kw["base_url"]
    model = (model or "").strip() or kw["model"]
    if app.config.ocr.engine != "vision_ocr":
        label.config(text="当前 OCR 引擎不是「云端视觉 OCR」，无需测试云端连接",
                     foreground="#c0392b")
        return
    if not api_key:
        label.config(text="请先在「云端 OCR」页填写 API Key",
                     foreground="#c0392b")
        return
    label.config(text="测试中…", foreground=theme.TEXT_MUTED)

    def _work():
        try:
            cls = app.discovered.get("ocr", {}).get("vision_ocr")
            if cls is None:
                raise RuntimeError("未发现云端视觉 OCR 引擎")
            probe = cls()
            probe.configure(cloud_api_key=api_key,
                            cloud_base_url=base_url,
                            cloud_model=model,
                            cloud_max_output_tokens=kw["max_output_tokens"],
                            cloud_retry_attempts=kw["retry_attempts"],
                            cloud_retry_backoff=kw["retry_backoff"],
                            cloud_timeout=kw["timeout"])
            if not probe.available():
                raise RuntimeError("云端 OCR API Key 为空")
            from PIL import Image
            img = Image.new("RGB", (80, 40), "white")
            res = probe.recognize(img)
            if not res.ok:
                raise RuntimeError(res.text or "返回为空")
            msg = f"视觉 OCR 正常（{probe._model}）:\n{res.text[:60]}"
            color = "#1e8e3e"
        except Exception as e:
            msg, color = f"连接失败: {e}", "#c0392b"

        def _show():
            try:
                label.config(text=msg, foreground=color)
            except Exception:
                pass
        _post_to_ui(app, _show)

    threading.Thread(target=_work, daemon=True, name="ocr-test").start()


# ======================================================================
# 关于
# ======================================================================
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


# ======================================================================
# 知识库（浏览 / 检索 / 删除 / 导出 JSON·MD·CSV / 导入 JSON·CSV）
# ======================================================================
# ----------------------------------------------------------------------
# 导入：模板自动生成 + 引导窗口
# ----------------------------------------------------------------------
_IMPORT_FIELD_HINTS = """【WinOCR 知识库导入模板 · 字段说明】

每行一条知识（原文/译文/AI 解读至少填一个，全空行自动跳过）。
「id」留空即可（自动编号）；「created_at」留空 = 当前时间。

字段名（英文，保持表头不改）：
  source_type    来源类型：manual / screenshot / clipboard / file / chat（可自定）
  source_path    来源文件路径（文件类才有，可留空）
  image_hash     原图哈希（溯源用，可留空）
  scene          场景标签：如「病历」「房产调研」（老版本字段叫 scenario，已兼容）
  tags           标签（可留空）
  note           备注（可留空）
  ocr_text       原文（OCR 识别文本，**必填之一**）
  translate_text 译文（**必填之一**）
  ai_explanation AI 解读（可留空）
  created_at     时间，格式 2026-08-24 10:00:00（留空=当前）

使用：
  1. 用 Excel / WPS 打开 .csv 模板，删掉示例行，按列填你的数据；
  2. 按 Ctrl+Shift+I（或知识库面板「导入」）→ 选目标项目 → 选择这个文件；
  3. 导入完成后在知识库面板检索验证。
"""


def ensure_import_templates() -> list:
    """在固定模板目录生成导入模板（CSV + JSON + 说明），幂等：已存在不覆盖。

    返回生成的模板文件路径列表。首次调用创建，之后直接返回既有文件。
    """
    from ...core.paths import import_templates_dir
    d = import_templates_dir()
    made = []

    csv_p = d / "知识库导入模板.csv"
    if not csv_p.exists():
        csv_text = (
            "id,created_at,source_type,source_path,image_hash,scene,tags,note,"
            "ocr_text,translate_text,ai_explanation\n"
            ',"2026-08-24 10:00:00",manual,"","","病历","ICD-10","",'
            '"肌酐偏高 肾功能检查","creatinine high","建议复查"\n'
            ',"",manual,"","","房产调研","","","梅东路大院 1992 年 无电梯",'
            '"Meidong Road compound built 1992 no elevator",""\n'
        )
        try:
            with open(csv_p, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv_text)
            made.append(csv_p)
        except OSError:
            pass

    json_p = d / "知识库导入模板.json"
    if not json_p.exists():
        import json
        sample = [{
            "id": 0, "created_at": "2026-08-24 10:00:00",
            "source_type": "manual", "source_path": "", "image_hash": "",
            "scene": "病历", "scenario": "", "tags": "ICD-10", "note": "",
            "ocr_text": "肌酐偏高 肾功能检查",
            "translate_text": "creatinine high",
            "ai_explanation": "建议复查",
        }]
        try:
            with open(json_p, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False, indent=2)
            made.append(json_p)
        except OSError:
            pass

    readme_p = d / "导入模板说明.txt"
    if not readme_p.exists():
        try:
            with open(readme_p, "w", encoding="utf-8") as f:
                f.write(_IMPORT_FIELD_HINTS)
            made.append(readme_p)
        except OSError:
            pass

    return made


def _run_import(window, path: str, project: str, status_callback=None) -> None:
    """按扩展名分发导入（json / csv），后台线程执行，完成后回写状态。"""
    app = window.app
    kb = app.services.get("knowledge")
    if kb is None:
        try:
            messagebox.showinfo("导入知识库", "知识库服务未初始化。",
                                parent=window.root)
        except Exception:
            pass
        return

    def _post_status(text):
        try:
            if status_callback is not None:
                status_callback(text)
            else:
                window.set_status(text)
        except Exception:
            pass

    _post_to_ui(app, lambda: _post_status("导入中…"))
    ext = os.path.splitext(path)[1].lower() if isinstance(path, str) else ""

    def _work():
        try:
            if ext == ".csv":
                ok, skipped = kb.import_csv(path, project=project)
            else:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = data.get("records", [])
                if not isinstance(data, list):
                    data = [data]
                ok, skipped = kb.import_records(data, project=project)
        except Exception as e:
            _post_to_ui(app, lambda: _post_status(f"导入失败: {e}"))
            return
        _post_to_ui(app, lambda: _post_status(
            f"已导入 {ok} 条（跳过 {skipped} 条空记录）→ {project}"))

    threading.Thread(target=_work, daemon=True, name="kb-import").start()


def open_import_dialog(window, status_callback=None) -> None:
    """导入引导窗口：选目标项目 → 打开模板文件夹 / 选 JSON / 选 CSV 导入。

    固定模板目录由 ``ensure_import_templates`` 自动生成（幂等）。
    被知识库面板「导入」按钮和全局热键「导入知识库」共同调用。
    """
    app = window.app
    root = window.root

    ensure_import_templates()
    from ...core.paths import import_templates_dir
    tpl_dir = import_templates_dir()

    win = tk.Toplevel(root)
    win.title("导入知识库")
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()

    body = ttk.Frame(win, padding=14)
    body.pack(fill=tk.BOTH, expand=True)

    ttk.Label(body, text="导入知识库", font=theme.TITLE_FONT).pack(anchor=tk.W)
    ttk.Label(body, text="选择目标项目，再选一个数据文件（JSON / CSV）。",
              foreground=theme.TEXT_MUTED).pack(anchor=tk.W, pady=(2, 10))

    # ---- 目标项目 ----
    ttk.Label(body, text="导入到项目").pack(anchor=tk.W)
    pm = app.projects
    projs = pm.config.projects
    cur = pm.current_id
    opts = []
    cur_idx = 0
    for i, p in enumerate(projs):
        mark = "（当前）" if p.id == cur else ""
        if not p.open:
            mark += "（已关闭）"
        opts.append(f"{p.name} — {p.id}{mark}")
        if p.id == cur:
            cur_idx = i
    pid_var = tk.StringVar(value=opts[cur_idx] if opts else "")
    ttk.Combobox(body, textvariable=pid_var, state="readonly", width=34,
                 values=opts).pack(anchor=tk.W, pady=(2, 10))

    # ---- 模板提示 ----
    tpl_box = ttk.LabelFrame(body, text="导入模板（系统自动生成，可编辑）",
                             padding=8)
    tpl_box.pack(fill=tk.X, pady=(0, 10))
    ttk.Label(tpl_box, text=f"目录：{tpl_dir}",
              foreground=theme.TEXT_MUTED, wraplength=360).pack(anchor=tk.W)

    def _open_tpl_dir():
        try:
            import os
            os.startfile(str(tpl_dir))
        except Exception as e:
            messagebox.showwarning("打开失败", str(e), parent=win)

    bar1 = ttk.Frame(tpl_box)
    bar1.pack(fill=tk.X, pady=(6, 0))
    ttk.Button(bar1, text="📂 打开模板文件夹", command=_open_tpl_dir).pack(side=tk.LEFT)
    ttk.Label(bar1, text="用 WPS/Excel 编辑 CSV 模板，或参考 JSON 模板结构",
              foreground=theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(8, 0))

    # ---- 动作 ----
    def _pick(ext_filter, title):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=win, title=title,
            filetypes=[(f"{ext_filter.upper()} 文件", f"*.{ext_filter}"),
                       ("所有文件", "*.*")])
        return path

    def _do_import(ext):
        path = _pick(ext, f"选择要导入的 {ext.upper()} 文件")
        if not path:
            return
        pid = None
        val = pid_var.get().strip()
        for i, p in enumerate(projs):
            if opts[i] == val:
                pid = p.id
                break
        if pid is None:
            pid = cur
        win.destroy()
        _run_import(window, path, pid, status_callback)

    bar = ttk.Frame(win, padding=(14, 0, 14, 14))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "导入 JSON…", lambda: _do_import("json")).pack(side=tk.RIGHT)
    ttk.Button(bar, text="导入 CSV…", command=lambda: _do_import("csv")
               ).pack(side=tk.RIGHT, padx=6)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=2)

    _center(win, root)


def open_knowledge(window) -> None:
    """知识库管理：检索（FTS5+LIKE 兜底）、列表回看、删除、导出三种格式、
    导入 JSON（可指定目标项目）。

    底层由 KnowledgeBase（sqlite3 + FTS5）驱动；本对话框只做表现层，
    所有操作都在后台线程执行后回写 Tk（项目铁律：非主线程不碰 Tk）。
    """
    app = window.app
    kb = app.services.get("knowledge")
    if kb is None:
        messagebox.showinfo("知识库", "知识库服务未初始化，无法打开。", parent=window.root)
        return

    win = tk.Toplevel(window.root)
    win.title("知识库")
    win.geometry("760x520")
    win.minsize(620, 380)
    win.transient(window.root)

    # ---- 顶部：检索框 + 动作按钮 ----
    top = ttk.Frame(win, padding=(10, 8))
    top.pack(fill=tk.X)
    q = tk.StringVar()
    ttk.Label(top, text="检索：").pack(side=tk.LEFT)
    ent = ttk.Entry(top, textvariable=q, width=32)
    ent.pack(side=tk.LEFT, padx=(0, 6))
    ent.bind("<Return>", lambda e: _load())
    ttk.Button(top, text="搜索", command=lambda: _load()).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="全部", command=lambda: (q.set(""), _load())).pack(side=tk.LEFT, padx=2)
    show_all_proj = tk.BooleanVar(value=True)
    ttk.Checkbutton(top, text="所有项目", variable=show_all_proj,
                    command=lambda: _load()).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="导出 JSON", command=lambda: _export("json")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导出 MD", command=lambda: _export("md")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导出 CSV", command=lambda: _export("csv")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导入", command=lambda: open_import_dialog(
        window, status_callback=_set_status)).pack(side=tk.RIGHT, padx=2)

    # ---- 中部：列表 + 详情预览 ----
    body = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

    left = ttk.LabelFrame(body, text="记录", padding=4)
    cols = ("id", "created_at", "source_type", "scene", "snippet")
    tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
    tree.heading("id", text="ID")
    tree.heading("created_at", text="时间")
    tree.heading("source_type", text="来源")
    tree.heading("scene", text="场景")
    tree.heading("snippet", text="内容摘要")
    tree.column("id", width=40, anchor=tk.E)
    tree.column("created_at", width=120, anchor=tk.W)
    tree.column("source_type", width=80, anchor=tk.W)
    tree.column("scene", width=60, anchor=tk.W)
    tree.column("snippet", width=280, anchor=tk.W)
    vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)
    body.add(left, weight=3)

    right = ttk.LabelFrame(body, text="详情（原文 / 译文 / AI 解读）", padding=4)
    detail = tk.Text(right, wrap=tk.WORD, font=theme.UI_FONT_SMALL, relief=tk.FLAT,
                     bg=theme.CANVAS_BG, state=tk.DISABLED)
    dvsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=detail.yview)
    detail.configure(yscrollcommand=dvsb.set)
    dvsb.pack(side=tk.RIGHT, fill=tk.Y)
    detail.pack(fill=tk.BOTH, expand=True)
    body.add(right, weight=2)

    # ---- 底部：状态 + 删除 ----
    bar = ttk.Frame(win, padding=(10, 0, 10, 10))
    bar.pack(fill=tk.X)
    ttk.Button(bar, text="删除选中", command=lambda: _delete()).pack(side=tk.RIGHT)
    status = ttk.Label(bar, text="", foreground=theme.TEXT_MUTED)
    status.pack(side=tk.LEFT)

    _records = []          # 当前列表对应的记录（id → KnowledgeRecord）

    def _set_status(text: str) -> None:
        try:
            status.config(text=text)
        except Exception:
            pass

    def _show_detail(rec) -> None:
        detail.config(state=tk.NORMAL)
        detail.delete("1.0", tk.END)
        detail.insert("1.0",
                      f"【原文】\n{rec.ocr_text or '（无）'}\n\n"
                      f"【译文】\n{rec.translate_text or '（无）'}\n\n"
                      f"【AI 解读】\n{rec.ai_explanation or '（无）'}\n\n"
                      f"来源：{rec.source_type or '-'}  场景：{rec.scene or '-'}\n"
                      f"时间：{rec.created_at}  ID：{rec.id}")
        detail.config(state=tk.DISABLED)

    def _load() -> None:
        """后台检索 → 回写列表（不阻塞 UI）。"""
        query = q.get().strip()
        _set_status("检索中…")
        proj = "*" if show_all_proj.get() else None

        def _work():
            try:
                rows = kb.search(query, limit=200, project=proj)
            except Exception as e:
                rows = []
                err = str(e)
                _post_to_ui(app, lambda: _set_status(f"检索失败: {err}"))
                return
            _post_to_ui(app, lambda: _fill(rows, query))

        threading.Thread(target=_work, daemon=True, name="kb-search").start()

    def _fill(rows, query: str) -> None:
        for it in tree.get_children():
            tree.delete(it)
        _records[:] = rows        # 就地替换闭包列表（勿用 global，否则详情读到空）
        for i, r in enumerate(_records):
            snippet = (r.ocr_text or r.translate_text or r.ai_explanation or "").replace(
                "\n", " ").strip()
            if len(snippet) > 60:
                snippet = snippet[:60] + "…"
            tree.insert("", tk.END, iid=str(i), values=(
                r.id, r.created_at, r.source_type, r.scene, snippet))
        _set_status(f"共 {len(_records)} 条" + (f"（检索词：{query}）" if query else ""))

    def _delete() -> None:
        sel = tree.selection()
        if not sel:
            _set_status("请先在列表中选择要删除的记录")
            return
        idx = int(sel[0])
        if not (0 <= idx < len(_records)):
            return
        rid = _records[idx].id
        if not messagebox.askyesno("删除", f"确定删除 ID={rid} 这条知识吗？", parent=win):
            return
        try:
            kb.delete(rid)
            _set_status(f"已删除 ID={rid}")
        except Exception as e:
            _set_status(f"删除失败: {e}")
        _load()

    def _export(fmt: str) -> None:
        from tkinter import filedialog
        ext = {"json": "*.json", "md": "*.md", "csv": "*.csv"}[fmt]
        path = filedialog.asksaveasfilename(
            parent=win, title=f"导出知识库（{fmt.upper()}）",
            defaultextension=f".{fmt}", filetypes=[(fmt.upper(), ext)])
        if not path:
            return
        _set_status("导出中…")

        def _work():
            try:
                text = kb.export(fmt, q.get().strip(),
                                 project=("*" if show_all_proj.get() else None))
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"导出失败: {e}"))
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"写入失败: {e}"))
                return
            _post_to_ui(app, lambda: _set_status(f"已导出 {len(text)} 字符 → {path}"))

        threading.Thread(target=_work, daemon=True, name="kb-export").start()

    tree.bind("<<TreeviewSelect>>", lambda e: _show_detail(_records[int(tree.selection()[0])])
              if tree.selection() else None)

    _load()
    _center(win, window.root)


def open_history(window) -> None:
    """历史记录浏览：检索、列表回看、详情、复制原文/译文、导出 MD·TXT、清空。

    底层由 JsonHistory（原子写 JSON，上限 500 条）驱动；本对话框只做表现层，
    所有读取/导出都在后台线程执行后回写 Tk（项目铁律：非主线程不碰 Tk）。
    划词翻译与截图翻译均会写入历史（见 pipeline.record）。
    """
    app = window.app
    store = app.services.get("persistence")
    if store is None:
        messagebox.showinfo("历史记录", "历史记录服务未初始化，无法打开。", parent=window.root)
        return

    win = tk.Toplevel(window.root)
    win.title("历史记录")
    win.geometry("760x520")
    win.minsize(620, 380)
    win.transient(window.root)

    # ---- 顶部：检索框 + 导出 ----
    top = ttk.Frame(win, padding=(10, 8))
    top.pack(fill=tk.X)
    q = tk.StringVar()
    ttk.Label(top, text="检索：").pack(side=tk.LEFT)
    ent = ttk.Entry(top, textvariable=q, width=32)
    ent.pack(side=tk.LEFT, padx=(0, 6))
    ent.bind("<Return>", lambda e: _load())
    ttk.Button(top, text="搜索", command=lambda: _load()).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="全部", command=lambda: (q.set(""), _load())).pack(side=tk.LEFT, padx=2)
    ttk.Button(top, text="导出 MD", command=lambda: _export("md")).pack(side=tk.RIGHT, padx=2)
    ttk.Button(top, text="导出 TXT", command=lambda: _export("txt")).pack(side=tk.RIGHT, padx=2)

    # ---- 中部：列表 + 详情 ----
    body = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

    left = ttk.LabelFrame(body, text="记录（新 → 旧）", padding=4)
    cols = ("time", "orig")
    tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
    tree.heading("time", text="时间")
    tree.heading("orig", text="原文摘要")
    tree.column("time", width=140, anchor=tk.W)
    tree.column("orig", width=300, anchor=tk.W)
    vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)
    body.add(left, weight=3)

    right = ttk.LabelFrame(body, text="详情（原文 / 译文）", padding=4)
    detail = tk.Text(right, wrap=tk.WORD, font=theme.UI_FONT_SMALL, relief=tk.FLAT,
                     bg=theme.CANVAS_BG, state=tk.DISABLED)
    dvsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=detail.yview)
    detail.configure(yscrollcommand=dvsb.set)
    dvsb.pack(side=tk.RIGHT, fill=tk.Y)
    detail.pack(fill=tk.BOTH, expand=True)
    body.add(right, weight=2)

    # ---- 底部：复制 + 清空 ----
    bar = ttk.Frame(win, padding=(10, 0, 10, 10))
    bar.pack(fill=tk.X)
    ttk.Button(bar, text="复制原文", command=lambda: _copy_orig()).pack(side=tk.LEFT, padx=2)
    ttk.Button(bar, text="复制译文", command=lambda: _copy_tran()).pack(side=tk.LEFT, padx=2)
    ttk.Button(bar, text="清空历史", command=lambda: _clear()).pack(side=tk.RIGHT)
    status = ttk.Label(bar, text="", foreground=theme.TEXT_MUTED)
    status.pack(side=tk.LEFT)

    _records = []          # 当前列表对应的记录（dict）

    def _set_status(text: str) -> None:
        try:
            status.config(text=text)
        except Exception:
            pass

    def _show_detail(rec: dict) -> None:
        detail.config(state=tk.NORMAL)
        detail.delete("1.0", tk.END)
        detail.insert("1.0",
                      f"【原文】\n{rec.get('ocr', '') or '（无）'}\n\n"
                      f"【译文】\n{rec.get('translate', '') or '（无）'}\n\n"
                      f"时间：{rec.get('time', '')}")
        detail.config(state=tk.DISABLED)

    def _load() -> None:
        """后台读取 + 过滤 → 回写列表（不阻塞 UI）。"""
        query = q.get().strip()
        _set_status("加载中…")

        def _work():
            try:
                rows = list(reversed(store.load_records() or []))   # 新 → 旧
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"读取失败: {e}"))
                return
            if query:
                rows = [r for r in rows
                        if query in (r.get("ocr", "") or "")
                        or query in (r.get("translate", "") or "")]
            _post_to_ui(app, lambda: _fill(rows, query))

        threading.Thread(target=_work, daemon=True, name="hist-load").start()

    def _fill(rows, query: str) -> None:
        for it in tree.get_children():
            tree.delete(it)
        _records[:] = rows
        for i, r in enumerate(_records):
            snippet = (r.get("ocr", "") or "").replace("\n", " ").strip()
            if len(snippet) > 60:
                snippet = snippet[:60] + "…"
            tree.insert("", tk.END, iid=str(i), values=(r.get("time", ""), snippet))
        _set_status(f"共 {len(_records)} 条" + (f"（检索词：{query}）" if query else ""))

    def _selected_rec():
        sel = tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        return _records[idx] if 0 <= idx < len(_records) else None

    def _copy(field: str) -> None:
        rec = _selected_rec()
        if rec is None:
            _set_status("请先选择一条记录")
            return
        text = rec.get(field, "") or ""
        if not text.strip():
            _set_status("该项为空")
            return
        try:
            win.clipboard_clear()
            win.clipboard_append(text)
            win.update()
            label = "原文" if field == "ocr" else "译文"
            _set_status(f"已复制{label}（{len(text)} 字）")
        except Exception as e:
            _set_status(f"复制失败: {e}")

    def _copy_orig() -> None:
        _copy("ocr")

    def _copy_tran() -> None:
        _copy("translate")

    def _clear() -> None:
        if not messagebox.askyesno("清空历史", "确定清空全部历史记录吗？此操作不可恢复。",
                                   parent=win):
            return
        try:
            store.clear()
            _set_status("已清空历史")
        except Exception as e:
            _set_status(f"清空失败: {e}")
        _load()

    def _export(fmt: str) -> None:
        from tkinter import filedialog
        from ..services.persistence.json_history import export_markdown, export_text
        path = filedialog.asksaveasfilename(
            parent=win, title=f"导出历史（{fmt.upper()}）",
            defaultextension=f".{fmt}",
            filetypes=[("Markdown", "*.md")] if fmt == "md" else [("Text", "*.txt")])
        if not path:
            return
        _set_status("导出中…")

        def _work():
            try:
                rows = store.load_records() or []
                if fmt == "md":
                    export_markdown(rows, path)
                else:
                    export_text(rows, path)
            except Exception as e:
                _post_to_ui(app, lambda: _set_status(f"导出失败: {e}"))
                return
            _post_to_ui(app, lambda: _set_status(f"已导出 {len(rows)} 条 → {path}"))

        threading.Thread(target=_work, daemon=True, name="hist-export").start()

    tree.bind("<<TreeviewSelect>>",
              lambda e: _show_detail(_records[int(tree.selection()[0])])
              if tree.selection() else None)

    _load()
    _center(win, window.root)


# ======================================================================
# 插件管理（浏览 / 启用 / 禁用，黑名单写回 config）
# ======================================================================
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


# ======================================================================
# 首次运行向导（P2-1）：检测到未配置 API Key 时弹一次，引导填好能用
# ======================================================================
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
