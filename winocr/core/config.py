# -*- coding: utf-8 -*-
"""单一配置模型 — 取代 WinOCR2.0 四散的配置机制。

旧版问题：config_local.py（可执行的 Python！）、config_v6.yaml、api_settings.py、
hotkey_settings.py 四套机制并存，字段分散、无类型约束、互相覆盖顺序全靠记忆。
更糟的是 config_local.py 会被 exec 执行 —— 配置文件拥有任意代码执行权限。

3.0：一个强 schema 的 AppConfig，从单一 TOML 加载（纯数据，不可执行），
全程类型化，UI 与 CLI 共用，改一处即可。缺字段自动回落默认值，永不因配置崩溃。

3.4（功能视角，无平台账号）：每个功能（AI 对话 / 大模型翻译 / 云端视觉 OCR）
直接在自己页里持有完整的一套连接参数（地址 / 密钥 / 模型 / 采样 / 限流），
不再有「平台账号」中间层，也不再按连接名引用。旧配置的散落字段在加载时
直接迁移进各功能自己的字段，无需用户手改。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import os
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional


# ======================================================================
# 连接参数 —— 每个功能直接持有自己的一套（无平台账号层）
# ======================================================================
@dataclass
class ConnectableConfig:
    """一个 OpenAI 兼容接口的完整参数，直接挂在功能配置上。

    每个功能（AI 对话 / 大模型翻译 / 云端视觉 OCR）在自己页里填这一套，
    不再有「平台账号」中间层，也不再按连接名引用。

    字段语义：
    - ``base_url`` / ``api_key`` / ``text_model`` / ``vision_model`` 留空 → 引擎内置默认。
    - 文本任务（AI 文本对话 / 大模型翻译）用 ``text_model``；
      视觉任务（AI 带图对话 / 云端视觉 OCR）用 ``vision_model``。
    - 采样 / 限流参数（temperature / top_p / 重试 / 超时…）AI 对话与翻译通用；
      视觉侧另有 ``vision_temperature`` / ``vision_top_p`` / ``vision_max_output_tokens``
      与文本侧解耦：``0`` = 跟随文本侧同名字段；``<0`` = **不发送**该参数
      （推理类视觉模型如 GLM-4.1V-9B-Thinking 常不支持 temperature/top_p，
      发默认 0.7 会直接 400 报错）；``>0`` = 视觉专属值。
    """

    base_url: str = ""
    api_key: str = ""
    text_model: str = ""
    vision_model: str = ""
    # --- 限流 / 上下文保护 ---
    max_output_tokens: int = 2048
    max_context_tokens: int = 32768
    max_turns: int = 12
    retry_attempts: int = 3
    retry_backoff: float = 1.5
    timeout: int = 60
    temperature: float = 0.7
    top_p: float = 0.9
    # --- 视觉模型专用参数（与文本侧解耦；<0 表示不发送，避免推理模型 400）---
    vision_temperature: float = 0.0
    vision_top_p: float = 0.0
    vision_max_output_tokens: int = 0


# ======================================================================
# 功能配置 —— 每个功能自带一套连接参数（继承 ConnectableConfig）
# ======================================================================
@dataclass
class OcrConfig(ConnectableConfig):
    engine: str = "rapidocr"  # rapidocr(本地) / vision_ocr(云端视觉模型)
    preprocess: bool = True  # 小图放大/对比度增强（代码截图智能保留彩色）
    model_type: str = "tiny"  # tiny / small / medium（本地档位）
    # --- 结构化输出（几何重建表格/版面，离线零额外模型）---
    structured: bool = False  # True = 用包围框把零散文字拼回 Markdown 表格
    # --- 模型档位由用户在设置里手动选（tiny / small / medium）---
    # 原本的「低置信度自动升档重试」已移除：升档要换档重建引擎（模型重新加载），
    # 实测让一次识别从 ~0.9s 涨到 ~3.8s，慢 4 倍，且常常白升（置信度已 0.99）。
    # 想要更高质量请直接在设置里把档位调高，引擎只建一次，不付重建代价。
    # --- 段落/行判定模式（A / B / A+B，供对比测试）---
    # "A"   = 仅数据驱动自适应行分组（修阅读顺序，不分段，纯 lines）
    # "B"   = 仅四角几何段落判定（行分组退回旧固定阈值，隔离 B 的效果）
    # "A+B" = 自适应行分组 + 四角几何段落（推荐，默认）
    paragraph_mode: str = "A+B"


@dataclass
class TranslateConfig(ConnectableConfig):
    engine: str = "auto"  # auto=回退链 / 指定单引擎
    target: str = "en"  # 默认译向（中→英）
    auto_translate: bool = True  # OCR 完成后自动翻译
    offline_mode: bool = False  # 离线优先：auto 回退链只走 online=False 引擎
    # auto 回退链默认顺序：在线引擎在前（配好 Key 才 available），
    # argos 本地离线**兜底放最后**——否则离线永远成功、在线 API 永不执行
    # （用户实测：配了 GLM Key 却一直是 argos 差译文，2026-09-02 定位）。
    fallback_order: List[str] = field(
        default_factory=lambda: ["glm", "hunyuan", "mymemory", "llama_cpp", "argos"]
    )


@dataclass
class AiConfig(ConnectableConfig):
    provider: str = "glm"  # AI 提供方：glm / openai_compat / llama_cpp（本地离线）
    # 连接参数在本配置内直接持有（base_url/api_key/text_model 等）
    # llama_cpp 提供方：text_model 填 GGUF 文件名，api_key/base_url 忽略


@dataclass
class HotkeyConfig:
    """全局热键（P5 起：逐场景字段删除，只留 enabled + quit + overrides）。

    默认组合键由胶囊声明（capsule.hotkey），这里只记录用户覆盖。
    """

    enabled: bool = True
    quit: str = "ctrl+shift+q"
    overrides: dict = field(default_factory=dict)  # 胶囊名 → 用户自定义组合键

    def resolved(self, action: str, default: str = "") -> str:
        """某个动作最终生效的组合键：用户覆盖 > 胶囊声明默认。"""
        return (self.overrides or {}).get(action) or default or ""


@dataclass
class ProjectConfig:
    """真·项目栏里的一个项目（标签页）。

    - ``id``：稳定标识（"default" 保留给默认项目），不随改名变化；
    - ``name``：标签页显示名，随时可改；
    - ``open``：是否在标签栏展开（关闭标签 = open=False，数据全留）；
    - ``translate_target``：译向覆盖，空串 = 用全局 config.translate.target。
    """

    id: str = "default"
    name: str = "默认"
    open: bool = True
    translate_target: str = ""


@dataclass
class UiConfig:
    mode: str = "simple"  # simple / advanced
    theme: str = "light"
    font_size: int = 11  # 默认 11pt（旧 10 偏小，多数用户看不清）
    # 3.4.3 起「自动划词」与 Alt+右键 钩子监听已取消，仅保留 Ctrl+Shift+D 按键触发。
    # 以下两个字段仅作旧配置兼容保留，不再被 UI 使用。
    selection_enabled: bool = True  # （已废弃）划词总开关
    selection_auto: bool = False  # （已废弃）选中即翻译
    window_size: str = "820x600"
    theme_colors: dict = field(
        default_factory=dict
    )  # 自定义界面配色：{token: {role: hex}}


@dataclass
class TtsConfig:
    """朗读（TTS）。两级引擎：edge（在线 Neural，音质接近真人）→ sapi（系统离线）。

    engine="auto" 时先试 edge，失败（无网/被墙/超时）自动降级 sapi，
    所以断网也能读出声，不会「点了没反应」。
    """

    engine: str = "auto"  # auto / edge / sapi
    voice: str = "zh-CN-XiaoxiaoNeural"  # edge 音色；sapi 时忽略
    rate: int = 0  # 语速增量，百分比：-50 ~ +100
    volume: int = 0  # 音量增量，百分比：-50 ~ +50
    auto_read: bool = False  # 翻译完成后自动朗读译文


@dataclass
class PluginConfig:
    """插件启用控制（3.4 改进蓝图 P1-1）。

    blacklist 里出现的插件名（按各轴 .name 匹配）在启动装配时被跳过，
    其余全部正常发现。留空 = 全部启用（默认）。实例在 build() 时装配，
    改动需重启程序后完全生效。
    """

    blacklist: List[str] = field(default_factory=list)


@dataclass
class LoggingConfig:
    """日志系统配置（3.4.21）。

    优先级：环境变量 WINOCR_LOG_LEVEL / WINOCR_LOG_DIR > 本配置 > 默认值。
    """

    level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR
    dir: str = ""  # 空 = 默认（user_dir()/logs）
    console: bool = True  # 是否同时输出到控制台（pythonw 自动跳过）


@dataclass
class AppConfig:
    ocr: OcrConfig = field(default_factory=OcrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    ai: AiConfig = field(default_factory=AiConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    plugin: PluginConfig = field(default_factory=PluginConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # 真·项目栏（3.4 MVP）：项目注册表 + 当前项目。默认含一个不可关的「默认」项目。
    projects: List[ProjectConfig] = field(default_factory=lambda: [ProjectConfig()])
    current_project: str = "default"

    # 运行期记录来源文件，save() 无参即可回写
    _source_path: Optional[str] = field(default=None, repr=False, compare=False)

    # ---------------- 构造 ----------------
    @classmethod
    def defaults(cls) -> "AppConfig":
        return cls()

    def sections(self):
        # projects 是 dataclass 列表，写成根级数组 projects = [...]，
        # 必须排在任意 [section] 表头之前（TOML 里表头后的裸键会归属该表）。
        return [
            ("projects", self.projects),
            ("ocr", self.ocr),
            ("translate", self.translate),
            ("ai", self.ai),
            ("hotkey", self.hotkey),
            ("ui", self.ui),
            ("tts", self.tts),
            ("plugin", self.plugin),
            ("logging", self.logging),
        ]

    def to_dict(self) -> dict:
        d = {name: _asdict_deep(obj) for name, obj in self.sections()}
        d["current_project"] = self.current_project
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        """按字段名安全构造：未知键忽略、类型不符自动纠正，坏配置不会让程序崩溃。"""

        def build(dc_cls, raw):
            raw = raw or {}
            kwargs = {}
            for f in fields(dc_cls):
                if f.name not in raw:
                    continue
                val = raw[f.name]
                kwargs[f.name] = _coerce(val, f.type)
            return dc_cls(**kwargs)

        cfg = cls(
            ocr=build(OcrConfig, d.get("ocr")),
            translate=build(TranslateConfig, d.get("translate")),
            ai=build(AiConfig, d.get("ai")),
            hotkey=build(HotkeyConfig, d.get("hotkey")),
            ui=build(UiConfig, d.get("ui")),
            tts=build(TtsConfig, d.get("tts")),
            plugin=build(PluginConfig, d.get("plugin")),
            logging=build(LoggingConfig, d.get("logging")),
        )
        cfg._apply_legacy_migration(d)
        # 真·项目栏：解析 projects 列表 + current_project（强制保证 default 存在、current 合法）
        raw_projects = d.get("projects")
        cur = d.get("current_project")
        if isinstance(cur, str):
            cur = cur.strip()
        cfg.projects, cfg.current_project = _coerce_projects(raw_projects, cur)
        return cfg

    # ---------------- 旧配置自动迁移（3.0 → 3.4） ----------------
    def _apply_legacy_migration(self, d: dict) -> None:
        """把 3.0 散落的 API 字段（glm_api_key / cloud_* / 翻译 glm_* 等）直接落进
        各功能配置自己的字段。只补空缺：新格式已显式写的字段一律优先。

        3.4 起不再有「平台账号 / 连接」概念，所以旧字段不再打包成连接，
        而是按用途直接并入 AI 对话 / 翻译 / OCR 三个功能的连接参数。
        """
        ai_raw = d.get("ai") or {}
        tr_raw = d.get("translate") or {}
        oc_raw = d.get("ocr") or {}

        # --- AI 对话（文本 + 视觉同源）---
        if _s(ai_raw.get("glm_api_key")) and not self.ai.api_key:
            self.ai.api_key = _s(ai_raw.get("glm_api_key"))
        if _s(ai_raw.get("glm_vision_api_key")) and not self.ai.api_key:
            self.ai.api_key = _s(ai_raw.get("glm_vision_api_key"))
        if _s(ai_raw.get("hunyuan_api_key")) and not self.ai.api_key:
            self.ai.api_key = _s(ai_raw.get("hunyuan_api_key"))
        if _s(ai_raw.get("glm_base_url")) and not self.ai.base_url:
            self.ai.base_url = _s(ai_raw.get("glm_base_url"))
        if _s(ai_raw.get("glm_vision_base_url")) and not self.ai.base_url:
            self.ai.base_url = _s(ai_raw.get("glm_vision_base_url"))
        if _s(ai_raw.get("glm_text_model")) and not self.ai.text_model:
            self.ai.text_model = _s(ai_raw.get("glm_text_model"))
        if _s(ai_raw.get("glm_vision_model")) and not self.ai.vision_model:
            self.ai.vision_model = _s(ai_raw.get("glm_vision_model"))
        for attr, raw_key, default in (
            ("max_output_tokens", "max_output_tokens", 2048),
            ("max_context_tokens", "max_context_tokens", 32768),
            ("max_turns", "max_turns", 12),
            ("retry_attempts", "retry_attempts", 3),
            ("timeout", "timeout", 60),
        ):
            v = _i_opt(ai_raw.get(raw_key), default)
            if v is not None and not getattr(self.ai, attr):
                setattr(self.ai, attr, v)
        for attr, raw_key, default in (
            ("retry_backoff", "retry_backoff", 1.5),
            ("temperature", "temperature", 0.7),
            ("top_p", "top_p", 0.9),
        ):
            v = _f_opt(ai_raw.get(raw_key), default)
            if v is not None and not getattr(self.ai, attr):
                setattr(self.ai, attr, v)

        # --- 大模型翻译（用本功能自己的连接参数）---
        if _s(tr_raw.get("glm_api_key")) and not self.translate.api_key:
            self.translate.api_key = _s(tr_raw.get("glm_api_key"))
        if _s(tr_raw.get("glm_base_url")) and not self.translate.base_url:
            self.translate.base_url = _s(tr_raw.get("glm_base_url"))
        if _s(tr_raw.get("glm_model")) and not self.translate.text_model:
            self.translate.text_model = _s(tr_raw.get("glm_model"))

        # --- 云端视觉 OCR（用本功能自己的连接参数）---
        if _s(oc_raw.get("cloud_api_key")) and not self.ocr.api_key:
            self.ocr.api_key = _s(oc_raw.get("cloud_api_key"))
        if _s(oc_raw.get("cloud_base_url")) and not self.ocr.base_url:
            self.ocr.base_url = _s(oc_raw.get("cloud_base_url"))
        if _s(oc_raw.get("cloud_model")) and not self.ocr.vision_model:
            self.ocr.vision_model = _s(oc_raw.get("cloud_model"))
        for attr, raw_key, default in (
            ("max_output_tokens", "cloud_max_output_tokens", 4096),
            ("retry_attempts", "cloud_retry_attempts", 3),
            ("timeout", "cloud_timeout", 60),
        ):
            v = _i_opt(oc_raw.get(raw_key), default)
            if v is not None and not getattr(self.ocr, attr):
                setattr(self.ocr, attr, v)
        v = _f_opt(oc_raw.get("cloud_retry_backoff"), 1.5)
        if v is not None and not self.ocr.retry_backoff:
            self.ocr.retry_backoff = v

        # --- 热键：3.1 的逐场景字段 → 迁移进 overrides（P5 后删除逐场景字段）---
        hk_raw = d.get("hotkey") or {}
        legacy_hk = {
            "snap_extract": "snap_translate",
            "extract": "clipboard_extract",
            "translate": "translate_text",
            "switch_engine": "cycle_engine",
        }
        if any(k in hk_raw for k in legacy_hk):
            ov = dict(self.hotkey.overrides or {})
            for old, new in legacy_hk.items():
                val = _s(hk_raw.get(old))
                if val and not _s(ov.get(new)):
                    ov[new] = val
            self.hotkey.overrides = ov

    # ---------------- 加载 ----------------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        from .paths import config_path

        p = path or str(config_path())
        if not os.path.isfile(p):
            cfg = cls.defaults()
            cfg._source_path = p
            return cfg
        try:
            raw = _read_toml(p)
            cfg = cls.from_dict(raw)
        except Exception as e:
            logger.warning("[配置] 解析失败，使用默认值：%s", e)
            cfg = cls.defaults()
        cfg._source_path = p
        cfg._apply_env_overrides()
        return cfg

    def _apply_env_overrides(self) -> None:
        """环境变量覆盖（CI / 临时调试友好，且 API Key 不必落盘）。"""
        env_key = os.environ.get("GLM_API_KEY") or os.environ.get("WINOCR_GLM_API_KEY")
        if env_key:
            self.ai.api_key = env_key.strip()
        mt = os.environ.get("WINOCR_MODEL_TYPE")
        if mt:
            self.ocr.model_type = mt.strip()

    # ---------------- 保存 ----------------
    def save(self, path: Optional[str] = None) -> str:
        """写出可读 TOML。字面量严格按 TOML 规范（bool 小写、数字裸写）。"""
        from .paths import config_path

        p = path or self._source_path or str(config_path())
        lines = ["# WinOCR 3.0 配置文件（纯数据，程序不会执行它）", ""]
        # 根级标量（current_project）必须写在任何 [section] 表头之前，
        # 否则会被 TOML 当成某个表的子键而丢失。
        lines.append(f"current_project = {_toml_literal(self.current_project)}")
        lines.append("")
        for name, obj in self.sections():
            # projects 是 dataclass 列表，写成顶层数组 projects = [{...}, ...]，
            # 不用 [projects] 表头（否则会变成 projects.projects 嵌套，丑且易错）。
            if isinstance(obj, list):
                lines.append(f"{name} = {_toml_literal(obj)}")
                lines.append("")
                continue
            lines.append(f"[{name}]")
            for k, v in asdict(obj).items():
                lines.append(f"{k} = {_toml_literal(v)}")
            lines.append("")
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self._source_path = p
        return p


# ---------------- 工具函数 ----------------


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _i(v: Any, default: int) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _f(v: Any, default: float) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _i_opt(v: Any, default: int) -> Optional[int]:
    """旧配置没写数字字段 → None（迁移时跳过该项，保留目标连接现有值）。"""
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _f_opt(v: Any, default: float) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _asdict_deep(obj):
    """to_dict 用：dataclass 实例 → dict；列表（如 projects 注册表）逐元素递归转换。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, list):
        return [_asdict_deep(x) for x in obj]
    return obj


