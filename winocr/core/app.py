# -*- coding: utf-8 -*-
"""组合根（Composition Root）— 全应用唯一的「接线」处。

WinOCR2.0 的接线散落在 WinOCR.py 顶部 import、translator.set_config()、
run.py、各 dialog 的保存函数里，改一处极易漏一处（配置改了引擎没重载，是常见 bug）。

3.0：App 负责且只负责四件事
  1. 加载单一配置
  2. 用注册表发现各轴插件（含用户 plugins/ 目录）
  3. 实例化并注入配置 —— 依赖注入，全程没有模块级单例
  4. 组装 Pipeline / 事件总线 / UI 适配器
此后任何扩展只需往 services/<轴>/ 丢文件，这里一行都不用改。
"""
from __future__ import annotations

from typing import Dict, Optional

from .config import AppConfig
from .event_bus import EventBus, Events
from .paths import plugins_dir
from .pipeline import Pipeline
from .registry import PluginRegistry, default_registry, register_plugin_dir


class App:
    """应用容器。构造后调用 build() 完成装配。"""

    def __init__(self, config: Optional[AppConfig] = None,
                 registry: Optional[PluginRegistry] = None) -> None:
        self.config = config or AppConfig.load()
        self.registry = registry or default_registry
        self.bus = EventBus()
        self.services: Dict[str, object] = {}
        self.discovered: Dict[str, dict] = {}
        self.pipeline: Optional[Pipeline] = None
        self.hotkeys = None
        self.ui = None
        self.capsules: Dict[str, type] = {}

        # 用户插件目录：丢一个 .py 就能新增引擎 / 胶囊，无需改动本体
        pd = plugins_dir()
        if pd.is_dir():
            register_plugin_dir(str(pd))
            # 第三方胶囊约定：plugins/capsules/*.py（与引擎平铺区分开）
            cap_dir = pd / "capsules"
            if cap_dir.is_dir():
                register_plugin_dir(str(cap_dir))

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------
    def build(self) -> "App":
        from ..services.ai.base import AiProvider
        from ..services.attach.base import AttachParser
        from ..services.capture.base import CaptureSource
        from ..services.ocr.base import OcrEngine
        from ..services.persistence.base import Persistence
        from ..services.translate.base import TranslateEngine
        from ..services.translate.dispatcher import TranslateDispatcher
        from .capsule import discover_capsules

        self.discovered = {
            "ocr": self._discover("ocr", OcrEngine),
            "translate": self._discover("translate", TranslateEngine),
            "ai": self._discover("ai", AiProvider),
            "capture": self._discover("capture", CaptureSource),
            "attach": self._discover("attach", AttachParser),
            "persistence": self._discover("persistence", Persistence),
        }

        # --- 翻译：全部实例化并注入密钥，交给调度器做回退链 ---
        translate_engines = {}
        for name, cls in self.discovered["translate"].items():
            inst = cls()
            inst.set_config(**self._translate_llm_kwargs())
            translate_engines[name] = inst
        self.translate_engines = translate_engines

        # --- OCR：按配置选定，注入预处理开关 ---
        ocr_inst = self._make_ocr()

        # --- AI：按配置选定提供方并注入密钥 ---
        ai_inst = self._make_ai()

        # --- 捕获源：全部实例化，UI 按需取用 ---
        capture_sources = {n: c() for n, c in self.discovered["capture"].items()}

        # --- 附件解析器：全部实例化 ---
        attach_parsers = {n: c() for n, c in self.discovered["attach"].items()}

        # --- 持久化：默认 JSON 历史（按当前项目隔离文件）---
        persistence = None
        pcls = self.discovered["persistence"].get("json_history")
        if pcls:
            persistence = pcls(project_id=self.config.current_project)

        # --- 知识库：独立服务轴，与 json_history 并存（数据资产闭环）---
        # project_getter 动态取当前项目 id（self.projects 在下方装配，lambda 惰性求值）。
        knowledge = None
        kcls = self.discovered["persistence"].get("knowledge")
        if kcls:
            knowledge = kcls(
                project_getter=lambda: (self.projects.current_id
                                        if getattr(self, "projects", None) else "default"))

        # --- 朗读 TTS：无插件轴（只有一个实现，两级引擎内部降级）---
        tts = None
        try:
            from ..services.tts import TtsService
            tts = TtsService(self.config.tts)
        except Exception:
            tts = None

        self.services = {
            "ocr": ocr_inst,
            "translate": TranslateDispatcher(translate_engines, self.config.translate),
            "ai": ai_inst,
            "capture": capture_sources,
            "attach": attach_parsers,
            "persistence": persistence,
            "knowledge": knowledge,
            "tts": tts,
        }
        self.pipeline = Pipeline(self.bus, self.services, self.config.translate)
        # 胶囊注册表：发现 winocr/capsules/ 下所有场景
        self.capsules = discover_capsules()
        # 真·项目栏：ProjectManager 统一管项目注册表 + 跨服务重指（知识/历史/对话/译向）
        from .projects import ProjectManager
        self.projects = ProjectManager(self)
        self.projects.migrate_legacy_history()
        self.projects.sync_translate_target()
        return self

    def _discover(self, axis: str, base):
        found = self.registry.discover(base, f"winocr.services.{axis}")
        # 插件黑名单：用户禁用的插件名（按 .name）启动装配时直接跳过
        blacklist = set(getattr(self.config, "plugin", None).blacklist or []) \
            if getattr(self.config, "plugin", None) else set()
        if blacklist:
            found = {name: cls for name, cls in found.items()
                     if name not in blacklist}
        return found

    # ------------------------------------------------------------------
    def _translate_llm_kwargs(self) -> dict:
        """大模型翻译引擎要注入的接口参数（来自翻译功能自己的连接参数）。

        3.4 起每功能独立持有连接参数，翻译只用自己页里填的那一套，
        不再跟随 / 引用其它功能。混元等其它翻译引擎也用同一套凭证。
        """
        t = self.config.translate
        return {
            "glm_api_key": t.api_key,
            "base_url": t.base_url,
            "model": t.text_model,
            "api_key": t.api_key,   # 混元等其它引擎也用本功能同一套凭证
            "max_output_tokens": t.max_output_tokens,
            "retry_attempts": t.retry_attempts,
            "retry_backoff": t.retry_backoff,
        }

    def effective_translate_llm(self) -> dict:
        """UI 用：把「最终真正生效」的接口参数摊开给用户看，免得靠猜。"""
        kw = self._translate_llm_kwargs()
        return {
            "url": kw["base_url"],
            "model": kw["model"],
            "has_key": bool(kw["glm_api_key"]),
        }

    def _make_ocr(self):
        cls = self.discovered["ocr"].get(self.config.ocr.engine)
        if cls is None:                      # 配置写错了也要能启动
            for name, c in self.discovered["ocr"].items():
                cls = c
                print(f"[App] OCR 引擎 '{self.config.ocr.engine}' 未找到，回退到 '{name}'")
                break
        if cls is None:
            return None
        inst = cls()
        self._configure_ocr(inst)
        return inst

    def _cloud_ocr_kwargs(self) -> dict:
        """云端视觉 OCR 当前生效的接口参数（来自 OCR 功能自己的连接参数）。

        注入与 UI 测试共用：唯一真相来源，避免「测试用的参数」和
        「实际请求用的参数」不一致。视觉采样参数一并取出，供 OCR 复用。
        """
        o = self.config.ocr
        return {
            "api_key": o.api_key,
            "base_url": o.base_url,
            "model": o.vision_model,
            "max_output_tokens": o.max_output_tokens,
            "retry_attempts": o.retry_attempts,
            "retry_backoff": o.retry_backoff,
            "timeout": o.timeout,
            # 视觉采样参数：<0 → 不发送（推理 OCR 模型安全）；0 → 跟随文本侧
            "vision_temperature": o.vision_temperature,
            "vision_top_p": o.vision_top_p,
        }

    def _configure_ocr(self, inst) -> None:
        """注入云端视觉 OCR 参数（来自 OCR 功能自己的连接参数）。"""
        kw = self._cloud_ocr_kwargs()
        o = self.config.ocr
        inst.configure(preprocess=o.preprocess, model_type=o.model_type,
                       structured=o.structured,
                       cloud_api_key=kw["api_key"], cloud_base_url=kw["base_url"],
                       cloud_model=kw["model"],
                       cloud_max_output_tokens=kw["max_output_tokens"],
                       cloud_retry_attempts=kw["retry_attempts"],
                       cloud_retry_backoff=kw["retry_backoff"],
                       cloud_timeout=kw["timeout"],
                       cloud_temperature=kw["vision_temperature"],
                       cloud_top_p=kw["vision_top_p"])

    def _make_ai(self):
        cls = self.discovered["ai"].get(self.config.ai.provider)
        if cls is None:
            return None
        # 对话历史持久化：已落地。提供方从「按项目隔离」的 chat_history_path 读取 /
        # 写入 JSON，重开程序后 AI 面板上下文仍在；不支持持久化的提供方回退到无参构造。
        try:
            from .paths import project_chat_history_path
            inst = cls(history_path=str(
                project_chat_history_path(self.config.current_project)))
        except TypeError:                 # 不支持持久化的提供方
            inst = cls()
        self._configure_ai(inst)
        return inst

    def _configure_ai(self, inst) -> None:
        a = self.config.ai
        # 3.4 起 AI 对话直接持有文本 + 视觉两套参数，无「连接」中间层。
        # 视觉侧与文本侧同在本功能配置内，按独立平台处理（不跨连接继承）。
        inst.set_api_key(a.api_key)
        inst.set_base_url(a.base_url)
        inst.set_models(text_model=a.text_model, vision_model=a.vision_model)
        inst.set_vision_config(base_url=a.base_url, api_key=a.api_key,
                               model=a.vision_model,
                               temperature=a.vision_temperature,
                               top_p=a.vision_top_p,
                               max_output_tokens=a.vision_max_output_tokens,
                               independent=True)
        inst.set_limits(max_output_tokens=a.max_output_tokens,
                        max_context_tokens=a.max_context_tokens,
                        max_turns=a.max_turns,
                        retry_attempts=a.retry_attempts,
                        retry_backoff=a.retry_backoff,
                        timeout=a.timeout,
                        temperature=a.temperature,
                        top_p=a.top_p)

    # ------------------------------------------------------------------
    # 运行期变更（配置改了必须一处生效，杜绝「改了没重载」）
    # ------------------------------------------------------------------
    def apply_config(self, save: bool = True) -> None:
        """把当前 self.config 重新注入所有已实例化的服务。"""
        ai = self.services.get("ai")
        # AI 提供方切换（P2-2）：不同 provider 是不同的类，仅重注入参数不够，
        # 需要按新 provider 重建实例（保留历史文件路径）。
        if ai is not None and self.config.ai.provider != getattr(ai, "name", ""):
            try:
                cls = self.discovered["ai"].get(self.config.ai.provider)
                if cls is not None:
                    from .paths import project_chat_history_path
                    try:
                        ai = cls(history_path=str(
                            project_chat_history_path(self.config.current_project)))
                    except TypeError:
                        ai = cls()
                    self._configure_ai(ai)
                    self.services["ai"] = ai
            except Exception as e:
                print(f"[配置] AI 提供方切换失败，沿用当前：{e}")
        if ai is not None:
            self._configure_ai(ai)

        kw = self._translate_llm_kwargs()
        for eng in getattr(self, "translate_engines", {}).values():
            eng.set_config(**kw)
        disp = self.services.get("translate")
        if disp is not None:
            disp.config = self.config.translate
        ocr = self.services.get("ocr")
        if ocr is not None:
            self._configure_ocr(ocr)
        tts = self.services.get("tts")
        if tts is not None:
            tts.cfg = self.config.tts        # 改音色/语速后立刻生效，无需重启
        if save:
            try:
                self.config.save()
            except Exception as e:
                print(f"[配置] 保存失败: {e}")
        self.bus.publish(Events.CONFIG_CHANGED, self.config)

    def start_hotkeys(self, handlers: dict, defaults: dict = None) -> bool:
        """注册全局热键。handlers: {动作名: 回调}；defaults: {动作名: 默认组合键}
        （来自胶囊声明）。失败不影响程序运行。"""
        from ..services.hotkey import HotkeyService
        self.hotkeys = HotkeyService(self.config.hotkey, self.bus)
        return self.hotkeys.register(handlers, defaults)

    # ------------------------------------------------------------------
    # 自检（doctor）：一眼看清哪一轴缺依赖
    # ------------------------------------------------------------------
    def describe(self) -> dict:
        out = {}
        for axis, classes in self.discovered.items():
            items = []
            for name, cls in classes.items():
                inst = None
                if axis == "translate":
                    inst = self.translate_engines.get(name)
                elif axis == "ocr" and self.services.get("ocr") is not None \
                        and getattr(self.services["ocr"], "name", None) == name:
                    inst = self.services["ocr"]
                elif axis == "ai":
                    inst = self.services.get("ai")
                elif axis == "capture":
                    inst = (self.services.get("capture") or {}).get(name)
                elif axis == "attach":
                    inst = (self.services.get("attach") or {}).get(name)
                ok = None
                if inst is not None and hasattr(inst, "available"):
                    try:
                        ok = bool(inst.available())
                    except Exception:
                        ok = False
                items.append({"name": name,
                              "display": getattr(cls, "display_name", name),
                              "available": ok})
            out[axis] = items
        return out

    # ------------------------------------------------------------------
    def attach_ui(self, ui_adapter) -> None:
        self.ui = ui_adapter
        ui_adapter.bind(self)

    def start(self) -> None:
        if self.ui is not None:
            self.ui.run()
        else:
            print("[App] 未挂载 UI 适配器（无界面模式）")

    def shutdown(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.unregister_all()
        self.bus.clear()
