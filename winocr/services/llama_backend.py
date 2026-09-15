# -*- coding: utf-8 -*-
"""llama.cpp 后端枚举兼容层（Windows 多变体包）。

官方 llama.cpp Windows 二进制（GGML_BACKEND_DL 构建）把 CPU 后端拆成
ggml-cpu-sandybridge.dll / ggml-cpu-haswell.dll 等按代际命名的变体，需
显式调用 ggml_backend_load_all() 按 CPU 特征挑选注册；llama-cpp-python
为静态构建设计、不会主动调它，直接加载模型会报 "no backends are loaded"。

此模块在进程内一次性补齐：
  1. 定位 ggml.dll（conda 布局 <prefix>/Library/bin 或 wheel 布局包内 lib/）；
  2. 临时切 CWD 到该目录后调用 ggml_backend_load_all——load_best 的变体
     扫描路径是「主程序目录 + 当前目录」，不切 CWD 会一个变体都扫不到；
  3. ggml 自行按指令集打分选最优（如 i7-2600 → sandybridge=AVX）。

静态/单体构建下扫不到变体会自动 fallback 或静默跳过，调用无害、幂等。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_done = False


def _ggml_dll_dir() -> Path | None:
    """定位 ggml.dll 所在目录（与 llama_cpp 包自身的库解析逻辑对齐）。"""
    cand = Path(sys.prefix) / "Library" / "bin"
    if (cand / "ggml.dll").is_file():
        return cand
    try:
        import llama_cpp

        cand = Path(llama_cpp.__file__).parent / "lib"
        if (cand / "ggml.dll").is_file():
            return cand
    except ImportError:
        pass
    return None


def ensure_ggml_backends() -> None:
    """幂等：确保 ggml CPU 后端（多变体 DL 包）已枚举注册。

    任何失败（静态构建无此导出 / 非多平台布局）都静默跳过，保持旧行为。
    """
    global _done
    if _done:
        return
    _done = True  # 无论成败只尝试一次
    try:
        import llama_cpp  # noqa: F401  先触发依赖链，ggml.dll 随之入进程

        d = _ggml_dll_dir()
        if d is None:
            return
        os.add_dll_directory(str(d))
        lib = ctypes.CDLL(str(d / "ggml.dll"))
        load_all = lib.ggml_backend_load_all
        load_all.restype = None
        old = os.getcwd()
        os.chdir(d)
        try:
            load_all()
        finally:
            os.chdir(old)
        logger.debug("ggml_backend_load_all 完成（%s）", d)
    except Exception as e:  # noqa: BLE001
        logger.debug("ggml_backend_load_all 跳过：%s", e)