def _toml_literal(v: Any) -> str:
    if is_dataclass(v) and not isinstance(v, type):
        v = asdict(v)  # dataclass 实例（如 ProjectConfig）先转 dict
    if isinstance(v, bool):
        return "true" if v else "false"  # 关键：不能写成 Python 的 True
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        # 写成 TOML inline table（键均为裸标识符：胶囊名/动作名）
        inner = ", ".join(f"{k} = {_toml_literal(x)}" for k, x in v.items())
        return "{" + inner + "}"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _coerce(val: Any, type_hint: Any) -> Any:
    """把 TOML 读出来的值纠正为字段声明的类型（字符串化的 true/1 也能吃下）。"""
    hint = str(type_hint)
    if "bool" in hint:
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "yes", "on")
    if "int" in hint and "List" not in hint:
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0
    if "float" in hint and "List" not in hint:
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
    if "List" in hint or "list" in hint:
        if isinstance(val, list):
            return [str(x) for x in val]
        return [s.strip() for s in str(val).strip("[]").split(",") if s.strip()]
    if "dict" in hint or "Dict" in hint:
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            # 自愈：历史上被误存成字符串的 dict（如 hotkey.overrides）
            try:
                import ast

                parsed = ast.literal_eval(val)
                return parsed if isinstance(parsed, dict) else {}
            except Exception as e:
                logger.debug("hotkey.overrides 解析失败，回退空 dict：%s", e)
                return {}
        return {}
    return str(val)


