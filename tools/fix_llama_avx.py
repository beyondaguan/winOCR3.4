# -*- coding: utf-8 -*-
"""Swap llama-cpp-python's baseline CPU kernel for official llama.cpp
multi-variant DLLs (native AVX / AVX2 / NEON speed-up on any machine).

Why
---
The conda-forge 'cpu_mkl' llama.cpp package installed by
tools/download_all_models.py ships an SSE2-baseline ggml-cpu.dll, so
llama-cpp-python runs at baseline speed even on AVX/AVX2-capable CPUs
(measured: 3.3 tok/s vs 18.1 tok/s for 0.5B Q4 on Sandy Bridge).
The official llama.cpp release ships runtime-selected CPU variants
(ggml-cpu-sandybridge.dll, ggml-cpu-haswell.dll, ggml-cpu-apple? no -
ggml-cpu-armv8.2.dll ...); swapping them in gives every x64 / ARM64 CPU
its native kernel, no rebuild needed.

Usage
-----
    python tools/fix_llama_avx.py                 # apply (default)
    python tools/fix_llama_avx.py --status        # show current state
    python tools/fix_llama_avx.py --detect        # CPU features + backend
    python tools/fix_llama_avx.py --rollback      # restore baseline DLLs
    python tools/fix_llama_avx.py --offline FILE  # apply from a local zip
                                                  # (no-network machines)

Notes
-----
* Download goes through the same China proxy chain as
  tools/download_all_models.py, then direct GitHub; the zip is cached in
  vendor/.cache/llama_cpp/ so re-runs are free.
* Backups are written to <bin>_backup_baseline/ on the FIRST apply only,
  so a rollback always restores the original set.
* A smoke check (register backends, require count >= 1) runs after the
  swap; on failure everything is rolled back automatically.
* Safe to run repeatedly: already-applied machines are skipped.
* Console output is ASCII-only so GBK / UTF-8 consoles both work.
* Exit codes: 0 = applied / already applied / nothing to do;
  1 = failed (non-fatal for the installation; only local LLM speed is
  affected - OCR / translate / UI work regardless).
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the proxy chain from the unified downloader (kept in sync).
from tools.download_all_models import gh_urls  # noqa: E402

# Must match the llama.cpp source tag used by the conda-forge package
# (llama.cpp-10588-...) so the exported C API stays compatible with the
# llama-cpp-python 0.3.35 bindings already installed.
LLAMA_TAG = "b10588"

UA = {"User-Agent": "winocr"}

# Files to overwrite from the official package (same upstream tag).
COPY_FIXED = ["ggml.dll", "ggml-base.dll", "llama.dll", "mtmd.dll", "libomp.dll"]
# Old conda static CPU kernel: moved away so the variant mechanism is used.
OLD_CPU_DLL = "ggml-cpu.dll"


# ---------------------------------------------------------------- layout
def find_bin_dirs() -> list[Path]:
    """All candidate directories that may contain llama.dll (any layout)."""
    cands: list[Path] = []

    # 1) project venv / conda layout: <prefix>/Library/bin
    cands.append(ROOT / ".venv" / "Library" / "bin")
    cands.append(Path(sys.prefix) / "Library" / "bin")

    # 2) pip wheel layout: <site-packages>/llama_cpp/lib
    try:
        import llama_cpp

        cands.append(Path(llama_cpp.__file__).parent / "lib")
    except ImportError:
        pass

    seen, out = set(), []
    for c in cands:
        if c not in seen and (c / "llama.dll").is_file():
            seen.add(c)
            out.append(c)
    return out


def backup_dir_for(bin_dir: Path) -> Path:
    return bin_dir.parent / (bin_dir.name + "_backup_baseline")


def variants_in(bin_dir: Path) -> list[Path]:
    return sorted(bin_dir.glob("ggml-cpu-*.dll"))


def zip_name_for_arch() -> str:
    if platform.machine() in ("ARM64", "aarch64"):
        return f"llama-{LLAMA_TAG}-bin-win-cpu-arm64.zip"
    return f"llama-{LLAMA_TAG}-bin-win-cpu-x64.zip"


# ---------------------------------------------------------------- status
def show_status() -> None:
    print("=== llama.cpp CPU kernel status ===")
    print(f"  machine          : {platform.machine()}")
    print(f"  llama.cpp tag    : {LLAMA_TAG}")
    try:
        import llama_cpp

        print(f"  llama-cpp-python : {llama_cpp.__version__}")
    except ImportError:
        print("  llama-cpp-python : NOT INSTALLED (local LLM disabled)")
    dirs = find_bin_dirs()
    if not dirs:
        print("  llama.dll        : not found (nothing to do)")
        return
    for d in dirs:
        vs = variants_in(d)
        bk = backup_dir_for(d)
        state = "OFFICIAL multi-variant" if vs else "baseline (conda-forge SSE2)"
        print(f"  bin dir          : {d}")
        print(f"  kernel           : {state}"
              + (f" ({len(vs)} variants)" if vs else ""))
        print(f"  backup folder    : {bk.name} "
              + ("exists" if bk.is_dir() else "absent"))


# ---------------------------------------------------------------- apply
def apply(offline_zip: str | None = None) -> int:
    if platform.machine() not in (
        "AMD64", "x86_64", "ARM64", "aarch64",
    ):
        print("[skip] unsupported machine, baseline kernel is fine")
        return 0

    dirs = find_bin_dirs()
    if not dirs:
        print("[skip] llama.dll not found (llama-cpp-python not installed?)")
        return 0
    bin_dir = dirs[0]

    if variants_in(bin_dir):
        print(f"[skip] variant DLLs already applied "
              f"({len(variants_in(bin_dir))} ggml-cpu-*.dll present)")
        return 0

    zip_path: Path | None = None
    extract_dir: Path | None = None
    if offline_zip:
        zip_path = Path(offline_zip)
        if not zip_path.is_file():
            print(f"[fail] offline zip not found: {zip_path}")
            return 1
        print(f"[1/4] using offline package: {zip_path}")
    else:
        print(f"[1/4] fetching official llama.cpp {LLAMA_TAG} CPU package "
              f"(proxy chain + direct)...")
        zip_path = ROOT / "vendor" / ".cache" / "llama_cpp" / zip_name_for_arch()
        try:
            download(zip_path)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] download error: {e}")
            print("       tip: run 'python tools/fix_llama_avx.py "
                  "--offline <zip>' on offline machines")
            return 1

    print("[2/4] extracting...")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                print(f"[fail] corrupted zip entry: {bad}")
                return 1
            names = zf.namelist()
            need = COPY_FIXED + [n for n in names
                                 if n.startswith("ggml-cpu-")
                                 and n.endswith(".dll")]
            extract_dir = Path(tempfile.mkdtemp(prefix="winocr_avx_"))
            for n in need:
                if n in names:
                    zf.extract(n, extract_dir)
    except Exception as e:  # noqa: BLE001
        print(f"[fail] extract error: {e}")
        return 1

    variants = sorted(extract_dir.glob("ggml-cpu-*.dll"))
    if not variants:
        print("[fail] no ggml-cpu-*.dll variants inside the package")
        shutil.rmtree(extract_dir, ignore_errors=True)
        return 1

    backup = backup_dir_for(bin_dir)
    print(f"[3/4] backing up current DLLs -> {backup.name}/ "
          f"and swapping in {len(variants)} CPU variants...")
    backup.mkdir(parents=True, exist_ok=True)
    for f in COPY_FIXED:
        src = bin_dir / f
        if src.is_file() and not (backup / f).exists():
            shutil.copy2(src, backup / f)
    old_cpu = bin_dir / OLD_CPU_DLL
    if old_cpu.is_file() and not (backup / OLD_CPU_DLL).exists():
        shutil.move(str(old_cpu), backup / OLD_CPU_DLL)

    for f in COPY_FIXED:
        src = extract_dir / f
        if src.is_file():
            shutil.copy2(src, bin_dir / f)
    for v in variants:
        shutil.copy2(v, bin_dir / v.name)
    shutil.rmtree(extract_dir, ignore_errors=True)

    print("[4/4] smoke check (register backends, count >= 1)...")
    if not smoke_check(bin_dir):
        print("[fail] smoke check failed, rolling back...")
        do_rollback(bin_dir)
        return 1

    print(f"[ok] official CPU variants applied "
          f"({len(variants)} variants, runtime auto-selects per CPU)")
    return 0


def download(dest: Path) -> None:
    """Download through proxy chain then direct; resumable via cache file."""
    if dest.is_file() and dest.stat().st_size > 8 * 1024 * 1024:
        print(f"       cached: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    gh_path = ("https://github.com/ggml-org/llama.cpp/releases/download/"
               f"{LLAMA_TAG}/{dest.name}")
    part = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for url in gh_urls(gh_path):
        try:
            print(f"       trying {url.split('/')[2]} ...")
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as resp, \
                    open(part, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = min(100, done * 100 // total)
                        print(f"\r       {pct}% ({done // 1048576} MB)",
                              end="", flush=True)
                print()
            part.rename(dest)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"       failed: {e}")
    raise RuntimeError(f"all download attempts failed ({last_err})")


def smoke_check(bin_dir: Path) -> bool:
    """Load the swapped DLL chain and require at least one backend."""
    try:
        import llama_cpp  # noqa: F401  load llama.dll dependency chain first

        os.add_dll_directory(str(bin_dir))
        lib = ctypes.CDLL(str(bin_dir / "ggml.dll"))
        load_all = lib.ggml_backend_load_all
        load_all.restype = None
        old = os.getcwd()
        os.chdir(bin_dir)  # load_best scans exe dir + CWD only
        try:
            load_all()
        finally:
            os.chdir(old)
        lib.ggml_backend_reg_count.restype = ctypes.c_size_t
        n = int(lib.ggml_backend_reg_count())
        print(f"       registered backends: {n}")
        return n >= 1
    except Exception as e:  # noqa: BLE001
        print(f"       smoke check error: {e}")
        return False


# ---------------------------------------------------------------- rollback
def do_rollback(bin_dir: Path) -> None:
    """Restore the original DLL set from the backup folder."""
    backup = backup_dir_for(bin_dir)
    try:
        if not backup.is_dir():
            print(f"[fail] no backup folder: {backup}")
            return
        for f in backup.iterdir():
            shutil.copy2(f, bin_dir / f.name)
        # remove variant DLLs that came with the official package
        removed = 0
        for v in variants_in(bin_dir):
            (bin_dir / v.name).unlink()
            removed += 1
        print(f"[ok] rolled back from {backup.name}/ "
              f"({removed} variant DLLs removed)")
    except Exception as e:  # noqa: BLE001
        print(f"[fail] rollback error: {e} (manual fix: restore {backup})")


# ---------------------------------------------------------------- detect
def _expected_variant(avx: bool, avx2: bool, avx512: bool) -> str:
    """Map host CPU features to the ggml-cpu variant ggml will pick."""
    if avx512:
        return ("skylakex (or newer: icelake/sapphirerapids/zen4/zen5 - "
                "runtime picks the best by CPUID score)")
    if avx2:
        return ("haswell (or zen1..zen5 on AMD - runtime picks by CPUID "
                "score)")
    if avx:
        return "sandybridge"
    return "sse42 / x64 baseline"


def _os_feature_support() -> tuple[bool, bool, bool]:
    """Ask Windows what the OS exposes: (AVX, AVX2, AVX512F)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.IsProcessorFeaturePresent.restype = wintypes.BOOL
    k32.IsProcessorFeaturePresent.argtypes = [wintypes.DWORD]
    # PF_AVX_INSTRUCTIONS_AVAILABLE = 17, PF_AVX2 = 40, PF_AVX512F = 41
    return (
        bool(k32.IsProcessorFeaturePresent(17)),
        bool(k32.IsProcessorFeaturePresent(40)),
        bool(k32.IsProcessorFeaturePresent(41)),
    )


