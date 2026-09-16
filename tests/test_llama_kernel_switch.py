# -*- coding: utf-8 -*-
"""llama_kernel_switch 单元测试：双内核文件集自举与切换（临时目录，无真实 DLL）。"""
import pytest

from winocr.services.llama_kernel_switch import (
    active_kernel,
    apply_kernel,
    normalize_sets,
    sync_kernel,
)

VARIANTS = [f"ggml-cpu-{g}.dll" for g in
            ("sandybridge", "haswell", "skylake-x", "icelake", "vesuvio",
             "znver1", "znver2", "znver3", "znver4", "znver5")]


def make_avx_active(root):
    """构造「多变体已激活」的 lib 目录：主内核=haswell 内容 + 10 变体。"""
    lib = root / "lib"
    lib.mkdir(parents=True)
    (lib / "ggml.dll").write_bytes(b"G")
    (lib / "ggml-cpu.dll").write_bytes(b"H")  # haswell 激活态
    for v in VARIANTS:
        (lib / v).write_bytes(b"V:" + v.encode())
    (lib / "backup_baseline").mkdir()
    (lib / "backup_baseline" / "ggml-cpu.dll").write_bytes(b"M")
    return lib


def make_mkl_only(root):
    """构造「MKL 基线、从未装过多变体」的 lib 目录（无 backup、无变体）。"""
    lib = root / "lib"
    lib.mkdir(parents=True)
    (lib / "ggml.dll").write_bytes(b"G")
    (lib / "ggml-cpu.dll").write_bytes(b"M")
    return lib


def test_active_kernel_detection(tmp_path):
    lib = make_avx_active(tmp_path)
    assert active_kernel(lib) == "avx"

    lib2 = make_mkl_only(tmp_path / "x")
    assert active_kernel(lib2) == "mkl"


def test_normalize_from_avx_active_builds_both_sets(tmp_path):
    lib = make_avx_active(tmp_path)
    normalize_sets(lib)
    avx, mkl = lib / "kernel_sets" / "avx", lib / "kernel_sets" / "mkl"
    assert (avx / "ggml-cpu.dll").read_bytes() == b"H"
    for v in VARIANTS:
        assert (avx / v).is_file()
    assert (mkl / "ggml-cpu.dll").read_bytes() == b"M"  # 来自 backup_baseline


def test_mkl_only_machine_cannot_switch_to_avx(tmp_path):
    """从没装过多变体包的机器：avx 集缺失 → 切换静默保持 mkl。"""
    lib = make_mkl_only(tmp_path)
    assert sync_kernel("avx", lib_dir=lib) is False
    assert active_kernel(lib) == "mkl"
    assert (lib / "ggml-cpu.dll").read_bytes() == b"M"


def test_switch_avx_to_mkl_removes_variants(tmp_path):
    lib = make_avx_active(tmp_path)
    normalize_sets(lib)
    assert apply_kernel(lib, "mkl") is True
    assert active_kernel(lib) == "mkl"
    assert (lib / "ggml-cpu.dll").read_bytes() == b"M"
    assert not any(lib.glob("ggml-cpu-*.dll"))  # 变体必须清走，防双后端注册


def test_switch_back_to_avx_restores_full_set(tmp_path):
    lib = make_avx_active(tmp_path)
    normalize_sets(lib)
    apply_kernel(lib, "mkl")
    assert apply_kernel(lib, "avx") is True
    assert active_kernel(lib) == "avx"
    assert (lib / "ggml-cpu.dll").read_bytes() == b"H"
    for v in VARIANTS:
        assert (lib / v).read_bytes() == b"V:" + v.encode()


def test_sync_is_idempotent_when_already_on_target(tmp_path):
    lib = make_avx_active(tmp_path)
    normalize_sets(lib)
    mtime = (lib / "ggml-cpu.dll").stat().st_mtime_ns
    content = (lib / "ggml-cpu.dll").read_bytes()
    assert sync_kernel("avx", lib_dir=lib) is True  # 已是 avx：True 且不动文件
    assert (lib / "ggml-cpu.dll").read_bytes() == content
    assert (lib / "ggml-cpu.dll").stat().st_mtime_ns == mtime


def test_sync_unknown_value_defaults_to_avx(tmp_path):
    lib = make_avx_active(tmp_path)
    normalize_sets(lib)
    apply_kernel(lib, "mkl")
    assert sync_kernel("garbage", lib_dir=lib) is True  # 非 "mkl" 一律按 avx
    assert active_kernel(lib) == "avx"


def test_normalize_mkl_active_without_backup_self_bootstraps(tmp_path):
    """mkl 激活且无 backup（如回滚后删了备份）也能自举 mkl 集。"""
    lib = make_mkl_only(tmp_path)
    normalize_sets(lib)
    assert (lib / "kernel_sets" / "mkl" / "ggml-cpu.dll").read_bytes() == b"M"
    # avx 集没有主内核文件 → 切 avx 不可用
    assert apply_kernel(lib, "avx") is False


@pytest.mark.parametrize("bad", [None, "", "AVX", "  mkl  "])
def test_sync_accepts_common_kernel_strings(tmp_path, bad):
    lib = make_avx_active(tmp_path)
    normalize_sets(lib)
    apply_kernel(lib, "mkl")
    want = "mkl" if bad == "  mkl  " else "avx"
    assert sync_kernel(bad, lib_dir=lib) is True
    assert active_kernel(lib) == want