def _coerce_projects(raw: Any, current_id: Optional[str]):
    """把 TOML 读出的 projects 列表纠正为 (List[ProjectConfig], current_id)。

    - 空/非法 → 回退单个默认项目；
    - 每个项目强制有唯一 id（冲突/缺失自动补）；
    - 强制保证存在一个 id=="default" 项目（不可关、不可删）；
    - current_project 必须指向存在的项目，否则落到第一个 open 或 default。
    """
    if not isinstance(raw, list) or not raw:
        return [ProjectConfig()], "default"
    projects = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        if not pid or pid in seen:
            pid = _new_project_id(seen)
        seen.add(pid)
        name = str(item.get("name") or "").strip() or "未命名"
        open_flag = item.get("open", True)
        if not isinstance(open_flag, bool):
            open_flag = str(open_flag).strip().lower() in ("true", "1", "yes", "on")
        target = str(item.get("translate_target") or "").strip()
        projects.append(
            ProjectConfig(id=pid, name=name, open=open_flag, translate_target=target)
        )
    if not projects:
        return [ProjectConfig()], "default"
    if not any(p.id == "default" for p in projects):  # default 必须存在
        projects.insert(0, ProjectConfig())
    ids = {p.id for p in projects}
    if not current_id or current_id not in ids:
        first_open = next((p.id for p in projects if p.open), None)
        current_id = first_open or "default"
    return projects, current_id


