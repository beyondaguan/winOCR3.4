# -*- coding: utf-8 -*-
"""llama.cpp 本地翻译引擎 -- 纯 CPU 推理（无需 GPU）。

使用 llama-cpp-python 加载 GGUF 格式的量化模型（如 HY-MT1.5-1.8B-Q4_K_M），
与 WinOCR OCR+翻译工作流集成。模型在首次翻译时惰性加载，注册表扫描阶段
不触发重型依赖导入（与 argos.py 的 ctranslate2 同模式）。

硬件适配（R5 5500 + GT 710 亮机卡）：
  - n_gpu_layers=0：完全禁用 GPU 卸载（GT 710 Kepler 架构算力不如 CPU AVX2）
  - n_threads=6：R5 5500 六核全开
  - n_ctx=2048：翻译场景够用，内存占用小

可通过环境变量微调：
  WINOCR_LLAMA_THREADS    (默认 6)
  WINOCR_LLAMA_CTX        (默认 2048)
  WINOCR_LLAMA_GPU_LAYERS (默认 0)
"""

from __future__ import annotations

import logging
import os
import threading

from .base import TranslateEngine

logger = logging.getLogger(__name__)

# ---- 推理参数（可被环境变量覆盖）----
_DEFAULT_THREADS = int(os.environ.get("WINOCR_LLAMA_THREADS", "6"))
_DEFAULT_CTX = int(os.environ.get("WINOCR_LLAMA_CTX", "2048"))
_DEFAULT_GPU_LAYERS = int(os.environ.get("WINOCR_LLAMA_GPU_LAYERS", "0"))

_LANG_NAMES = {
    "zh-CN": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "fr": "法文",
    "de": "德文",
    "es": "西班牙文",
    "ru": "俄文",
}


class LlamaCppEngine(TranslateEngine):
    """llama.cpp 本地翻译引擎（纯 CPU）。

    适配 GGUF 量化模型（HY-MT1.5-1.8B-Q4_K_M 等），与 WinOCR OCR->翻译
    工作流集成。首次翻译时惰性加载模型，后续复用。

    模型文件搜索：winocr.core.paths.find_llama_gguf()
    搜索目录：models/llama/ (项目内) > ~/.winocr/models/llama/ (用户目录)
    """

    name = "llama_cpp"
    display_name = "llama.cpp 本地翻译"
    online = False

    def __init__(self):
        self._model_name = ""  # 指定模型文件名（来自 config.text_model）
        self._max_tokens = 512  # 最大生成 token 数
        self._llm = None  # llama_cpp.Llama 实例（惰性初始化）
        self._model_path = ""  # 已加载的模型路径
        self._loaded_for_name = ""  # 已加载实例对应的模型名（防换模型竞态）
        self._wired = False  # 应用启动的首次参数注入不触发预热（保持惰性设计）
        self._lock = threading.RLock()  # 可重入：translate 持锁调用 _get_llm（内部也拿同一把锁）

    # ------------------------------------------------------------------
    def set_config(
        self,
        model=None,
        glm_api_key=None,
        api_key=None,
        base_url=None,
        max_output_tokens=None,
        retry_attempts=None,
        retry_backoff=None,
        **kwargs,
    ):
        """注入接口参数。

        model: 模型文件名（如 "hy-mt1.5-1.8b-q4_k_m.gguf"），来自翻译页的 text_model。
               留空则自动搜索目录中的第一个 .gguf 文件。
        max_output_tokens: 最大生成 token 数（翻译通常 512 足够）。
        其余参数为统一注入接口兼容（本引擎忽略 base_url/api_key 等）。
        """
        if model is not None:
            new_model = (model or "").strip()
            if new_model != self._model_name:
                self._llm = None
                self._model_path = ""
                self._loaded_for_name = ""
                self._model_name = new_model
                if self._wired and new_model:
                    # 会话中换模型：立即后台预加载，切换后首次翻译不再干等
                    # 磁盘加载 + 初始化（qwen 0.5B 冷启 ~5s，1.8B ~2s）。
                    self._start_preload()
            self._wired = True
        if max_output_tokens is not None:
            self._max_tokens = int(max_output_tokens) or 512
        if self._model_name:
            self.display_name = f"llama.cpp ({os.path.basename(self._model_name)})"
        else:
            self.display_name = "llama.cpp 本地翻译"

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

    def warmup(self):
        """预加载模型（加速首次翻译）。"""
        self._get_llm()

    def _start_preload(self):
        """后台预加载当前选定模型。

        每次真实换模型都起线程：多线程在 self._lock 上天然串行，
        后到者发现自己要的模型已加载就立即退出，不会重复装载。
        预热与 translate 共用锁：加载期间来到的翻译请求只是排队，
        加载完成后直接推理——原生层始终串行，不会并发崩溃。
        """
        threading.Thread(
            target=self._safe_preload, daemon=True, name="winocr-llama-preload"
        ).start()

    def _safe_preload(self):
        try:
            self._get_llm()
        except Exception as e:
            logger.warning("[llama.cpp] 模型预热失败（首次翻译时会再尝试）: %s", e)

    def _get_llm(self):
        """惰性加载模型（线程安全）。"""
        if (
            self._llm is not None
            and self._model_path
            and self._loaded_for_name == self._model_name
        ):
            return self._llm
        with self._lock:
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
                    + "\n或在翻译设置的「模型」字段填写模型文件名。"
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
            # 记录发起加载时的模型名：加载期间若 set_config 又换了名字，
            # 下次调用会因名字不匹配而重载，不会拿旧模型冒充新模型。
            self._loaded_for_name = name_at_load
            return self._llm

    # ------------------------------------------------------------------
    def translate(self, text: str, source: str, target: str) -> str:
        # llama.cpp 的 context 不是线程安全的：两个流水线线程并发调用
        # create_chat_completion 会在原生层触发 access violation 直接打死
        # 整个进程（Python try/except 拦不住），因此推理全程串行加锁。
        # 换模型时的 _get_llm() 也持有同一把锁，保证"换模型 + 推理"不交错。
        with self._lock:
            return self._translate_locked(text, source, target)

    def _translate_locked(self, text: str, source: str, target: str) -> str:
        llm = self._get_llm()
        src_name = _LANG_NAMES.get(source, source)
        tgt_name = _LANG_NAMES.get(target, target)
        prompt = (
            f"你是一个专业翻译引擎。请将以下{src_name}文本翻译为{tgt_name}。"
            f"要求：只输出译文，不输出任何解释或原文。"
            f"如果文本已经是{tgt_name}，请原样输出。\n\n{text}"
        )
        try:
            resp = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._max_tokens,
                temperature=0.1,
                top_p=0.9,
            )
            out = resp["choices"][0]["message"]["content"]
            return out.strip() if out else ""
        except Exception as e:
            raise RuntimeError(f"llama.cpp 翻译失败: {e}") from None
