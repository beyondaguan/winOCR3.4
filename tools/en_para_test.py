"""生成 3 张不同排版的英文段落图，跑 RapidOCR 管线验证段落分组（A+B）。

不依赖任何网页/截图，纯本地合成，用于确认：
  - 单段多行 -> 1 段
  - 双段带大间距 -> 2 段
  - 双段带首行缩进（无间距）-> 2 段
"""
import os
import sys

import PIL
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FONT_PATH = r"C:\Windows\Fonts\arial.ttf"


def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def render(text_lines, line_gap, para_breaks, indent_first=0, w=900, font_size=26):
    """text_lines: list[str]; para_breaks: set of line-index after which a
    段落间隙应插入；indent_first: 每段首行额外左缩进像素。"""
    font = load_font(font_size)
    pad = 30
    line_h = font_size + line_gap
    # 估算高度
    n_para_gaps = len(para_breaks)
    h = pad * 2 + line_h * len(text_lines) + 55 * n_para_gaps
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = pad
    for i, line in enumerate(text_lines):
        x = pad + (indent_first if i == 0 or (i - 1) in para_breaks else 0)
        d.text((x, y), line, fill="black", font=font)
        y += line_h
        if i in para_breaks:
            y += 55
    return img


def make_images(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = {}

    # 1) 单段落：4 行，紧凑行距，无段间间隙
    p1 = os.path.join(out_dir, "en_single.png")
    render(
        [
            "The quick brown fox jumps over the lazy dog near the riverbank.",
            "Pack my box with five dozen liquor jugs and ship them abroad.",
            "How vexingly quick daft zebras jump; the moon wanes nightly.",
            "We promptly judged antique ivory buckles for the quizzical king.",
        ],
        line_gap=6,
        para_breaks=set(),
    ).save(p1)
    paths["single"] = p1

    # 2) 双段落：各 3 行，中间大间距
    p2 = os.path.join(out_dir, "en_two_gap.png")
    render(
        [
            "Artificial intelligence reshapes how teams collect and analyze data.",
            "Models trained on large corpora generalize across many domains.",
            "Evaluation remains the hardest unsolved part of the pipeline.",
            "Market research demands evidence, not confident speculation.",
            "Segment users by behavior before drafting any product strategy.",
            "Validate assumptions with real interviews and usage telemetry.",
        ],
        line_gap=12,
        para_breaks={2},
    ).save(p2)
    paths["two_gap"] = p2

    # 3) 双段落：首行缩进，无垂直间距（缩进式分段）
    p3 = os.path.join(out_dir, "en_indent.png")
    render(
        [
            "Climate policy requires coordination between regulators and industry.",
            "Subsidies alone will not close the gap without clear standards.",
            "    Supply chains must report emissions with verified methodology.",
            "Transparency builds the trust that long-term planning depends on.",
        ],
        line_gap=12,
        para_breaks={1},
        indent_first=55,
    ).save(p3)
    paths["indent"] = p3

    return paths


def main():
    out_dir = os.path.join(ROOT, "tools", "_en_para_imgs")
    paths = make_images(out_dir)

    from winocr.services.ocr.rapidocr import RapidOcrEngine

    engine = RapidOcrEngine()
    engine.preprocess = True
    engine.model_type = "tiny"
    engine.structured = False
    engine.auto_upgrade = True

    for mode in ("A", "B", "A+B"):
        engine.paragraph_mode = mode
        print("#" * 64)
        print(f"##########  paragraph_mode = {mode}  ##########")
        print("#" * 64)
        for name, path in paths.items():
            img = Image.open(path).convert("RGB")
            res = engine.recognize(img)
            print("-" * 60)
            print(f"[{name}]  lines={len(res.lines)}  paragraphs={len(res.paragraphs)}")
            print(f"  para_ids={res.para_ids}")
            print("  --- text ---")
            print(res.text)
            print()


if __name__ == "__main__":
    main()
