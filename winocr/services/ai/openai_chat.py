# -*- coding: utf-8 -*-
"""OpenAI 兼容对话提供方（SiliconFlow / DeepSeek / 自建 vLLM 等）。

与 GlmChatProvider 共用 ``winocr.services.openai_compatible`` 共享客户端，
差别只在默认 URL / 模型名——智谱是 OpenAI 兼容接口，其它厂商亦然。
3.4 改进蓝图 P2-2：解除「AI 提供方仅 glm」的供应商锁定，让
``AiConfig.provider`` 下拉可选 glm / openai_compat，两者在设置页
共用同一套连接参数（地址 / 密钥 / 模型 / 采样 / 限流）。
"""
from __future__ import annotations

import json
import os
import threading
from typing import List, Optional

from .base import AiProvider
from ...core.types import ChatMessage
from ..openai_compatible import (
    OpenAIClientConfig,
    OpenAICompatibleClient,
)

_DEFAULT_SYSTEM = (
    "你是 WinOCR 的 AI 助手，帮助用户理解截图识别出的文字、文档与图片内容。"
    "请用用户使用的语言作答，简洁、准确、直接给结论。"
)

# SiliconFlow 兼容 OpenAI 协议；留空 base_url 时用它（用户可在设置页改任意 OpenAI 兼容地址）
_DEFAULT_URL = "https://api.siliconflow.cn/v1/chat/completions"


