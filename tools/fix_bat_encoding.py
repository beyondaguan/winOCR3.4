# -*- coding: utf-8 -*-
"""把仓库里的 .bat 统一成「GBK 编码 + CRLF 行尾」。

为什么必须这样：
  cmd.exe 读取批处理文件时，是按**当前控制台代码页**逐行解码的。
  中文 Windows 默认代码页 936(GBK)，若 .bat 存成 UTF-8，中文就会被当成
  GBK 字节解释 —— 这就是「涓枃涔卞コ」式乱码的根因。
  在文件里写 `chcp 65001` 也救不了：那一行生效前，解析器已经在按 936 读了，
  而且 cmd 对无 BOM 的 UTF-8 批处理支持很差（if/for 多行块容易被截断）。

  行尾同理：cmd 对 LF 行尾的 `if (...) else (...)` 块解析不稳，必须 CRLF。

用法：
    python tools/fix_bat_encoding.py          # 修复
    python tools/fix_bat_encoding.py --check  # 只检查，不改（可用于 CI）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".git", "__pycache__", "build", "dist"}


def iter_bats():
    for p in sorted(ROOT.rglob("*.bat")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def read_text_any(p: Path) -> str:
    """无论原文件是 UTF-8 / GBK / 带 BOM，都读成 str。"""
    raw = p.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


def normalize(text: str) -> bytes:
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"
    return body.replace("\n", "\r\n").encode("gbk")


def main() -> int:
    check_only = "--check" in sys.argv
    bad = 0
    for p in iter_bats():
        rel = p.relative_to(ROOT)
        try:
            want = normalize(read_text_any(p))
        except UnicodeEncodeError as e:
            print(f"[!] {rel}  含 GBK 无法表示的字符: {e.object[e.start:e.end]!r}")
            bad += 1
            continue
        if p.read_bytes() == want:
            print(f"[+] {rel}  GBK + CRLF，正常")
            continue
        bad += 1
        if check_only:
            print(f"[-] {rel}  编码/行尾不合规")
        else:
            p.write_bytes(want)
            print(f"[*] {rel}  已转为 GBK + CRLF ({len(want)} 字节)")
    if check_only and bad:
        print(f"\n{bad} 个文件不合规，运行 `python tools/fix_bat_encoding.py` 修复。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