def show_detect() -> None:
    """Report host CPU features, expected variant, and the actually
    registered backends (what llama.cpp will really compute with)."""
    print("=== CPU detect ===")
    machine = platform.machine()
    print(f"  machine       : {machine}")

    dirs = find_bin_dirs()
    if not dirs:
        print("  llama.dll     : not found (nothing to detect)")
        return
    bin_dir = dirs[0]
    print(f"  bin dir       : {bin_dir}")

    if machine in ("AMD64", "x86_64"):
        avx, avx2, avx512 = _os_feature_support()
        print(f"  OS support    : AVX={'yes' if avx else 'no'} "
              f"AVX2={'yes' if avx2 else 'no'} "
              f"AVX512F={'yes' if avx512 else 'no'}")
        expect = _expected_variant(avx, avx2, avx512)
        print(f"  expected pick : ggml-cpu-{expect}")
    else:
        print("  OS support    : NEON family (ARM64)")
        print("  expected pick : armv8.2 / neon variants (runtime picks)")

    # --- what actually registered inside this process -----------------
    try:
        import llama_cpp  # noqa: F401  load the DLL chain

        os.add_dll_directory(str(bin_dir))
        lib = ctypes.CDLL(str(bin_dir / "ggml.dll"))
        lib.ggml_backend_reg_count.restype = ctypes.c_size_t
        # note: dev name/description accessors are inline helpers over the
        # dev->iface vtable (no exports). dev struct layout (x64):
        #   +0  get_name, +8  get_description, +16 get_memory ...
        # we read the fn pointers straight out of the struct.
        _str_fn = ctypes.WINFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p)
        lib.ggml_backend_dev_count.restype = ctypes.c_size_t
        lib.ggml_backend_dev_get.restype = ctypes.c_void_p
        lib.ggml_backend_dev_get.argtypes = [ctypes.c_size_t]

        def _dev_str(dev: int, offset: int) -> str:
            addr = int.from_bytes(ctypes.string_at(dev + offset, 8), "little")
            if not addr:
                return "?"
            return (_str_fn(addr)(dev) or b"").decode("utf-8", "replace")
        load_all = lib.ggml_backend_load_all
        load_all.restype = None
        old = os.getcwd()
        os.chdir(bin_dir)
        try:
            load_all()
        finally:
            os.chdir(old)

        n_dev = int(lib.ggml_backend_dev_count())
        print(f"  registered    : {n_dev} backend device(s)")
        for i in range(n_dev):
            dev = lib.ggml_backend_dev_get(i)
            if not dev:
                continue
            name = _dev_str(dev, 0)
            desc = _dev_str(dev, 8)
            print(f"    - {name}: {desc}")
    except Exception as e:  # noqa: BLE001
        print(f"  registered    : (probe failed: {e})")

    variants = variants_in(bin_dir)
    print(f"  installed     : {len(variants)} variant DLL(s)"
          + (" (official package applied)" if variants
             else " -> run 'python tools/fix_llama_avx.py' to upgrade"))


# ---------------------------------------------------------------- main
def main(argv: list[str]) -> int:
    if "--status" in argv:
        show_status()
        return 0
    if "--detect" in argv:
        show_detect()
        return 0
    if "--rollback" in argv:
        dirs = find_bin_dirs()
        if not dirs:
            print("[skip] llama.dll not found")
            return 0
        do_rollback(dirs[0])
        return 0
    if "--offline" in argv:
        i = argv.index("--offline")
        if i + 1 >= len(argv):
            print("usage: python tools/fix_llama_avx.py --offline <zip>")
            return 1
        return apply(offline_zip=argv[i + 1])
    if any(a.startswith("-") for a in argv):
        print(__doc__)
        return 1
    return apply()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
