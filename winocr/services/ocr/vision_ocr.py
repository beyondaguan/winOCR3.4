# -*- coding: utf-8 -*-
"""云端视觉 OCR 引擎（OpenAI 兼容视觉模型）。

作为本地 RapidOCR 的互补：断网用 RapidOCR，需要更强识别时切换成本地之外的
云端视觉模型（如 GLM-4.1V-9B-Thinking、Qwen2.5-VL）。

接口完全开放：Base URL / 模型名 / API Key 都可填任意 OpenAI 兼容平台
（智谱、SiliconFlow、自建 vLLM …）。底层复用共享客户端，自带 429 退避重试。
"""
from __future__ import annotations

import time

from ...core.types import OcrResult
from ..ocr.base import OcrEngine
from ..openai_compatible import OpenAIClientConfig, OpenAICompatibleClient

DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = "GLM-4.1V-9B-Thinking"

_OCR_PROMPT = (
    "你是一个 OCR 引擎。请识别图片中出现的全部文字，"
    "严格按原文从上到下、从左到右的顺序输出，保持原有换行。"
    "只输出识别结果，不要解释、不要翻译、不要改写。"
)


class VisionOcrEngine(OcrEngine):
    name = "vision_ocr"
    display_name = "云端视觉 OCR"
    offline = False                    # 依赖远程接口

    def __init__(self):
        self.api_key = ""
        self._url = DEFAULT_URL
        self._model = DEFAULT_MODEL
        self._max_output_tokens = 4096
        self._retry_attempts = 3
        self._retry_backoff = 1.5
        self._timeout = 60
        self._max_image_side = 1600
        # 视觉采样参数：None = 请求体不写该字段（推理 OCR 模型安全默认）
        self._temperature = None
        self._top_p = None

    # ---- 配置注入 ----
    def configure(self, cloud_api_key=None, cloud_base_url=None, cloud_model=None,
                  cloud_max_output_tokens=None, cloud_retry_attempts=None,
                  cloud_retry_backoff=None, cloud_timeout=None,
                  cloud_temperature=None, cloud_top_p=None, **_):
        if cloud_api_key is not None:
            self.api_key = (cloud_api_key or "").strip()
        if cloud_base_url is not None:
            self._url = (cloud_base_url or "").strip() or DEFAULT_URL
        if cloud_model is not None:
            self._model = (cloud_model or "").strip() or DEFAULT_MODEL
        if cloud_max_output_tokens is not None:
            self._max_output_tokens = cloud_max_output_tokens
        if cloud_retry_attempts is not None:
            self._retry_attempts = cloud_retry_attempts
        if cloud_retry_backoff is not None:
            self._retry_backoff = cloud_retry_backoff
        if cloud_timeout is not None:
            self._timeout = cloud_timeout
        # 视觉采样参数：<0 → 不发送；0 → 跟随文本侧默认（这里回落为不发送，
        # 因 OCR 用推理模型时发 temperature 会 400）；>0 → 显式值。
        if cloud_temperature is not None:
            self._temperature = (None if float(cloud_temperature) < 0
                                 else (float(cloud_temperature)
                                       if float(cloud_temperature) > 0 else None))
        if cloud_top_p is not None:
            self._top_p = (None if float(cloud_top_p) < 0
                           else (float(cloud_top_p)
                                 if float(cloud_top_p) > 0 else None))

    def apply_config(self, config) -> None:
        """从 OcrConfig 注入云端视觉 OCR 参数（app 组合根调用）。"""
        self.configure(
            cloud_api_key=config.api_key,
            cloud_base_url=config.base_url,
            cloud_model=config.vision_model,
            cloud_max_output_tokens=config.max_output_tokens,
            cloud_retry_attempts=config.retry_attempts,
            cloud_retry_backoff=config.retry_backoff,
            cloud_timeout=config.timeout,
            cloud_temperature=config.vision_temperature,
            cloud_top_p=config.vision_top_p,
        )

    def _client(self) -> OpenAICompatibleClient:
        cfg = OpenAIClientConfig(
            api_key=self.api_key,
            base_url=self._url,
            model=self._model,
            max_output_tokens=self._max_output_tokens,
            retry_attempts=self._retry_attempts,
            retry_backoff=self._retry_backoff,
            timeout=self._timeout,
            max_image_side=self._max_image_side,
        )
        return OpenAICompatibleClient(cfg)

    # ---- 运行时 ----
    def available(self) -> bool:
        return bool(self.api_key)

    def recognize(self, image) -> OcrResult:
        if not self.api_key:
            raise RuntimeError("云端 OCR 未配置 API Key")
        t0 = time.time()
        try:
            client = self._client()
            user_msg = OpenAICompatibleClient.multimodal_user_message(_OCR_PROMPT, [image])
            text = client.complete([user_msg],
                                   temperature=self._temperature,
                                   top_p=self._top_p)
        except Exception as e:
            raise RuntimeError(f"云端 OCR 失败: {e}") from None
        text = text.strip()
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        return OcrResult(text=text, lines=lines, engine=self.name,
                         elapsed=time.time() - t0)
