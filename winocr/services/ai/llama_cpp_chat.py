# -*- coding: utf-8 -*-
"""llama.cpp 本地对话提供方 -- 纯 CPU 推理。

与翻译引擎 LlamaCppEngine 共用同一套模型加载逻辑（find_llama_gguf），
但服务于 AI 对话面板。支持对话历史持久化（与 GlmChatProvider /
OpenAiCompatProvider 同语义）。不支持视觉（supports_vision=False）。

注意：翻译专用模型（如 HY-MT1.5-1.8B）在 AI 对话面板上的表现有限；
若需高质量对话，建议换用通用 instruct 模型（如 Qwen2.5-1.5B-Instruct）。

可通过环境变量微调：
  WINOCR_LLAMA_THREADS    (默认 6)
  WINOCR_LLAMA_CTX        (默认 2048)
  WINOCR_LLAMA_GPU_LAYERS (默认 0)
  MKL_THREADING_LAYER     (默认 TBB，见下方说明)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import List, Optional

# conda-forge 版 llama.cpp 的 ggml-blas 链接 Intel MKL：默认 INTEL 线程层
# 加载 libiomp5md.dll，与 ctranslate2（Argos）自带的 OpenMP 运行时同进程
# 冲突（"OMP: Error #15" 直接中止进程）。改用 TBB 线程层规避；必须在
# llama_cpp 原生库加载前设置，用户显式设置时不覆盖。
os.environ.setdefault("MKL_THREADING_LAYER", "TBB")

from .base import AiProvider
from ...core.types import ChatMessage

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM = (
    "你是 WinOCR 的 AI 助手，帮助用户理解截图识别出的文字、文档内容。"
    "请用用户使用的语言作答，简洁、准确、直接给结论。"
)

_DEFAULT_THREADS = int(os.environ.get("WINOCR_LLAMA_THREADS", "6"))
_DEFAULT_CTX = int(os.environ.get("WINOCR_LLAMA_CTX", "2048"))
_DEFAULT_GPU_LAYERS = int(os.environ.get("WINOCR_LLAMA_GPU_LAYERS", "0"))


class LlamaCppProvider(AiProvider):
    """llama.cpp 本地 AI 对话提供方（纯 CPU）。

    与 GlmChatProvider 接口对齐：apply_config / chat / available /
    clear_history / get_history / set_history_path。
    本地引擎不需要 API Key，available() 只看模型文件和 llama-cpp-python 是否就绪。
    """

    name = "llama_cpp"
    display_name = "llama.cpp 本地对话"
    supports_vision = False

    def __init__(self, history_path: Optional[str] = None) -> None:
        self._model_name = ""
        self.max_output_tokens = 2048
        self.max_context_tokens = 32768
        self.max_turns = 12
        self.temperature = 0.7
        self.top_p = 0.9
        self.history_path = history_path
        self._hist_lock = threading.Lock()
        # llama.cpp context 不是线程安全的，并发推理会在原生层 access violation
        # 打死整个进程：加载与推理共用一把锁，串行执行。
        self._infer_lock = threading.Lock()
        self._llm = None
        self._model_path = ""
        self._loaded_for_name = ""  # 已加载实例对应的模型名（防换模型竞态）
        self._wired = False  # 应用启动的首次参数注入不触发预热（保持惰性设计）
        self.system_prompt = _DEFAULT_SYSTEM
        self._messages: List[dict] = []
        self._load_history()

    # ------------------------------------------------------------------
    # 配置（与 GlmChatProvider.apply_config 对齐，由 app 组合根调用）
    # ------------------------------------------------------------------
    def set_api_key(self, key: str) -> None:
        """本地引擎不需要 API Key，忽略。"""
        pass

    def apply_config(self, config) -> None:
        """从 AiConfig 注入参数。"""
        new_model = (config.text_model or "").strip()
        # 模型名变更时失效缓存，否则 _get_llm 会返回旧模型
        if new_model != self._model_name:
            self._llm = None
            self._model_path = ""
            self._loaded_for_name = ""
            self._model_name = new_model
            if self._wired and new_model:
                # 会话中换模型：立即后台预加载，切换后首条对话不再干等
                self._start_preload()
        self._wired = True
        self.max_output_tokens = config.max_output_tokens
        self.max_context_tokens = config.max_context_tokens
        self.max_turns = config.max_turns
        self.temperature = float(config.temperature)
        self.top_p = float(config.top_p)
        if self._model_name:
            self.display_name = f"llama.cpp ({os.path.basename(self._model_name)})"

    # ------------------------------------------------------------------
    # 运行时
    # ------------------------------------------------------------------
    def available(self) -> bool:
        """模型文件存在且 llama-cpp-python 可导入时可用。"""
        from ...core.paths import find_llama_gguf

        path = find_llama_gguf(self._model_name)
        if not path:
            return False
        try:
            import llama_cpp  # noqa: F401

            return True
        except ImportError:
            return False

    def clear_history(self) -> None:
        self._messages.clear()
        self._persist()

    def get_history(self) -> List[dict]:
        return list(self._messages)

    # ------------------------------------------------------------------
    # 历史持久化（与 GlmChatProvider 同模式：JSON + 原子写）
    # ------------------------------------------------------------------
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
                if (
                    isinstance(m, dict)
                    and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)
                ):
                    clean.append({"role": m["role"], "content": m["content"]})
            self._messages = clean
        except Exception:
            self._messages = []

    def _persist(self) -> None:
        if not self.history_path:
            return
        try:
            os.makedirs(
                os.path.dirname(os.path.abspath(self.history_path)), exist_ok=True
            )
            tmp = self.history_path + ".tmp"
            with self._hist_lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._messages, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.history_path)
        except Exception:
            pass

    def set_history_path(self, p: str) -> None:
        if p == self.history_path:
            return
        try:
            self._persist()
        except Exception:
            pass
        self.history_path = p
        self._messages = []
        self._load_history()

    # ------------------------------------------------------------------
    # 模型加载（惰性、线程安全）
    # ------------------------------------------------------------------
    def _start_preload(self):
        """后台预加载当前选定模型。

        每次真实换模型都起线程：多线程在 _infer_lock 上天然串行，
        后到者发现模型已加载就立即退出，不会重复装载。
        预热与 chat 共用锁：加载期间来到的对话请求只是排队，
        加载完成后直接推理——原生层始终串行。
        """
        threading.Thread(
            target=self._safe_preload, daemon=True, name="winocr-llama-chat-preload"
        ).start()

    def _safe_preload(self):
        try:
            self._get_llm()
        except Exception as e:
            logger.warning("[llama.cpp] 对话模型预热失败（首次对话时会再尝试）: %s", e)

    def _get_llm(self):
        if (
            self._llm is not None
            and self._model_path
            and self._loaded_for_name == self._model_name
        ):
            return self._llm
        with self._infer_lock:
            if (
                self._llm is not None
                and self._model_path
                and self._loaded_for_name == self._model_name
            ):
                return self._llm
            from ...core.paths import find_llama_gguf, llama_model_search_dirs

            path = find_llama_gguf(self._model_name)
            name_at_load = self._model_name  # 路径与名字同源快照，防加载中换名
            if not path:
                raise RuntimeError(
                    "llama.cpp: 未找到 GGUF 模型文件。请将模型放入以下目录之一：\n"
                    + "\n".join(f"  - {d}" for d in llama_model_search_dirs())
                    + "\n或在 AI 设置的「文本模型」字段填写模型文件名。"
                )
            try:
                from llama_cpp import Llama
            except ImportError as e:
                raise RuntimeError(
                    "llama-cpp-python 未安装，请运行: pip install llama-cpp-python"
                ) from e
            logger.info(
                "[llama.cpp] 加载模型: %s (threads=%d, ctx=%d, gpu_layers=%d)",
                os.path.basename(path),
                _DEFAULT_THREADS,
                _DEFAULT_CTX,
                _DEFAULT_GPU_LAYERS,
            )
            self._llm = Llama(
                model_path=path,
                n_ctx=_DEFAULT_CTX,
                n_threads=_DEFAULT_THREADS,
                n_gpu_layers=_DEFAULT_GPU_LAYERS,
                verbose=False,
            )
            self._model_path = path
            self._loaded_for_name = name_at_load
            return self._llm

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, msg: ChatMessage) -> str:
        # 推理串行化（与翻译引擎 LlamaCppEngine 同理，防止原生层并发崩溃）
        with self._infer_lock:
            return self._chat_locked(msg)

    def _chat_locked(self, msg: ChatMessage) -> str:
        llm = self._get_llm()

        full_text = msg.text or ""
        if msg.context_blocks:
            blob = "\n\n".join(msg.context_blocks)
            full_text = (
                f"{full_text}\n\n"
                f"以下是背景上下文（当前屏幕内容与知识库相关记忆），"
                f"请结合它理解问题、如实回答：\n\n{blob}"
            ).strip()
        if msg.attachments_text:
            blob = "\n\n".join(msg.attachments_text)
            full_text = (
                f"{full_text}\n\n以下是用户附加的文件内容，请结合它回答：\n\n{blob}"
            ).strip()

        user_msg = {"role": "user", "content": full_text}
        history_entry = {"role": "user", "content": full_text}

        request_messages = [{"role": "system", "content": self.system_prompt}]
        request_messages += self._messages + [user_msg]

        try:
            resp = llm.create_chat_completion(
                messages=request_messages,
                max_tokens=self.max_output_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
            )
            reply = resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise RuntimeError(f"llama.cpp 对话失败: {e}") from None

        self._messages.append(history_entry)
        self._messages.append({"role": "assistant", "content": reply})
        self._trim_history()
        self._persist()
        return reply

    def _trim_history(self) -> None:
        """双阈值裁剪：先按轮次，再按上下文 token 估算（字符数 / 4）。"""
        if len(self._messages) > self.max_turns * 2:
            self._messages = self._messages[-self.max_turns * 2 :]
        limit = int(self.max_context_tokens * 4 * 0.8)
        total = sum(
            len(m.get("content") or "")
            for m in self._messages
            if isinstance(m.get("content"), str)
        )
        while total > limit and len(self._messages) > 2:
            removed = self._messages.pop(0)
            if isinstance(removed.get("content"), str):
                total -= len(removed["content"])
