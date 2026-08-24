# -*- coding: utf-8 -*-
"""视觉常量集中地（多主题注册表）。

3.0 把颜色字面量全部收拢到这里：换肤只改这一个文件（或设置页）。
3.1 起支持「多主题 + 浅/深模式 + 自定义配色覆盖」：
  - 4 套主色（蓝/绿/紫/橙）× 浅/深 = 8 套配色，对齐丢失的旧版界面；
  - 自定义配色覆盖来自 config.ui.theme_colors（角色名 → #RRGGBB）；
  - 运行时切主题后调 TkUi.reload_ui() 用当前调色板重建窗口即可生效。

活动调色板存于模块级 ACTIVE；所有部件构建时读 theme.ACCENT 等名字，
它们由 apply_active() 在切主题时同步刷新，所以「构建期引用」永远拿到当前值。
"""
from __future__ import annotations

import tkinter as tk

# ---- 调色板角色（所有界面共用的语义槽位）----
TOKENS = (
    "accent", "accent_hover", "accent_disabled", "accent_subtitle",
    "canvas_bg", "card_bg", "input_bg", "input_border", "status_bg",
    "text_main", "text_muted", "text_hint", "border",
    "user_bg", "user_fg", "assist_bg", "assist_fg", "assist_border",
    "system_bg", "system_fg", "system_border", "danger",
)

# 4 套主色族： (主色, hover, 按下) —— 蓝=Manggo/SnowShot 默认
_ACCENTS = {
    "blue":   ("#1677ff", "#4096ff", "#0958d9"),
    "green":  ("#13c2a2", "#36cfc9", "#0b8f78"),
    "purple": ("#7c5cff", "#9d86ff", "#5a3fd6"),
    "orange": ("#fa8c16", "#ffa940", "#d9700a"),
}
THEME_FAMILIES = tuple(_ACCENTS.keys())     # ("blue","green","purple","orange")
THEME_FAMILY_LABELS = {
    "blue": "蓝 (Manggo)", "green": "绿", "purple": "紫", "orange": "橙",
}


def _light(accent, hover, pressed) -> dict:
    return {
        "accent": accent, "accent_hover": hover, "accent_disabled": "#d9d9d9",
        "accent_subtitle": "#dbe7ff",
        "canvas_bg": "#f5f5f5", "card_bg": "#ffffff", "input_bg": "#ffffff",
        "input_border": "#d9d9d9", "status_bg": "#fafafa",
        "text_main": "#1f2329", "text_muted": "#8c8c8c", "text_hint": "#bfbfbf",
        "border": "#dbdbdb",
        "user_bg": accent, "user_fg": "#ffffff",
        "assist_bg": "#ffffff", "assist_fg": "#1f2329", "assist_border": "#d6e4ff",
        "system_bg": "#fff8e1", "system_fg": "#8a6d1a", "system_border": "#f0e2b0",
        "danger": "#ff4d4f",
    }


def _dark(accent, hover, pressed) -> dict:
    return {
        "accent": accent, "accent_hover": hover, "accent_disabled": "#3a3c44",
        "accent_subtitle": "#d6e4ff",
        "canvas_bg": "#141414", "card_bg": "#26272e", "input_bg": "#2a2a2a",
        "input_border": "#434343", "status_bg": "#1f1f1f",
        "text_main": "#e8eaed", "text_muted": "#8c8c8c", "text_hint": "#5a5a5a",
        "border": "#3a3c44",
        "user_bg": accent, "user_fg": "#ffffff",
        "assist_bg": "#1f1f1f", "assist_fg": "#e8eaed", "assist_border": "#303030",
        "system_bg": "#2a2410", "system_fg": "#d4b65a", "system_border": "#4a4420",
        "danger": "#ff7875",
    }


def _base_palette(family: str, mode: str) -> dict:
    """返回某主色族 + 模式的基础调色板（未合并自定义）。"""
    accent, hover, pressed = _ACCENTS.get(family, _ACCENTS["blue"])
    return _dark(accent, hover, pressed) if mode == "dark" else _light(accent, hover, pressed)


# ---- 活动调色板（模块级，apply_active 写入）----
ACTIVE = _base_palette("blue", "light")
_custom: dict = {}               # 来自 config.ui.theme_colors 的覆盖
_family = "blue"
_mode = "light"


def parse_theme(value: str) -> tuple[str, str]:
    """把 config.ui.theme 解析成 (family, mode)。

    兼容旧值："light"/"dark" → 当作 blue 族的同名模式；
    新值形如 "blue:dark" / "green:light"。
    """
    value = (value or "blue:light").strip().lower()
    if ":" in value:
        fam, mod = value.split(":", 1)
        return (fam if fam in _ACCENTS else "blue", mod if mod in ("light", "dark") else "light")
    if value in ("light", "dark"):
        return ("blue", value)
    return (value if value in _ACCENTS else "blue", "light")


