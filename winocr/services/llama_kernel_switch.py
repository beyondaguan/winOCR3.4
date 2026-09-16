# -*- coding: utf-8 -*-
"""llama.cpp CPU 内核切换（官方多变体 AVX ⇄ conda MKL 基线）。

两种内核的差异文件只有 ggml-cpu.dll（外加多变体包的 14 个
ggml-cpu-*.dll 变体文件——MKL 模式下必须从 lib/ 移走，否则
ggml_backend_load_all 会把它们也注册进来，与 MKL 内核形成双 CPU 后端）。

磁盘布局（lib_dir = llama_cpp 包的库目录）：
    lib/ggml-cpu.dll            当前生效的 CPU 内核
    lib/ggml-cpu-*.dll          变体文件（仅 AVX 模式存在于 lib/）
    lib/kernel_sets/avx/        AVX 模式完整文件集（主内核 + 全部变体）
    lib/kernel_sets/mkl/        MKL 基线文件集（ggml-cpu.dll）

kernel_sets 首跑由 normalize_sets() 从现场状态自举：
  * avx 集 ← 当前 lib/ 里的激活内核与变体（仅当 AVX 激活时可信）；
  * mkl 集 ← tools/fix_llama_avx.py 的 backup_baseline/ 备份优先，
    其次 MKL 激活时的 lib/ggml-cpu.dll。
缺任一来源则该目标集不完整，对应切换静默跳过（保持现状，无害）。

硬约束：DLL 已加载进进程时文件被锁（WinError 32），不能热切换。
因此 sync_kernel() 只应在 ensure_ggml_backends() 入口、即 import
llama_cpp 之前调用——进程首次加载模型前完成换装；运行中改配置
则下次启动生效。
"""
from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

VARIANT_GLOB = "ggml-cpu-*.dll"
MAIN_DLL = "ggml-cpu.dll"


def _auto_lib_dir() -> Path | None:
    """定位 llama_cpp 库目录（与 llama_backend._ggml_dll_dir 对齐）。"""
    cand = Path(sys.prefix) / "Library" / "bin"
    if (cand / "ggml.dll").is_file():
        return cand
    try:
        import llama_cpp

        cand = Path(llama_cpp.__file__).parent / "lib"
        if (cand / "ggml.dll").is_file():
            return cand
    except Exception:  # noqa: BLE001  断链状态下导入会炸——路径照样能推
        for sp in (Path(sys.prefix) / "Lib" / "site-packages",):
            cand = sp / "llama_cpp" / "lib"
            if (cand / "ggml.dll").is_file():
                return cand
    return None


def active_kernel(lib_dir: Path) -> str:
    """当前激活的内核：'avx' / 'mkl'。判据 = lib/ 下有无变体文件。"""
    return "avx" if any(lib_dir.glob(VARIANT_GLOB)) else "mkl"


def normalize_sets(lib_dir: Path) -> None:
    """从现场状态自举 kernel_sets/（幂等：已有的集合文件不覆盖）。"""
    sets = lib_dir / "kernel_sets"
    cur = active_kernel(lib_dir)

    avx = sets / "avx"
    avx.mkdir(parents=True, exist_ok=True)
    main_src = lib_dir / MAIN_DLL
    if not (avx / MAIN_DLL).is_file() and cur == "avx" and main_src.is_file():
        shutil.copy2(main_src, avx / MAIN_DLL)
    for v in lib_dir.glob(VARIANT_GLOB):
        dst = avx / v.name
        if not dst.is_file():
            shutil.copy2(v, dst)

    mkl = sets / "mkl"
    mkl.mkdir(parents=True, exist_ok=True)
    if not (mkl / MAIN_DLL).is_file():
        bak = lib_dir / "backup_baseline" / MAIN_DLL
        if bak.is_file():
            shutil.copy2(bak, mkl / MAIN_DLL)
        elif cur == "mkl" and main_src.is_file():
            shutil.copy2(main_src, mkl / MAIN_DLL)


def apply_kernel(lib_dir: Path, target: str) -> bool:
    """把 target（'avx'|'mkl'）文件集铺到 lib_dir。成功 True，失败 False（不抛）。"""
    target = "mkl" if target == "mkl" else "avx"
    src_dir = lib_dir / "kernel_sets" / target
    main = src_dir / MAIN_DLL
    if not main.is_file():
        logger.info("[llama_kernel] %s 文件集不完整，保持现状", target)
        return False
    try:
        shutil.copy2(main, lib_dir / MAIN_DLL)
        if target == "mkl":
            for v in lib_dir.glob(VARIANT_GLOB):
                v.unlink()
        else:
            for v in src_dir.glob(VARIANT_GLOB):
                shutil.copy2(v, lib_dir / v.name)
        return True
    except OSError as e:
        logger.warning("[llama_kernel] 换装失败（DLL 可能被占用）：%s", e)
        return False


def sync_kernel(kernel: str, lib_dir: Path | None = None) -> bool:
    """按目标内核换装（幂等、绝不抛）。须在 import llama_cpp 之前调用。

    kernel 取值 'avx' / 'mkl'（其余按 'avx' 处理）。已是目标内核时
    直接返回 True，不动任何文件。
    """
    try:
        d = lib_dir or _auto_lib_dir()
        if d is None or not (d / "ggml.dll").is_file():
            return False
        normalize_sets(d)
        k = "mkl" if (kernel or "").strip().lower() == "mkl" else "avx"
        cur = active_kernel(d)
        if cur == k:
            return True
        if apply_kernel(d, k):
            logger.info("[llama_kernel] CPU 内核切换: %s → %s", cur, k)
            return True
        return False
    except Exception as e:  # noqa: BLE001  换装是优化项，失败不阻断模型加载
        logger.debug("[llama_kernel] 跳过：%s", e)
        return False
