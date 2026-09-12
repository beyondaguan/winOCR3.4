# -*- coding: utf-8 -*-
"""WinOCR 3.4 unified multi-thread model downloader (ASCII console output).

Downloads every external resource with parallel HTTP Range requests,
verifies integrity, and places each file at its final location:

  ocr       OCR PP-OCRv6 models (tiny + medium)      -> models/v6_tiny/, models/v6_medium/
  translate Argos zh<->en offline packages           -> vendor/argos_packages/
  llm       Qwen2.5-0.5B + HY-MT1.5-1.8B GGUF        -> models/llama/
  wheel     llama-cpp-python CPU wheel (GitHub)      -> ./ + pip install into current env

Usage:
    python tools/download_all_models.py               # download everything
    python tools/download_all_models.py ocr llm       # selected groups only
    python tools/download_all_models.py --list        # show resources, no download
    python tools/download_all_models.py --threads 16  # parallel chunk count (default 8)
    python tools/download_all_models.py --no-wheel-install

Notes:
  - Resumable: per-chunk .part files; re-running skips finished chunks/files.
  - GitHub URLs are tried through several China-accessible proxies, then direct.
  - Output is ASCII-only so GBK / UTF-8 consoles both work.
"""
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from winocr.core.paths import argos_install_dir, ocr_model_dir  # noqa: E402
from tools import download_argos as argos_dl                       # noqa: E402
from tools import download_ocr_model as ocr_dl                     # noqa: E402

UA = {"User-Agent": "winocr"}
TIMEOUT = 30

# GitHub proxy prefixes (tried in order, direct URL appended automatically)
GH_PROXIES = [
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://mirror.ghproxy.com/",
]

WHEEL_NAME = "llama_cpp_python-0.3.35-py3-none-win_amd64.whl"
WHEEL_GH_PATH = ("https://github.com/abetlen/llama-cpp-python/releases/"
                 "download/v0.3.35/" + WHEEL_NAME)

# --- Domestic, non-GitHub source: conda-forge packages mirrored in China ---
# conda-forge ships prebuilt win-64 llama-cpp-python (PyPI only has sdist).
# The Python bindings are split from the native DLLs (separate conda pkgs);
# we download all of them from Chinese university/cloud mirrors and assemble
# into the current Python environment:
#   Lib/site-packages/llama_cpp/**  (bindings + dist-info)
#   <prefix>/Library/bin/*.dll      (llama/ggml + MKL + vulkan loader)
CONDA_MIRRORS = [
    "https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/win-64/",
    "https://mirrors.huaweicloud.com/anaconda/cloud/conda-forge/win-64/",
    "https://mirrors.ustc.edu.cn/anaconda/cloud/conda-forge/win-64/",
]
# build string per CPython version (conda-forge 0.3.35, build 0)
_CONDA_LCP_BUILDS = {
    (3, 10): "py310h699e580_0",
    (3, 11): "py311h5dfdfe8_0",
    (3, 12): "py312ha1a9051_0",
    (3, 13): "py313h927ade5_0",
    (3, 14): "py314hb98de8c_0",
}
# conda package filename -> DLLs to extract from its Library/bin
_CONDA_DLL_PACKAGES = [
    ("llama.cpp-10588-cpu_mkl_h012c08f_0.conda",
     {"llama.dll", "mtmd.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll",
      "ggml-blas.dll", "ggml-vulkan.dll"}),
    # None = extract every Library/bin/*.dll. mkl_rt.3.dll delay-loads
    # mkl_core.3.dll / mkl_intel_thread.3.dll / mkl_avx*.dll / mkl_vml_*.dll
    # at runtime (not visible in the static import table), so a single-DLL
    # extract crashes with "Cannot load mkl_intel_thread.3.dll".
    ("mkl-2026.1.0-hac47afa_235.conda", None),
    ("llvm-openmp-23.1.1-h49e36cd_0.conda", {"libiomp5md.dll", "libomp.dll"}),
    ("tbb-2023.1.0-hdcfe883_0.conda",
     {"tbb12.dll", "tbbmalloc.dll", "tbbmalloc_proxy.dll", "tbbbind_2_5.dll"}),
    ("libvulkan-loader-1.4.357.0-h477610d_2.conda", {"vulkan-1.dll"}),
]
CONDA_CACHE = ROOT / "vendor" / ".cache" / "conda"


