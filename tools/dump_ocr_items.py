# -*- coding: utf-8 -*-
"""导出 RapidOCR 真实识别项（含四点框），用于调试 structure.py 版面重建。

用法（在本机，WinOCR 所在环境，需已安装 rapidocr/PIL）：
    python tools/dump_ocr_items.py <图片路径>
    python tools/dump_ocr_items.py              # 或读取剪贴板图片

输出（写到桌面，方便直接发回）：
    ~/Desktop/ocr_debug_items.json      # 原始识别项 [{box,text,score}, ...]
    ~/Desktop/ocr_debug_items.txt       # 结构化重建结果 + 扁平分行结果

拿到 JSON 后，把「识别项」逐条对照重建逻辑，即可精确定位列/行聚类偏差，
不必再靠截图猜框。
"""
import json
import os
import sys

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def _load_image():
    if len(sys.argv) > 1:
        from PIL import Image
        return Image.open(sys.argv[1]).convert("RGB")
    # 否则读剪贴板图片
    from PIL import ImageGrab
    img = ImageGrab.grabclipboard()
    if isinstance(img, list):               # 文件列表，取第一张
        img = img[0]
    if img is None:
        sys.exit("没有提供图片路径，且剪贴板无图片。用法：python tools/dump_ocr_items.py <图片>")
    return img.convert("RGB") if not hasattr(img, "convert") else img


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from winocr.services.ocr.rapidocr import RapidOcrEngine

    image = _load_image()
    print(f"图片尺寸: {image.size}")

    engine = RapidOcrEngine()
    engine.structured = True

    # 与 recognize() 走完全相同的预处理，保证导出框 = structure.py 实际看到的框
    proc = engine._preprocess_image(image) if engine.preprocess else image

    # 1) 原始识别项
    items = engine._run(proc)
    payload = []
    for box, text, score in items:
        payload.append({"box": box, "text": text, "score": round(float(score), 4)})
    json_path = os.path.join(DESKTOP, "ocr_debug_items.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[1] 识别项 {len(payload)} 条 -> {json_path}")

    # 2) 结构化重建 + 扁平分行（各存一份，对照看）
    res = engine.recognize(image)
    txt_path = os.path.join(DESKTOP, "ocr_debug_items.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("===== 结构化输出（rebuild_markdown_from_items）=====\n")
        f.write(res.text or "")
        f.write("\n\n===== 扁平分行（_group_by_lines，未开结构化时的文本）=====\n")
        f.write("\n".join(res.lines or []))
    print(f"[2] 识别结果 -> {txt_path}")

    # 3) 顺便打印精简版：每项 (top, left, right, bottom, text) 便于直接看
    print("\n===== 精简识别项（top,left,right,bottom,text）=====")
    for box, text, score in items:
        if not box:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        print(f"  top={min(ys):6.1f} left={min(xs):6.1f} right={max(xs):6.1f} "
              f"bottom={max(ys):6.1f}  {text!r}")
    print("\n请把 ocr_debug_items.json 发回给调试。")
    print(f"（提示：若上面有 top 相同的两行相邻，说明换行续行被拆分成了独立框。）")


if __name__ == "__main__":
    main()
