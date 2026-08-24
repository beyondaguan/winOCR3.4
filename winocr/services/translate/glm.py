# -*- coding: utf-8 -*-
"""GLM 翻译引擎（任意 OpenAI 兼容大模型）。

只依赖标准库，底层走 ``winocr.services.openai_compatible`` 共享客户端，
因此天生支持任意 OpenAI 兼容接口（智谱 / SiliconFlow / OneAPI / 自建 vLLM），
并自带 429 退避重试与可配 max_tokens。接口参数（URL/模型/Key/限流）由 App 注入，
来自「大模型翻译」页自己的连接参数（每功能独立持有，不引用其它功能）。
"""
from __future__ import annotations

from .base import TranslateEngine
from ..openai_compatible import OpenAIClientConfig, OpenAICompatibleClient

#: 用户没填任何东西时的兜底 —— 智谱官方免费基座
DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4-flash"


class GlmEngine(TranslateEngine):
    name = "glm"
    display_name = "GLM 翻译"
    online = True
    # 供测试与 UI 展示引用
    DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    DEFAULT_MODEL = "glm-4-flash"

    def __init__(self):
        self.api_key = ""
        self._url = DEFAULT_URL
        self._model = DEFAULT_MODEL
        self._max_output_tokens = 1024
        self._retry_attempts = 3
        self._retry_backoff = 1.5
        self._timeout = 30
        self._lang_names = {"zh-CN": "中文", "en": "英文", "ja": "日文",
                            "ko": "韩文", "fr": "法文", "de": "德文",
                            "es": "西班牙文", "ru": "俄文"}

    # ------------------------------------------------------------------
    def set_config(self, glm_api_key=None, base_url=None, model=None,
                   api_key=None, max_output_tokens=None, retry_attempts=None,
                   retry_backoff=None, **kwargs):
        """注入接口参数。

        空字符串表示「用户清空了这一项」，必须回落到默认值而不是保留旧值——
        否则改错一次配置后就再也回不去，只能重启程序。None 才表示「不涉及」。
        ``api_key`` 是混元等其它引擎的密钥，本引擎忽略；保留以兼容统一注入。
        """
        if glm_api_key is not None:
            self.api_key = (glm_api_key or "").strip()
        if base_url is not None:
            self._url = (base_url or "").strip() or DEFAULT_URL
        if model is not None:
            self._model = (model or "").strip() or DEFAULT_MODEL
        if max_output_tokens is not None:
            self._max_output_tokens = max_output_tokens
        if retry_attempts is not None:
            self._retry_attempts = retry_attempts
        if retry_backoff is not None:
            self._retry_backoff = retry_backoff
        self.display_name = f"GLM 翻译 ({self._model})"

    def _client(self) -> OpenAICompatibleClient:
        cfg = OpenAIClientConfig(
            api_key=self.api_key,
            base_url=self._url,
            model=self._model,
            max_output_tokens=self._max_output_tokens,
            retry_attempts=self._retry_attempts,
            retry_backoff=self._retry_backoff,
            timeout=self._timeout,
        )
        return OpenAICompatibleClient(cfg)

    # ------------------------------------------------------------------
    def available(self) -> bool:
        return bool(self.api_key)

    def translate(self, text: str, source: str, target: str) -> str:
        if not self.api_key:
            raise RuntimeError("GLM API Key 未配置")
        src_name = self._lang_names.get(source, source)
        tgt_name = self._lang_names.get(target, target)
        prompt = (f"你是一个专业翻译引擎。请将以下{src_name}文本翻译为{tgt_name}。"
                  f"要求：只输出译文，不输出任何解释或原文。"
                  f"如果文本已经是{tgt_name}，请原样输出。\n\n{text}")
        try:
            return self._client().complete(
                [{"role": "user", "content": prompt}], temperature=0.1, top_p=0.9)
        except Exception as e:
            raise RuntimeError(f"GLM 翻译失败: {e}") from None
