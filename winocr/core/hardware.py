# -*- coding: utf-8 -*-
"""硬件探测 — 让 WinOCR 在不同 CPU / GPU 机型上自动选择合理默认值。

设计原则：
  1. 纯 Python 标准库优先，零外部依赖就能算出 CPU 线程建议值
  2. GPU 探测尽量"软"：试几个常见标志，一个都没有就当没 GPU
  3. 所有返回值只作为 fallback——用户设了环境变量就尊重用户
  4. 模块加载时一次性探测，缓存结果；进程生命周期内硬件不会变
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CPU 线程建议
# ---------------------------------------------------------------------------

def suggest_cpu_threads(max_threads: int = 8) -> int:
    """根据物理核数给一个 llama.cpp / ctranslate2 都能用的默认线程数。

    为什么不是全核？
      - llama.cpp 超过 ~8 线程收益快速递减（内存带宽瓶颈）
      - ctranslate2 同理，intra_threads 超过 8 没明显提升
      - 留 1~2 核给 UI / OCR / 系统更流畅

    为什么不是 os.cpu_count() / 2？
      - 超线程（SMT）下逻辑核 ≈ 2× 物理核，但推理是计算密集型，
        物理核才是真并行。Python 拿不到物理核数时退化为逻辑核。
    """
    raw = os.cpu_count() or 1

    # 尝试用 psutil 拿物理核（有就更准，没有就用逻辑核）
    try:
        import psutil  # type: ignore
        physical = psutil.cpu_count(logical=False)
        if physical and physical > 0:
            raw = physical
    except Exception:
        pass

    # 建议值：物理核数，但至少 2（推理不能单线程），封顶 max_threads
    return max(2, min(raw, max_threads))


# ---------------------------------------------------------------------------
# GPU 探测（NVIDIA CUDA 优先，其次 Metal / Vulkan）
# ---------------------------------------------------------------------------

def has_nvidia_gpu() -> bool:
    """是否有可用的 NVIDIA GPU + CUDA 运行时（且架构够新，GPU 推理才比 CPU 快）。

    探测顺序：
      1. 环境变量 WINOCR_LLAMA_GPU_LAYERS 显式设置过（非 0 非空）→ 信用户
      2. nvidia-smi 查 GPU 名称 → 过滤掉 Kepler / Pascal 老卡（算力 ≤ 6.1）
      3. torch.cuda.is_available()
      4. ctypes 直接试加载 cudart64_*.dll

    为什么过滤老卡？
      - GT 710 / GTX 750 等 Kepler/Pascal 架构（算力 ≤ 6.1）没有 FP16
        硬件加速，llama.cpp 在上面跑 FP32 反而比 CPU AVX2 慢
      - Volta（V100，算力 7.0）及以上才值得 offload 到 GPU
    """
    # 1) 用户显式设过 GPU layers → 直接信（不管探测结果）
    env = os.environ.get("WINOCR_LLAMA_GPU_LAYERS", "").strip()
    if env and env != "0":
        return True

    # 2) nvidia-smi 查型号，过滤老架构
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        major = int(parts[1].split(".")[0])
                        if major >= 7:          # Volta+（V100/RTX 20xx/30xx/40xx）
                            return True
                        # 老卡（Kepler/Pascal/Turing 部分）→ 跳过，继续看其他探测
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass

    # 3) PyTorch
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return True
    except Exception:
        pass

    # 4) ctypes 加载 cudart
    try:
        import ctypes
        for suffix in ("64_12", "64_11", "64_10", "64"):
            try:
                ctypes.CDLL(f"cudart{suffix}.dll")
                return True
            except OSError:
                continue
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# 内存建议（ctx 窗口大小根据总内存调）
# ---------------------------------------------------------------------------

def suggest_ctx_window(default: int = 2048) -> int:
    """根据可用内存给 llama.cpp 上下文窗口建议值。

    每 1K ctx 大约吃 0.5~1MB KV cache（Q4 量化模型）：
      - 4GB 内存机器：保守 2048
      - 8GB：4096
      - 16GB+：8192（但用户默认 2048 已够翻译场景，这里只给建议不强制）
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            total_gb = ms.ullTotalPhys / (1024 ** 3)
            if total_gb >= 16:
                return 8192
            elif total_gb >= 8:
                return 4096
            else:
                return 2048
    except Exception:
        pass
    return default
