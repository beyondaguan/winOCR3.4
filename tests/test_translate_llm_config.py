# -*- coding: utf-8 -*-
"""翻译侧大模型接口配置（功能视角，无平台账号）：

每个功能（AI 对话 / 翻译 / OCR）直接持有自己的连接参数，
翻译不再跟随 / 引用 AI 连接，旧版散落字段加载时直接并入各功能字段。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.core import App, AppConfig
from winocr.services.translate.glm import GlmEngine


def _app(**fields):
    cfg = AppConfig()
    for k, v in fields.items():
        axis, _, field = k.partition("__")
        setattr(getattr(cfg, axis), field, v)
    return App(cfg).build()


# ----------------------------------------------------------------------
# 引擎层：空串必须回落默认，而不是保留旧值
# ----------------------------------------------------------------------
def test_engine_defaults():
    e = GlmEngine()
    assert e._client().cfg.base_url == GlmEngine.DEFAULT_URL
    assert e._client().cfg.model == GlmEngine.DEFAULT_MODEL
    assert not e.available()


def test_engine_override_then_reset():
    e = GlmEngine()
    e.set_config(glm_api_key="k", base_url="https://api.siliconflow.cn/v1/chat/completions",
                 model="Qwen/Qwen3-8B")
    assert e._client().cfg.base_url.endswith("siliconflow.cn/v1/chat/completions")
    assert e._client().cfg.model == "Qwen/Qwen3-8B"
    assert e.available()

    # 用户把两个框清空 → 必须回到默认，不能残留上一次的值
    e.set_config(glm_api_key="k", base_url="", model="")
    assert e._client().cfg.base_url == GlmEngine.DEFAULT_URL
    assert e._client().cfg.model == GlmEngine.DEFAULT_MODEL


def test_engine_display_name_tracks_model():
    e = GlmEngine()
    e.set_config(model="glm-4-flash")
    assert "glm-4-flash" in e.display_name


# ----------------------------------------------------------------------
# 装配层：翻译用自己页里的连接参数，与 AI 对话互不影响
# ----------------------------------------------------------------------
def test_translate_uses_own_params():
    app = _app(
        translate__api_key="T-KEY",
        translate__base_url="https://api.siliconflow.cn/v1/chat/completions",
        translate__text_model="Qwen/Qwen3-8B",
        ai__api_key="AI-KEY",
        ai__text_model="glm-4-flash",
    )
    eng = app.translate_engines["glm"]
    assert eng.api_key == "T-KEY"
    assert eng._client().cfg.base_url.endswith("siliconflow.cn/v1/chat/completions")
    assert eng._client().cfg.model == "Qwen/Qwen3-8B"

    eff = app.effective_translate_llm()
    assert eff["has_key"] is True
    assert eff["model"] == "Qwen/Qwen3-8B"


def test_translate_independent_from_ai():
    """翻译改了不会影响 AI 对话，反之亦然。"""
    app = _app(
        translate__api_key="T-KEY",
        translate__text_model="Qwen/Qwen3-8B",
        ai__api_key="AI-KEY",
        ai__text_model="glm-4-flash",
    )
    ai = app.services.get("ai")
    if ai is not None:
        assert ai.text_client.cfg.model == "glm-4-flash"
        assert ai.text_client.cfg.api_key == "AI-KEY"
    assert app.translate_engines["glm"]._client().cfg.model == "Qwen/Qwen3-8B"


def test_apply_config_reinjects_at_runtime():
    """运行期改翻译参数必须立刻生效，且清空能回到默认。"""
    app = _app(translate__api_key="T-KEY",
               translate__text_model="THUDM/GLM-Z1-9B-0414",
               translate__base_url="https://api.siliconflow.cn/v1/chat/completions")
    eng = app.translate_engines["glm"]
    assert eng._client().cfg.model == "THUDM/GLM-Z1-9B-0414"

    # 清空翻译模型与 URL → 回到引擎默认
    app.config.translate.text_model = ""
    app.config.translate.base_url = ""
    app.apply_config(save=False)
    assert eng._client().cfg.model == GlmEngine.DEFAULT_MODEL
    assert eng._client().cfg.base_url == GlmEngine.DEFAULT_URL


# ----------------------------------------------------------------------
# 配置持久化：功能连接字段要能存能读
# ----------------------------------------------------------------------
def test_config_roundtrip(tmp_path):
    cfg = AppConfig()
    cfg.translate.api_key = "sk-abc"
    cfg.translate.base_url = "https://api.siliconflow.cn/v1/chat/completions"
    cfg.translate.text_model = "Qwen/Qwen3-8B"
    cfg.translate.temperature = 0.3
    cfg.translate.top_p = 0.95
    cfg.ai.api_key = "ai-key"
    cfg.ai.vision_model = "glm-4v-flash"
    p = tmp_path / "config.toml"
    cfg.save(p)

    back = AppConfig.load(p)
    assert back.translate.api_key == "sk-abc"
    assert back.translate.base_url.endswith("/v1/chat/completions")
    assert back.translate.text_model == "Qwen/Qwen3-8B"
    assert back.translate.temperature == 0.3
    assert back.translate.top_p == 0.95
    assert back.ai.api_key == "ai-key"
    assert back.ai.vision_model == "glm-4v-flash"


# ----------------------------------------------------------------------
# 旧配置自动迁移（3.0 → 3.4）：散落字段直接并入各功能
# ----------------------------------------------------------------------
def test_legacy_fields_migrate():
    cfg = AppConfig.from_dict({
        "ai": {"glm_api_key": "AI-KEY",
               "glm_base_url": "https://api.siliconflow.cn/v1/chat/completions",
               "glm_text_model": "Qwen/Qwen3-8B",
               "glm_vision_model": "glm-4v-flash",
               "hunyuan_api_key": "HY-KEY",
               "max_output_tokens": 999},
        "translate": {"glm_model": "t-model"},
        "ocr": {"cloud_api_key": "CK", "cloud_base_url": "https://c/v1/chat/completions",
                "cloud_model": "c-model"},
    })
    assert cfg.ai.api_key == "AI-KEY"
    assert cfg.ai.base_url.endswith("siliconflow.cn/v1/chat/completions")
    assert cfg.ai.text_model == "Qwen/Qwen3-8B"
    assert cfg.ai.vision_model == "glm-4v-flash"
    assert cfg.ai.max_output_tokens == 999
    # 翻译
    assert cfg.translate.text_model == "t-model"
    # OCR
    assert cfg.ocr.api_key == "CK"
    assert cfg.ocr.base_url.endswith("c/v1/chat/completions")
    assert cfg.ocr.vision_model == "c-model"

    # 迁移结果保存后加载，字段保持（幂等）
    p = tempfile.mktemp(suffix=".toml")
    try:
        cfg.save(p)
        back = AppConfig.load(p)
        assert back.ai.api_key == "AI-KEY"
        assert back.translate.text_model == "t-model"
        assert back.ocr.api_key == "CK"
    finally:
        os.remove(p)