# ---------------------------------------------------------------- manifest
def gh_urls(gh_url):
    return [p + gh_url for p in GH_PROXIES] + [gh_url]


def build_entries():
    """Return list of dicts: group, name, urls, dest, sha256, kind, min_mb."""
    entries = []

    # ---- OCR (URLs / SHA256 reused from tools/download_ocr_model.py) ----
    for tier in ("tiny", "medium"):
        d = ocr_model_dir(tier)
        for part in ("det", "rec"):
            fname, sha = ocr_dl._MODELS[part][tier]
            entries.append(dict(
                group="ocr", name=f"{tier}/{fname}",
                urls=[f"{ocr_dl._BASE}/{part}/{fname}"],
                dest=d / fname, sha256=sha, kind="file", min_mb=0.5,
            ))
        fname, url, sha = ocr_dl._CLS
        entries.append(dict(
            group="ocr", name=f"{tier}/{fname}",
            urls=[url], dest=d / fname, sha256=sha, kind="file", min_mb=0.1,
        ))

    # ---- Argos offline translation (download zip, then extract) ----
    for code, frm, to, url, dirname in argos_dl._PKGS:
        entries.append(dict(
            group="translate", name=f"{code}.argosmodel",
            urls=[url], dest=ROOT / "vendor" / ".cache" / f"{code}.zip",
            sha256=None, kind="argos", min_mb=10,
            install_dir=argos_install_dir() / dirname,
        ))

    # ---- LLM GGUF models (ModelScope) ----
    entries.append(dict(
        group="llm", name="qwen2.5-0.5b-instruct-q4_k_m.gguf",
        urls=["https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/"
              "resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf"],
        dest=ROOT / "models" / "llama" / "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        sha256=None, kind="gguf", min_mb=400,
    ))
    entries.append(dict(
        group="llm", name="HY-MT1.5-1.8B-Q4_K_M.gguf",
        urls=["https://www.modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/"
              "resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf"],
        dest=ROOT / "models" / "llama" / "HY-MT1.5-1.8B-Q4_K_M.gguf",
        sha256=None, kind="gguf", min_mb=1000,
    ))

    # ---- llama-cpp-python: conda-forge CN mirror preferred, GitHub wheel fallback ----
    entries.append(dict(
        group="wheel", name="llama-cpp-python 0.3.35 (conda CN mirror, fallback: GitHub CPU wheel)",
        urls=gh_urls(WHEEL_GH_PATH),
        dest=ROOT / WHEEL_NAME, sha256=None, kind="wheel", min_mb=6.0,
    ))
    return entries


# ------------------------------------------------------------- HTTP helpers
def probe(url):
    """Return (total_size, supports_range)."""
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        cr = r.headers.get("Content-Range", "")
        if r.status == 206 and "/" in cr:
            return int(cr.rsplit("/", 1)[1]), True
        total = int(r.headers.get("Content-Length", 0))
        return total, False


