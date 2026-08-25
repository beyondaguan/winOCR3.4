# -*- coding: utf-8 -*-
"""WinOCR PP-OCRv6 模型下载器（ModelScope 官方源，纯 ASCII 输出）。

用法：
    python tools/download_ocr_model.py [tiny|small|medium]   # 默认 medium

说明：
  - tiny / medium 需从 ModelScope（RapidAI/RapidOCR，官方仓库）下载到 models/v6_{tier}/；
  - small 模型 rapidocr pip 包已自带（PP-OCRv6_det_small.onnx 等），本脚本可跳过；
  - 任何失败都不影响现有模型（引擎自动回退 tiny）。
  - URL/SHA256 取自 rapidocr 包内 default_models.yaml（唯一真相来源）。
"""
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from winocr.core.paths import ocr_model_dir  # noqa: E402

_MIN_VALID_MODEL_SIZE = 100 * 1024      # <100KB 视为占位/残缺，拒收

# 源：RapidAI/RapidOCR ModelScope 仓库（v3.9.2，与 default_models.yaml 一致）
_BASE = ("https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
         "v3.9.2/onnx/PP-OCRv6")
# task: (文件名, SHA256)
_MODELS = {
    "det": {
        "tiny": ("PP-OCRv6_det_tiny.onnx",
                 "f42c0fbd294d95eac1a550e131b277dac97462c8025fa4b6c3cec1b7894bd3d5"),
        "small": ("PP-OCRv6_det_small.onnx",
                  "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
        "medium": ("PP-OCRv6_det_medium.onnx",
                   "92078b7355007ccfffcd4c8cd441a3afd4538904d06881b29a155e1e679907c2"),
    },
    "rec": {
        "tiny": ("PP-OCRv6_rec_tiny.onnx",
                 "e16e242de5937ad92609223f19bc2aff3727ee40b095f996907c24749bad251b"),
        "small": ("PP-OCRv6_rec_small.onnx",
                  "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
        "medium": ("PP-OCRv6_rec_medium.onnx",
                   "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b"),
    },
}
# cls 全档位通用
_CLS = ("ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
        "v3.9.2/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c")


def _valid(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= _MIN_VALID_MODEL_SIZE
    except OSError:
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, expect_sha: str) -> bool:
    try:
        print(f"  <- {url.split('/')[-1]}")
        req = urllib.request.Request(url, headers={"User-Agent": "winocr"})
        r = urllib.request.urlopen(req, timeout=180)
        data = r.read()
        if len(data) < _MIN_VALID_MODEL_SIZE:
            print(f"     [拒绝] 文件过小 {len(data)}B，疑似占位")
            return False
        dest.write_bytes(data)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expect_sha:
            print(f"     [拒绝] SHA256 不符: {actual[:16]}… != {expect_sha[:16]}…")
            dest.unlink(missing_ok=True)
            return False
        print(f"     OK {len(data) / 1024:.0f} KB (SHA256 校验通过)")
        return True
    except Exception as e:
        print(f"     FAIL: {type(e).__name__}: {e}")
        return False


def main() -> int:
    tier = sys.argv[1] if len(sys.argv) > 1 else "medium"
    if tier not in ("tiny", "small", "medium"):
        print("用法: python tools/download_ocr_model.py [tiny|small|medium]")
        return 2

    d = ocr_model_dir(tier)
    d.mkdir(parents=True, exist_ok=True)
    print(f"下载 PP-OCRv6 {tier} 模型 -> {d}")

    if tier == "small":
        # small 优先用 pip 包自带；仅当缺时才提示可手动下载
        try:
            import rapidocr
            pkg = Path(os.path.dirname(rapidocr.__file__)) / "models"
            if _valid(pkg / "PP-OCRv6_det_small.onnx") and _valid(pkg / "PP-OCRv6_rec_small.onnx"):
                print("  small 模型已随 rapidocr 包自带，无需下载。")
                print("  引擎 model_type=small 时直接引用包内模型，零下载。")
                return 0
        except Exception:
            pass
        print("  small 未随包自带，改走在线下载。")

    ok = 0
    tasks = []
    for part in ("det", "rec"):
        fname, sha = _MODELS[part][tier]
        tasks.append((f"{_BASE}/{part}/{fname}", d / fname, sha))
    # cls 全档位通用
    fname, url, sha = _CLS
    tasks.append((url, d / fname, sha))

    for url, dest, sha in tasks:
        if _valid(dest):
            if _sha256(dest) == sha:
                print(f"  [跳过] {dest.name} 已存在且校验通过")
                ok += 1
                continue
            print(f"  [跳过] {dest.name} 存在但校验失败，重新下载")
        print(f"  [{dest.name}]")
        if _download(url, dest, sha):
            ok += 1

    print(f"\n结果: {ok}/{len(tasks)} 个文件就绪")
    if ok == len(tasks):
        print(f"模型可用。config 的 ocr.model_type 设为 {tier} 后生效；")
        print("下载中途失败不影响现有模型（引擎自动回退 tiny）。")
        return 0
    print("部分下载失败。已保留可用文件，重跑本脚本会跳过已成功的部分。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
