# -*- coding: utf-8 -*-
"""设置对话框：热键 / API 与引擎 / 连接测试。

从 dialogs.py 拆分而来。
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from ...services.hotkey import ACTIONS
from ...services.translate.glm import GlmEngine
from .dialogs_common import (
    _post_to_ui, _center, _modal, _ScrollableFrame,
    _record_key, _hotkey_status,
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
            var.set(getattr(d, action))
        enabled.set(d.enabled)

    bar = ttk.Frame(win, padding=(12, 8))
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    theme.accent_button(bar, "保存并生效", _save).pack(side=tk.RIGHT)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)
    ttk.Button(bar, text="恢复默认", command=_restore).pack(side=tk.LEFT)

    _center(win, root)


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