def fetch_range(url, start, end, part_file, counter, lock):
    """Download bytes [start, end] into part_file, resuming from its size."""
    part_file = Path(part_file)
    have = part_file.stat().st_size if part_file.exists() else 0
    length = end - start + 1
    if have >= length:
        with lock:
            counter[0] += length
        return
    req = urllib.request.Request(
        url, headers={**UA, "Range": f"bytes={start + have}-{end}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, \
            open(part_file, "ab") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            with lock:
                counter[0] += len(chunk)


def stream_full(url, dest_part, counter, lock):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(dest_part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            with lock:
                counter[0] += len(chunk)


def download_one(url, dest, threads):
    """Download url -> dest with parallel chunks. Returns True on success.

    Chunked mode uses per-chunk .partNN files so interrupted runs resume
    per chunk; chunks are concatenated into dest after all complete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    total, ranged = probe(url)
    if total <= 0:
        return False
    part = Path(str(dest) + ".part")

    counter, lock = [0], threading.Lock()
    t0 = time.time()

    if not ranged or total < 8 * 1024 * 1024:
        # Small file or server without Range support: single stream, no resume.
        part.unlink(missing_ok=True)
        workers = [threading.Thread(target=stream_full, args=(url, part, counter, lock))]
        chunk_files = []
        expected = total
    else:
        n = min(threads, max(1, total // (8 * 1024 * 1024)))
        step = total // n
        tasks = []
        for i in range(n):
            s = i * step
            e = total - 1 if i == n - 1 else s + step - 1
            cf = Path(f"{dest}.part{i:03d}")
            tasks.append((s, e, cf))
        workers = [threading.Thread(
            target=fetch_range, args=(url, s, e, cf, counter, lock))
            for s, e, cf in tasks]
        chunk_files = [cf for _, _, cf in tasks]
        expected = total

    for w in workers:
        w.start()
    while any(w.is_alive() for w in workers):
        with lock:
            done = counter[0]
        pct = done * 100 // expected if expected else 0
        spd = done / 1024 / 1024 / max(time.time() - t0, 0.1)
        print(f"\r    {done/1048576:8.1f}/{expected/1048576:.1f} MB "
              f"{pct:3d}%  {spd:6.2f} MB/s   ", end="", flush=True)
        time.sleep(0.5)
    for w in workers:
        w.join()
    print()

    if not chunk_files:
        if counter[0] < expected * 0.999:
            print(f"    [INCOMPLETE] got {counter[0]}/{expected} bytes")
            return False
        os.replace(part, dest)
        return True

    # Verify every chunk, then concatenate in order.
    got = sum(cf.stat().st_size for cf in chunk_files if cf.exists())
    if got != total:
        print(f"    [INCOMPLETE] got {got}/{total} bytes; re-run to resume chunks")
        return False
    dest_tmp = Path(str(dest) + ".joining")
    with open(dest_tmp, "wb") as out:
        for cf in chunk_files:
            with open(cf, "rb") as src:
                shutil.copyfileobj(src, out, 1 << 20)
    os.replace(dest_tmp, dest)
    for cf in chunk_files:
        cf.unlink(missing_ok=True)
    return True


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------- post actions
def install_argos(entry):
    dest = entry["install_dir"]
    if argos_dl._valid_install_dir(dest):
        print("    [SKIP] already extracted")
        return True
    zpath = entry["dest"]
    if not argos_dl._is_zip_ok(zpath):
        return False
    tmpdir = Path(tempfile.mkdtemp(prefix="winocr_argos_"))
    try:
        with zipfile.ZipFile(zpath) as zf:
            argos_dl._safe_extract(zf, tmpdir)
        contents = list(tmpdir.iterdir())
        src = contents[0] if len(contents) == 1 and contents[0].is_dir() else tmpdir
        if not argos_dl._valid_install_dir(src):
            print("    [BAD] archive structure incomplete")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        print(f"    [OK] extracted -> {dest.relative_to(ROOT)}")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def install_wheel(path):
    print("    Installing wheel into current Python environment...")
    # pip logs to stderr even on success; capture it so a normal install
    # does not flash red text in PowerShell / Windows Terminal.
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall",
         "-q", str(path)],
        capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        print("    [OK] llama-cpp-python installed")
    else:
        tail = (r.stderr or r.stdout or "").strip()[-2000:]
        if tail:
            print(tail)
        print("    [WARN] pip install failed; install manually:")
        print(f"           \"{sys.executable}\" -m pip install \"{path}\"")
    return r.returncode == 0


def llama_cpp_importable():
    """True only if the compiled shared library actually loads."""
    code = "import llama_cpp; print(llama_cpp.__version__)"
    env = {k: v for k, v in os.environ.items() if k != "LLAMA_CPP_LIB_PATH"}
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, env=env, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _conda_tar(conda_path):
    """Open pkg-*.tar.zst inside a .conda archive. Needs zstandard."""
    import zstandard  # noqa: PLC0415
    zf = zipfile.ZipFile(conda_path)
    pn = next(n for n in zf.namelist() if n.startswith("pkg-"))
    raw = zstandard.ZstdDecompressor().decompress(zf.read(pn))
    return tarfile.open(fileobj=io.BytesIO(raw))


def _fetch_conda(filename):
    """Download one conda package via Chinese mirrors; cache locally."""
    dest = CONDA_CACHE / filename
    if dest.is_file() and zipfile.is_zipfile(dest) and dest.stat().st_size > 100_000:
        print(f"    [CACHE] {filename} ({dest.stat().st_size/1048576:.1f} MB)")
        return dest
    for base in CONDA_MIRRORS:
        url = base + filename
        host = url.split("/")[2]
        print(f"    <- {host} / {filename}")
        try:
            if download_one(url, dest, 8):
                return dest
        except Exception as ex:
            print(f"\n    [FAIL] {type(ex).__name__}: {ex}")
    return None


def install_from_conda_mirror():
    """Install prebuilt llama-cpp-python from CN conda-forge mirrors.

    Returns True on success. Windows x64 only; needs Python 3.10-3.14.
    """
    if sys.platform != "win32" or struct.calcsize("P") * 8 != 64:
        print("    [SKIP] conda-mirror install only supports Windows x64")
        return False
    build = _CONDA_LCP_BUILDS.get(sys.version_info[:2])
    if not build:
        print(f"    [SKIP] no conda package for Python {sys.version_info[:2]}")
        return False

    # zstandard is required to unpack .conda; pull the tiny pure wheel from
    # the Aliyun PyPI mirror if missing (still fully domestic).
    try:
        import zstandard  # noqa: F401
    except ImportError:
        print("    Installing zstandard (to unpack .conda)...")
        # Capture output: pip's normal progress goes to stderr and would
        # show up as flashing red text in PowerShell / Windows Terminal.
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "zstandard", "-q",
             "-i", "https://mirrors.aliyun.com/pypi/simple/"],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-2000:]
            if tail:
                print(tail)
            print("    [FAIL] cannot install zstandard")
            return False

    binding_name = f"llama-cpp-python-0.3.35-{build}.conda"
    files = [binding_name] + [f for f, _ in _CONDA_DLL_PACKAGES]
    CONDA_CACHE.mkdir(parents=True, exist_ok=True)
    fetched = {}
    for fn in files:
        p = _fetch_conda(fn)
        if p is None:
            print("    [FAIL] all conda mirrors failed for this file")
            return False
        fetched[fn] = p

    import site  # noqa: PLC0415
    prefix = Path(sys.prefix)
    # In venvs getsitepackages()[0] can be the prefix itself; pick the
    # entry whose basename is literally site-packages.
    cands = [Path(p) for p in site.getsitepackages()]
    site_pkgs = next((p for p in cands if p.name == "site-packages"),
                     prefix / "Lib" / "site-packages")
    bin_dir = prefix / "Library" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    site_pkgs.mkdir(parents=True, exist_ok=True)

    # 1) Python bindings -> <prefix>/Lib/site-packages/
    print("    Extracting Python bindings...")
    tf = _conda_tar(fetched[binding_name])
    for m in tf.getmembers():
        if not m.isfile() or "Lib/site-packages/" not in m.name:
            continue
        rel = m.name.split("Lib/site-packages/", 1)[1]
        dst = site_pkgs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(tf.extractfile(m).read())

    # 2) Native DLLs -> <prefix>/Library/bin/
    #    wanted=None means take every Library/bin/*.dll (used for MKL,
    #    whose mkl_rt delay-loads a dozen CPU/VML runtime DLLs).
    for fn, wanted in _CONDA_DLL_PACKAGES:
        tf = _conda_tar(fetched[fn])
        n_dll = 0
        for m in tf.getmembers():
            if not m.isfile() or "/bin/" not in m.name:
                continue
            base = m.name.split("/")[-1]
            if wanted is None or base in wanted:
                (bin_dir / base).write_bytes(tf.extractfile(m).read())
                n_dll += 1
        print(f"    {n_dll} DLLs <- {fn}")

    return llama_cpp_importable()


def verify(entry):
    p = entry["dest"]
    if not p.is_file():
        return False
    size_mb = p.stat().st_size / 1048576
    if size_mb < entry["min_mb"]:
        print(f"    [BAD] too small: {size_mb:.1f} MB")
        return False
    if entry["sha256"]:
        actual = sha256_of(p)
        if actual != entry["sha256"]:
            print(f"    [BAD] SHA256 mismatch")
            return False
    if entry["kind"] == "gguf":
        with open(p, "rb") as f:
            if f.read(4) != b"GGUF":
                print("    [BAD] not a GGUF file")
                return False
    if entry["kind"] == "wheel":
        if not zipfile.is_zipfile(p):
            print("    [BAD] not a valid wheel/zip")
            return False
    return True


def already_placed(entry):
    """Final location satisfied (file valid, or argos already extracted)."""
    if entry["kind"] == "argos":
        return argos_dl._valid_install_dir(entry["install_dir"])
    return entry["dest"].is_file() and verify(entry)


# -------------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    threads = 8
    do_install_wheel = True
    if "--threads" in args:
        threads = int(args[args.index("--threads") + 1])
        del args[args.index("--threads") + 1]
        args.remove("--threads")
    if "--no-wheel-install" in args:
        do_install_wheel = False
        args.remove("--no-wheel-install")

    entries = build_entries()
    groups = {a for a in args if not a.startswith("-")}
    valid = {"ocr", "translate", "llm", "wheel"}
    if groups - valid:
        print(f"Unknown group(s): {groups - valid}; choose from {sorted(valid)}")
        return 2
    if groups:
        entries = [e for e in entries if e["group"] in groups]

    print("=" * 64)
    print("  WinOCR 3.4 unified model downloader")
    print("=" * 64)
    total_mb = 0
    for e in entries:
        total_mb += e["min_mb"]
        print(f"  [{e['group']:<9}] {e['name']}  (>= {e['min_mb']:.0f} MB)")
    print(f"  {len(entries)} files, approx >= {total_mb:.0f} MB total, {threads} threads")
    if "--list" in args:
        return 0

    ok, skip, fail = 0, 0, []
    for e in entries:
        print(f"\n[{e['group']}] {e['name']}")

        # llama-cpp-python: prefer the domestic conda-forge mirror
        # (prebuilt, no GitHub, no compiler); GitHub wheel is the fallback.
        if e["group"] == "wheel" and do_install_wheel and llama_cpp_importable():
            print("    [SKIP] llama-cpp-python already importable")
            skip += 1
            continue
        if e["group"] == "wheel" and do_install_wheel:
            print("    Trying domestic conda-forge mirrors (NJU / Huawei / USTC)...")
            if install_from_conda_mirror():
                print("    [OK] installed from domestic conda mirror (CPU+MKL)")
                ok += 1
                continue
            print("    [WARN] conda-mirror route failed, falling back to GitHub wheel")

        # For the wheel, an existing file only skips the download; it must
        # still be pip-installed when the module is not importable.
        if e["kind"] != "wheel" and already_placed(e):
            print("    [SKIP] already in place")
            skip += 1
            continue
        done = False
        if e["kind"] == "wheel" and e["dest"].is_file() and verify(e):
            print("    [CACHE] wheel file already downloaded")
            done = True
        if not done:
            for url in e["urls"]:
                host = url.split("/")[2]
                print(f"    <- {host}")
                try:
                    if download_one(url, e["dest"], threads):
                        done = True
                        break
                except Exception as ex:
                    print(f"\n    [FAIL] {type(ex).__name__}: {ex}")
        if not done:
            print("    [FAIL] all mirrors failed")
            fail.append(e["name"])
            continue
        if not verify(e):
            fail.append(e["name"])
            continue
        if e["kind"] == "argos":
            if not install_argos(e):
                fail.append(e["name"])
                continue
        if e["kind"] == "wheel":
            if do_install_wheel and not install_wheel(e):
                fail.append(e["name"])
                continue
        print("    [OK]")
        ok += 1

    print("\n" + "=" * 64)
    print(f"  Done: {ok} downloaded, {skip} skipped, {len(fail)} failed")
    if fail:
        for n in fail:
            print(f"    FAIL: {n}")
        print("  Re-run this script to resume; finished files are skipped.")
        return 1
    print("  All resources ready. Run: python main.py doctor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