class OpenAiCompatProvider(AiProvider):
    name = "openai_compat"
    display_name = "OpenAI 兼容（SiliconFlow / DeepSeek / vLLM 等）"
    supports_vision = True

    DEFAULT_URL = _DEFAULT_URL
    DEFAULT_TEXT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
    DEFAULT_VISION_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

    def __init__(self, history_path: Optional[str] = None) -> None:
        self.api_key = ""
        self.history_path = history_path
        self._hist_lock = threading.Lock()
        self._text_cfg = OpenAIClientConfig(
            api_key="", base_url=self.DEFAULT_URL, model=self.DEFAULT_TEXT_MODEL)
        self._vision_cfg = OpenAIClientConfig(
            api_key="", base_url="", model=self.DEFAULT_VISION_MODEL)
        self.text_client = OpenAICompatibleClient(self._text_cfg)
        self.vision_client = OpenAICompatibleClient(self._vision_cfg)
        self.system_prompt = _DEFAULT_SYSTEM
        self._messages: List[dict] = []
        self.vision_temperature: Optional[float] = None
        self.vision_top_p: Optional[float] = None
        self._load_history()

        self._vision_explicit_url = False
        self._vision_explicit_key = False
        self._vision_explicit_max_tokens = False
        self._vision_independent = False

        self.max_output_tokens = 2048
        self.max_context_tokens = 32768
        self.max_turns = 12
        self.temperature = 0.7
        self.top_p = 0.9
        self._sync_vision_defaults()

    # ------------------------------------------------------------------
    # 配置（与 GlmChatProvider 同语义）
    # ------------------------------------------------------------------
    def set_api_key(self, key: str) -> None:
        self.api_key = (key or "").strip()
        self._text_cfg.api_key = self.api_key
        self._sync_vision_defaults()

    def set_models(self, text_model: str = "", vision_model: str = "") -> None:
        if text_model and text_model.strip():
            self._text_cfg.model = text_model.strip()
        if vision_model and vision_model.strip():
            self._vision_cfg.model = vision_model.strip()

    def set_base_url(self, url: str = "") -> None:
        self._text_cfg.base_url = (url or "").strip() or self.DEFAULT_URL
        self._sync_vision_defaults()

    def set_vision_config(self, base_url: str = "", api_key: str = "",
                          model: str = "", temperature: float = 0.0,
                          top_p: float = 0.0,
                          max_output_tokens: int = 0,
                          independent: bool = False) -> None:
        self._vision_independent = bool(independent)
        if base_url is not None and base_url.strip():
            self._vision_cfg.base_url = base_url.strip()
            self._vision_explicit_url = True
        elif independent:
            self._vision_cfg.base_url = self.DEFAULT_URL
            self._vision_explicit_url = True
        if api_key is not None and api_key.strip():
            self._vision_cfg.api_key = api_key.strip()
            self._vision_explicit_key = True
        elif independent:
            self._vision_cfg.api_key = ""
            self._vision_explicit_key = True
        if model and model.strip():
            self._vision_cfg.model = model.strip()
        if temperature < 0:
            self.vision_temperature = None
        elif temperature > 0:
            self.vision_temperature = float(temperature)
        else:
            self.vision_temperature = self.temperature
        if top_p < 0:
            self.vision_top_p = None
        elif top_p > 0:
            self.vision_top_p = float(top_p)
        else:
            self.vision_top_p = self.top_p
        if max_output_tokens > 0:
            self._vision_cfg.max_output_tokens = int(max_output_tokens)
            self._vision_explicit_max_tokens = True
        self._sync_vision_defaults()

    def _sync_vision_defaults(self) -> None:
        if self._vision_independent:
            if not self._vision_cfg.base_url:
                self._vision_cfg.base_url = self.DEFAULT_URL
            if not self._vision_explicit_max_tokens:
                self._vision_cfg.max_output_tokens = self._text_cfg.max_output_tokens
            for attr in ("timeout", "retry_attempts", "retry_backoff",
                         "max_image_side"):
                setattr(self._vision_cfg, attr, getattr(self._text_cfg, attr))
            return
        if not self._vision_explicit_url:
            self._vision_cfg.base_url = self._text_cfg.base_url
        if not self._vision_explicit_key:
            self._vision_cfg.api_key = self._text_cfg.api_key
        if not self._vision_explicit_max_tokens:
            self._vision_cfg.max_output_tokens = self._text_cfg.max_output_tokens
        for attr in ("timeout", "retry_attempts", "retry_backoff", "max_image_side"):
            setattr(self._vision_cfg, attr, getattr(self._text_cfg, attr))

    def set_limits(self, max_output_tokens=None, max_context_tokens=None,
                   max_turns=None, retry_attempts=None, retry_backoff=None,
                   timeout=None, max_image_side=None, temperature=None,
                   top_p=None) -> None:
        if max_output_tokens is not None:
            self.max_output_tokens = max_output_tokens
            self._text_cfg.max_output_tokens = max_output_tokens
        if max_context_tokens is not None:
            self.max_context_tokens = max_context_tokens
        if max_turns is not None:
            self.max_turns = max_turns
        if retry_attempts is not None:
            self._text_cfg.retry_attempts = retry_attempts
            self._vision_cfg.retry_attempts = retry_attempts
        if retry_backoff is not None:
            self._text_cfg.retry_backoff = retry_backoff
            self._vision_cfg.retry_backoff = retry_backoff
        if timeout is not None:
            self._text_cfg.timeout = timeout
            self._vision_cfg.timeout = timeout
        if max_image_side is not None:
            self._text_cfg.max_image_side = max_image_side
            self._vision_cfg.max_image_side = max_image_side
        if temperature is not None:
            self.temperature = float(temperature)
        if top_p is not None:
            self.top_p = float(top_p)

    def apply_config(self, config) -> None:
        """从 AiConfig 注入全部连接参数（app 组合根调用，替代散落的 set_* 长调用）。"""
        self.set_api_key(config.api_key)
        self.set_base_url(config.base_url)
        self.set_models(text_model=config.text_model, vision_model=config.vision_model)
        self.set_vision_config(
            base_url=config.base_url, api_key=config.api_key,
            model=config.vision_model,
            temperature=config.vision_temperature,
            top_p=config.vision_top_p,
            max_output_tokens=config.vision_max_output_tokens,
            independent=True)
        self.set_limits(
            max_output_tokens=config.max_output_tokens,
            max_context_tokens=config.max_context_tokens,
            max_turns=config.max_turns,
            retry_attempts=config.retry_attempts,
            retry_backoff=config.retry_backoff,
            timeout=config.timeout,
            temperature=config.temperature,
            top_p=config.top_p)

    # ------------------------------------------------------------------
    # 运行时
    # ------------------------------------------------------------------
    def available(self) -> bool:
        return bool(self.api_key)

    def clear_history(self) -> None:
        self._messages.clear()
        self._persist()

    def get_history(self) -> List[dict]:
        return list(self._messages)

    def _load_history(self) -> None:
        if not self.history_path or not os.path.isfile(self.history_path):
            return
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return
            clean = []
            for m in data:
                if (isinstance(m, dict) and m.get("role") in ("user", "assistant")
                        and isinstance(m.get("content"), str)):
                    clean.append({"role": m["role"], "content": m["content"]})
            self._messages = clean
        except Exception:
            self._messages = []

    def _persist(self) -> None:
        if not self.history_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.history_path)),
                        exist_ok=True)
            tmp = self.history_path + ".tmp"
            with self._hist_lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._messages, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.history_path)
        except Exception:
            pass

    def set_history_path(self, p: str) -> None:
        """切换项目时重指对话历史文件：先持久化当前，再载入新项目上下文。"""
        if p == self.history_path:
            return
        try:
            self._persist()
        except Exception:
            pass
        self.history_path = p
        self._messages = []
        self._load_history()

    def chat(self, msg: ChatMessage) -> str:
        if not self.api_key:
            raise RuntimeError("API Key 未配置，请在「API 设置」中填写")

        images = [i for i in (msg.images or []) if i is not None]
        docs = [t for t in (msg.attachments_text or []) if t and t.strip()]

        full_text = msg.text or ""
        if msg.context_blocks:
            blob = "\n\n".join(msg.context_blocks)
            full_text = (f"{full_text}\n\n"
                         f"以下是背景上下文（当前屏幕内容与知识库相关记忆），"
                         f"请结合它理解问题、如实回答：\n\n{blob}").strip()
        if docs:
            blob = "\n\n".join(docs)
            full_text = (f"{full_text}\n\n"
                         f"以下是用户附加的文件内容，请结合它回答：\n\n{blob}").strip()

        if images:
            client = self.vision_client
            user_msg = OpenAICompatibleClient.multimodal_user_message(full_text, images)
            history_entry = {"role": "user",
                             "content": f"[图片 x{len(images)}] {full_text}"}
            use_temp, use_top = self.vision_temperature, self.vision_top_p
        else:
            client = self.text_client
            user_msg = {"role": "user", "content": full_text}
            history_entry = {"role": "user", "content": full_text}
            use_temp, use_top = self.temperature, self.top_p

        request_messages = self._history_for_request() + [user_msg]
        reply = client.complete(request_messages, system_prompt=self.system_prompt,
                                temperature=use_temp, top_p=use_top)
        self._messages.append(history_entry)
        self._messages.append({"role": "assistant", "content": reply})
        self._trim_history()
        self._persist()
        return reply

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _history_for_request(self) -> List[dict]:
        return list(self._messages)

    def _trim_history(self) -> None:
        if len(self._messages) > self.max_turns * 2:
            self._messages = self._messages[-self.max_turns * 2:]
        limit = int(self.max_context_tokens * 4 * 0.8)
        total = sum(len(m.get("content") or "")
                    for m in self._messages
                    if isinstance(m.get("content"), str))
        while total > limit and len(self._messages) > 2:
            removed = self._messages.pop(0)
            if isinstance(removed.get("content"), str):
                total -= len(removed["content"])
