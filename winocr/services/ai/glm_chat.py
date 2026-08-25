# -*- coding: utf-8 -*-
"""GLM 对话提供方（移植自 WinOCR2.0/engines/glm_chat.py，含多模态）。

只用标准库，不引入 SDK。底层请求统一走
``winocr.services.openai_compatible`` 的共享客户端，因此：
- 文本 / 视觉可分别使用任意 OpenAI 兼容接口（URL、Key、模型名独立）；
- 自动 429 退避重试、超时、max_tokens 全部只实现一次。

相比 2.0 的实质改进：
- 历史里不保留图片的 base64，只留一句占位描述（避免请求体随轮次膨胀到几 MB）；
- 历史按「对话轮次」+「上下文 token 上限」双阈值裁剪，防触发长度上限被关；
- 视觉连接可独立于文本连接配置（不同平台 / 不同 Key）。
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


class GlmChatProvider(AiProvider):
    name = "glm"
    display_name = "智谱 GLM-4（文本 + 视觉）"
    supports_vision = True

    DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    DEFAULT_TEXT_MODEL = "glm-4-flash"
    DEFAULT_VISION_MODEL = "glm-4v-flash"

    def __init__(self, history_path: Optional[str] = None) -> None:
        self.api_key = ""
        self.history_path = history_path          # 对话历史持久化文件（重开保留上下文）
        self._hist_lock = threading.Lock()
        # 文本侧连接配置（跟随「AI 对话」页所选连接）
        self._text_cfg = OpenAIClientConfig(
            api_key="", base_url=self.DEFAULT_URL, model=self.DEFAULT_TEXT_MODEL)
        # 视觉侧连接配置：默认地址/Key 复用文本侧，仅模型名不同（见 _sync_vision）
        self._vision_cfg = OpenAIClientConfig(
            api_key="", base_url="", model=self.DEFAULT_VISION_MODEL)
        self.text_client = OpenAICompatibleClient(self._text_cfg)
        self.vision_client = OpenAICompatibleClient(self._vision_cfg)
        self.system_prompt = _DEFAULT_SYSTEM
        self._messages: List[dict] = []
        # 视觉侧采样参数（与文本侧解耦）：None = 请求体不写该字段
        # （推理类视觉模型常要求如此，否则 400）。由 set_vision_config 设定。
        self.vision_temperature: Optional[float] = None
        self.vision_top_p: Optional[float] = None
        self._load_history()

        # 视觉侧哪些字段是用户「显式」指定的。
        # 没显式指定的字段才继承文本侧（URL / Key 各自独立判定，
        # 因此「只填视觉 Key、URL 仍跟随文本」这类部分覆盖也成立）。
        self._vision_explicit_url = False
        self._vision_explicit_key = False
        self._vision_explicit_max_tokens = False
        # 视觉侧是否按「独立平台」处理：AI 对话配置里文本与视觉共用一套凭证，
        # 独立时 URL / Key 绝不回退到文本侧，避免带着 A 平台 Key 发到 B 平台地址。
        self._vision_independent = False

        # ---- 限流 / 上下文保护（可被用户配置覆盖）----
        self.max_output_tokens = 2048
        self.max_context_tokens = 32768       # 粗略上限，超了就裁剪历史
        self.max_turns = 12                    # 保留的最大对话轮次
        self.temperature = 0.7                 # 采样温度（可经连接配置）
        self.top_p = 0.9                       # 核采样（可经连接配置）
        self._sync_vision_defaults()

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def set_api_key(self, key: str) -> None:
        self.api_key = (key or "").strip()
        self._text_cfg.api_key = self.api_key
        # 重新同步视觉侧（仅继承未显式指定的字段，不会锁定视觉专属 URL/Key）。
        self._sync_vision_defaults()

    def set_models(self, text_model: str = "", vision_model: str = "") -> None:
        """模型名开放填写。留空回落内置默认。"""
        if text_model and text_model.strip():
            self._text_cfg.model = text_model.strip()
        if vision_model and vision_model.strip():
            self._vision_cfg.model = vision_model.strip()

    def set_base_url(self, url: str = "") -> None:
        """文本侧 Base URL。留空回落智谱官方。"""
        self._text_cfg.base_url = (url or "").strip() or self.DEFAULT_URL
        self._sync_vision_defaults()

    def set_vision_config(self, base_url: str = "", api_key: str = "",
                          model: str = "", temperature: float = 0.0,
                          top_p: float = 0.0,
                          max_output_tokens: int = 0,
                          independent: bool = False) -> None:
        """视觉侧可独立指定接口（不同平台 / 不同 Key / 不同采样参数）。

        ``independent=True`` 表示视觉侧按**独立平台**处理：AI 对话配置里文本与视觉
        共用同一套凭证，独立平台模式仅用于阻止运行时误把文本侧兜底地址/密钥
        套用到视觉侧（URL 留空 → 回落智谱官方默认地址；
        Key 留空 → 明确为「未配置」，而不是悄悄借用文本 Key）。
        ``independent=False``（跟随 / 默认）时：未显式填写的字段（URL / Key
        各自独立判定）继续复用文本侧当前值；只有用户真正写过的字段才会锁定。
        采样参数语义：``temperature`` / ``top_p`` 传 ``<0`` → 请求体不写该字段
        （推理类视觉模型安全值）；``0`` → 跟随文本侧同名字段；``>0`` → 视觉专属值。
        ``max_output_tokens`` 传 ``>0`` 才锁定视觉专属，``0`` 跟随文本侧。
        """
        self._vision_independent = bool(independent)
        if base_url is not None and base_url.strip():
            self._vision_cfg.base_url = base_url.strip()
            self._vision_explicit_url = True
        elif independent:
            # 独立连接：URL 留空 → 引擎默认地址，不再继承文本侧
            self._vision_cfg.base_url = self.DEFAULT_URL
            self._vision_explicit_url = True
        if api_key is not None and api_key.strip():
            self._vision_cfg.api_key = api_key.strip()
            self._vision_explicit_key = True
        elif independent:
            # 独立连接：Key 留空 → 保持空（未配置），绝不借用文本 Key
            self._vision_cfg.api_key = ""
            self._vision_explicit_key = True
        if model and model.strip():
            self._vision_cfg.model = model.strip()
        # ---- 视觉采样参数（与文本侧解耦）----
        if temperature < 0:
            self.vision_temperature = None          # 不发送
        elif temperature > 0:
            self.vision_temperature = float(temperature)
        else:
            self.vision_temperature = self.temperature   # 跟随文本侧
        if top_p < 0:
            self.vision_top_p = None                # 不发送
        elif top_p > 0:
            self.vision_top_p = float(top_p)
        else:
            self.vision_top_p = self.top_p              # 跟随文本侧
        if max_output_tokens > 0:
            self._vision_cfg.max_output_tokens = int(max_output_tokens)
            self._vision_explicit_max_tokens = True
        self._sync_vision_defaults()

    def _sync_vision_defaults(self) -> None:
        """视觉未显式指定的字段 → 复用文本侧当前值（每次调用都重新跟随，
        因此后改文本 URL / Key / 限流 也能正确同步）。

        独立平台模式（``_vision_independent=True``）：视觉侧 URL / Key **不跟随文本侧**
        —— 地址用引擎默认，Key 用本功能配置里视觉侧自己的值（空即「未配置」），
        杜绝跨平台串 Key / 串地址。
        ``max_output_tokens`` 仍跟随（纯数值、无鉴权语义）。

        注意：temperature / top_p 是 provider 级、按调用方（文本 / 视觉）分别取用，
        不在这里同步；视觉侧由 set_vision_config 单独设定。
        """
        if self._vision_independent:
            # 独立平台：绝不借用文本侧 URL / Key；只同步纯数值限流参数
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
        # 限流参数：视觉侧与文本侧保持一致（一处调控全局）
        for attr in ("timeout", "retry_attempts", "retry_backoff", "max_image_side"):
            setattr(self._vision_cfg, attr, getattr(self._text_cfg, attr))

    def set_limits(self, max_output_tokens=None, max_context_tokens=None,
                   max_turns=None, retry_attempts=None, retry_backoff=None,
                   timeout=None, max_image_side=None, temperature=None,
                   top_p=None) -> None:
        """统一注入限流 / 上下文保护参数。"""
        if max_output_tokens is not None:
            self.max_output_tokens = max_output_tokens
            self._text_cfg.max_output_tokens = max_output_tokens
            # 视觉侧仅在未显式指定时才跟随文本侧（见 _sync_vision_defaults）
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

    # ------------------------------------------------------------------
    # 历史持久化（已落地）：对话从 chat_history_path 读取 / 写入 JSON，
    # 重开程序后 AI 面板上下文仍在；写盘用临时文件 + os.replace 原子替换，防进程被杀写坏。
    # ------------------------------------------------------------------
    def _load_history(self) -> None:
        """从文件恢复历史（只收 role/content 的 dict；坏文件安静忽略）。"""
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
            # 原子写：先写临时文件再 os.replace，进程被杀也不会写坏整个历史。
            # 与 services/persistence/json_history.py 的写法保持一致。
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
        self._load_history()                      # 持久化失败不阻断对话

    def chat(self, msg: ChatMessage) -> str:
        if not self.api_key:
            raise RuntimeError("GLM API Key 未配置，请在「API 设置」中填写")

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
            # 视觉侧采样参数（推理类模型常被设为 None = 不发送，避免 400）
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
        """双阈值裁剪：先按轮次，再按上下文 token 估算（字符数 / 4）。

        既防止对话无限变长触发长度上限，也避免一轮超长把历史撑爆。
        """
        if len(self._messages) > self.max_turns * 2:
            self._messages = self._messages[-self.max_turns * 2:]
        limit = int(self.max_context_tokens * 4 * 0.8)   # 留 20% 余量给输出
        total = sum(len(m.get("content") or "")
                    for m in self._messages
                    if isinstance(m.get("content"), str))
        while total > limit and len(self._messages) > 2:
            removed = self._messages.pop(0)
            if isinstance(removed.get("content"), str):
                total -= len(removed["content"])