def set_active(family: str, mode: str, custom: dict = None) -> None:
    """切换活动主题：重算并刷新所有模块级名字。"""
    global ACTIVE, _custom, _family, _mode
    _family, _mode = (family if family in _ACCENTS else "blue"), (
        mode if mode in ("light", "dark") else "light")
    _custom = {k: v for k, v in (custom or {}).items() if k in TOKENS}
    ACTIVE = _base_palette(_family, _mode)
    ACTIVE.update(_custom)
    apply_active()


# ---- 气泡配色：从 ACTIVE 派生 ----
def bubble(role: str) -> dict:
    table = {
        "user": ("user_bg", "user_fg", "user_bg"),
        "assistant": ("assist_bg", "assist_fg", "assist_border"),
        "system": ("system_bg", "system_fg", "system_border"),
    }
    bg_k, fg_k, bd_k = table.get(role, table["system"])
    return {"bg": ACTIVE[bg_k], "fg": ACTIVE[fg_k], "border": ACTIVE[bd_k]}


def apply_active() -> None:
    """把 ACTIVE 同步进模块级名字，供 `theme.ACCENT` 这类引用取当前值。"""
    for tok in TOKENS:
        globals()[tok.upper()] = ACTIVE.get(tok, "")
    # 气泡配色依赖 ACTIVE，切主题后一并刷新（构建期引用才能拿到新值）
    globals()["BUBBLE"] = {
        "user": bubble("user"),
        "assistant": bubble("assistant"),
        "system": bubble("system"),
    }


def current_theme() -> str:
    return f"{_family}:{_mode}"


def current_family() -> str:
    return _family


def current_mode() -> str:
    return _mode


def custom_overrides() -> dict:
    return dict(_custom)


def reset_custom() -> None:
    global _custom
    _custom = {}
    set_active(_family, _mode)


# 初始即是一份可用的浅色蓝主题（构建期引用不会拿到空串）
apply_active()


# ---- 字体 ----
# 全部字号从 BASE_FONT_SIZE 派生，改一个值整套界面同步缩放
# （config.ui.font_size 就接在这里；老默认 10pt 对高分屏偏小）。
FONT_FAMILY = "Microsoft YaHei"
MONO_FAMILY = "Consolas"
BASE_FONT_SIZE = 10


def set_font_size(size: int) -> None:
    """按基准字号重算所有字体常量。必须在建控件之前调用。

    偏移量固定：small = base-2、title = base+1，
    这样放大时层级关系不会被压平（都变成一样大就没有主次了）。
    """
    global BASE_FONT_SIZE, UI_FONT, UI_FONT_BOLD, UI_FONT_SMALL
    global TITLE_FONT, MONO_FONT
    try:
        size = int(size)
    except Exception:
        size = 10
    BASE_FONT_SIZE = max(8, min(18, size))
    b = BASE_FONT_SIZE
    UI_FONT = (FONT_FAMILY, b)
    UI_FONT_BOLD = (FONT_FAMILY, b, "bold")
    UI_FONT_SMALL = (FONT_FAMILY, max(7, b - 2))
    TITLE_FONT = (FONT_FAMILY, b + 1, "bold")
    MONO_FONT = (MONO_FAMILY, b)


UI_FONT = (FONT_FAMILY, 10)
UI_FONT_BOLD = (FONT_FAMILY, 10, "bold")
UI_FONT_SMALL = (FONT_FAMILY, 8)
TITLE_FONT = (FONT_FAMILY, 11, "bold")
MONO_FONT = (MONO_FAMILY, 10)

# ---- 尺寸 ----
BUBBLE_PAD = 10
BUBBLE_MARGIN = 8
TEXT_AREA_MIN_H = 180       # 原文/译文区最小高度
CHAT_PANEL_MIN_H = 260      # 对话面板最小高度


def accent_button(parent, text, command, **kw):
    """统一风格的主按钮（tk.Button，ttk 无法直接染色）。"""
    opts = dict(bg=ACCENT, fg="white", activebackground=ACCENT_HOVER,
                activeforeground="white", relief=tk.FLAT, bd=0,
                font=UI_FONT_BOLD, padx=10, cursor="hand2")
    opts.update(kw)
    return tk.Button(parent, text=text, command=command, **opts)


def set_button_enabled(btn, enabled: bool) -> None:
    try:
        btn.config(state=tk.NORMAL if enabled else tk.DISABLED,
                   bg=ACCENT if enabled else ACCENT_DISABLED,
                   cursor="hand2" if enabled else "arrow")
    except Exception:
        pass
