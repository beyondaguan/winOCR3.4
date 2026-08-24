"""取词服务（services/capture/selection.py）单测。

覆盖：UIA 直读优先、剪贴板兜底（慢复制轮询）、WM_COPY 第二轮兜底、
全部失败返回 ("", "none")、以及备份/还原剪贴板被调用。
全部用注入的假剪贴板后端，不依赖真实 Windows 窗口 / uiautomation。

这是 3.4 里唯一能稳定单测的取词路径（旧版逻辑全塞在 ui/tk/app.py，
只能靠实机冒烟）—— 抽取后回归成本从「人工实机」降到「CI 自动」。
"""
import sys

import pytest

from winocr.services.capture import selection as sel


class _FakeClipboard:
    """模拟剪贴板后端：backup/restore/read 可控，read 按序列返回值。"""

    def __init__(self, reads):
        self._reads = list(reads)
        self._i = 0
        self.backed_up = None
        self.restored = None

    def backup(self):
        self.backed_up = {"fmt": b"prev"}
        return self.backed_up

    def restore(self, data):
        self.restored = data

    def read(self):
        if self._i < len(self._reads):
            v = self._reads[self._i]
            self._i += 1
            return v
        return ""


def _patch_helpers(monkeypatch, uia_text=""):
    """堵住所有真实 Windows 调用，让取词链在测试里确定性执行。"""
    monkeypatch.setattr(sel, "ensure_uia", lambda: None)
    monkeypatch.setattr(sel, "send_ctrl_c", lambda: None)
    monkeypatch.setattr(sel, "send_wm_copy", lambda: None)
    monkeypatch.setattr(sel, "foreground_title", lambda: "<test>")
    monkeypatch.setattr(sel, "read_uia_selection",
                        lambda log=None: uia_text)


def test_uia_path_takes_priority(monkeypatch):
    """UIA 能取到文本时，绝不碰剪贴板兜底。"""
    _patch_helpers(monkeypatch, uia_text="UIA selected")
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=_FakeClipboard([]))
    assert (text, src) == ("UIA selected", "uia")


def test_clipboard_fallback_on_slow_copy(monkeypatch):
    """UIA 空 → 注入 Ctrl+C → 轮询等到『慢复制』的文本（src=clip）。

    对应旧版 0.2s 固定等待读到空 → 误报「没有可翻译的文本」的回归。
    """
    _patch_helpers(monkeypatch, uia_text="")
    cb = _FakeClipboard(["", "", "", "  slow copy text  "])
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=cb)
    assert src == "clip"
    assert text == "slow copy text"
    # 备份/还原必须被调用（无损用户数据）
    assert cb.backed_up is not None
    assert cb.restored == cb.backed_up


def test_wmcopy_second_round(monkeypatch):
    """UIA 空 + 剪贴板轮询始终空 → 第二轮 WM_COPY 命中（src=wmcopy）。"""
    _patch_helpers(monkeypatch, uia_text="")

    # 第一轮（clip）轮询返回空，第二轮（wmcopy）轮询命中
    def fake_poll(reader, timeout=1.0, interval=0.1):
        fake_poll.n += 1
        return "" if fake_poll.n == 1 else "wm selected"
    fake_poll.n = 0
    monkeypatch.setattr(sel, "poll_clipboard_text", fake_poll)

    cb = _FakeClipboard([""])          # read 永远空，逼出 wmcopy 分支
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=cb)
    assert src == "wmcopy"
    assert text == "wm selected"


def test_all_empty_returns_none_src(monkeypatch):
    """三条路径全失败 → ("", "none")，且备份仍被还原。"""
    _patch_helpers(monkeypatch, uia_text="")
    cb = _FakeClipboard([""])
    monkeypatch.setattr(sel, "poll_clipboard_text",
                        lambda reader, timeout=1.0, interval=0.1: "")
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=cb)
    assert (text, src) == ("", "none")
    assert cb.restored == cb.backed_up


def test_backup_skipped_when_nothing_to_restore(monkeypatch):
    """剪贴板本就空（backup 返回 None）：不应调用 restore（避免误清用户数据）。"""
    _patch_helpers(monkeypatch, uia_text="")

    class _EmptyClip:
        def backup(self):
            return None              # 本就空

        def restore(self, data):
            raise AssertionError("不应调用 restore")

        def read(self):
            return "clip text"

    monkeypatch.setattr(sel, "poll_clipboard_text",
                        lambda reader, timeout=1.0, interval=0.1: reader())
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=_EmptyClip())
    assert (text, src) == ("clip text", "clip")


def test_poll_clipboard_text_timeout_and_exception():
    """轮询：命中慢复制 / 超时返回空 / reader 抛异常安静超时。"""
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        return "" if calls["n"] < 4 else "ready"

    assert sel.poll_clipboard_text(slow, timeout=2.0, interval=0.02) == "ready"
    # 始终空 → 超时
    assert sel.poll_clipboard_text(lambda: "", timeout=0.15, interval=0.03) == ""
    # reader 抛异常 → 安静超时
    def boom():
        raise RuntimeError("x")
    assert sel.poll_clipboard_text(boom, timeout=0.15, interval=0.03) == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