def _new_project_id(seen: set) -> str:
    for _ in range(20):
        pid = uuid.uuid4().hex[:8]
        if pid not in seen:
            return pid
    return uuid.uuid4().hex


def _read_toml(path: str) -> dict:
    try:
        import tomllib  # Python 3.11+

        with open(path, "rb") as f:
            return tomllib.load(f)
    except ModuleNotFoundError:
        return _read_minimal_toml(path)


def _read_minimal_toml(path: str) -> dict:
    """<3.11 回退：浅层 [section] + 标量/数组，支持 [a.b] 嵌套表，覆盖本配置足矣。"""
    import re

    out: Dict[str, Any] = {}

    def _ensure_table(d: dict, parts: list) -> dict:
        node = d
        for part in parts:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        return node

    with open(path, "r", encoding="utf-8") as f:
        section: Optional[list] = None
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^\[(.+)\]$", line)
            if m:
                section = [s.strip() for s in m.group(1).split(".")]
                _ensure_table(out, section)
                continue
            if "=" in line and section:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v.startswith("[") and v.endswith("]"):
                    parsed: Any = [
                        x.strip().strip('"').strip("'")
                        for x in v[1:-1].split(",")
                        if x.strip()
                    ]
                elif v.lower() in ("true", "false"):
                    parsed = v.lower() == "true"
                elif re.fullmatch(r"-?\d+", v):
                    parsed = int(v)
                else:
                    parsed = v.strip('"').strip("'")
                node = _ensure_table(out, section[:-1])
                node[section[-1]][k] = parsed
    return out
