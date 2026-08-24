# -*- coding: utf-8 -*-
"""控制台编码策略的防回归测试。

背景（真实踩过的坑，别再踩第二次）：
Windows 上 Python 3.6+ 输出到**真控制台**时，底层是 _WindowsConsoleIO，
它把文本层交来的 UTF-8 字节解码成宽字符再调 WriteConsoleW，因此这条路
天生 Unicode 安全，跟 chcp 是几号无关。
此时如果「好心」把 stdout 编码改成 cp936，文本层吐 GBK 字节、底层仍按
UTF-8 解 —— 反而制造出「鑷妫」式乱码。

而输出到**管道/文件**时不走 WriteConsoleW，Python 按自身默认编码写字节，
cmd / type 按控制台代码页解码，这时才需要主动对齐 cp936。

所以铁律：真控制台只改 errors，绝不改 encoding；管道才改 encoding。
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as entry  # noqa: E402


class _FakeStream:
    """记录 reconfigure 收到了什么参数。"""

    def __init__(self):
        self.calls = []

    def reconfigure(self, **kw):
        self.calls.append(kw)


def test_stringio_not_treated_as_console():
    """没有 fileno 的流不能被误判成控制台。"""
    assert entry._is_real_console(io.StringIO()) is False


def test_console_stream_keeps_encoding(monkeypatch):
    """真控制台：encoding 必须保持 None（不动），只放宽 errors。"""
    monkeypatch.setattr(entry, "_is_real_console", lambda s: True)
    out, err = _FakeStream(), _FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    entry._init_console()

    for s in (out, err):
        assert s.calls, "reconfigure 没被调用"
        assert s.calls[0]["encoding"] is None, "真控制台不得改 encoding"
        assert s.calls[0]["errors"] == "replace"


def test_piped_stream_aligns_to_codepage(monkeypatch):
    """管道/文件：encoding 必须对齐到控制台代码页。"""
    if sys.platform != "win32":
        return
    monkeypatch.setattr(entry, "_is_real_console", lambda s: False)
    out = _FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", _FakeStream())

    entry._init_console()

    enc = out.calls[0]["encoding"]
    # 有控制台时应形如 cp936 / cp65001；完全无控制台时允许 None
    assert enc is None or enc.startswith("cp")


def test_init_console_never_raises(monkeypatch):
    """哪怕流是残废的，也绝不能把主流程搞崩。"""
    class _Broken:
        def reconfigure(self, **kw):
            raise OSError("boom")

    monkeypatch.setattr(sys, "stdout", _Broken())
    monkeypatch.setattr(sys, "stderr", None)
    entry._init_console()          # 不抛异常即通过


def test_cli_output_is_gbk_safe():
    """CLI 里不许再出现 GBK 表示不了的字符（✓ ✗ emoji）。

    这些字符在 936 代码页下重定向到文件会变成 ?，
    早期版本还会直接抛 UnicodeEncodeError 打断安装脚本。
    GUI 里的 emoji 不受此限——Tk 内部是 Unicode，与代码页无关。
    """
    text = Path(entry.__file__).read_text(encoding="utf-8")
    bad = sorted({ch for ch in text if not _gbk_ok(ch)})
    assert not bad, f"main.py 含非 GBK 字符: {bad}"


def _gbk_ok(ch: str) -> bool:
    try:
        ch.encode("gbk")
        return True
    except Exception:
        return False
