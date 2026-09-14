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


# ---------------------------------------------------------------------------
# 以下为「吸收旧版划词 4 项修复」的回归测试：
#   1) INPUT 结构体含 MOUSEINPUT union（x64 下 sizeof==40，否则 SendInput 静默失败）
#   2) UIA 祖先无文本时做有界后代 BFS 扫描
#   3) 应用-策略映射表（chrome→uia / wechat→clip / winrar→wmcopy）
#   4) WM_COPY 目标解析优先焦点控件（GetGUIThreadInfo）
# ---------------------------------------------------------------------------
import ctypes
import sys
import types


def test_input_struct_size_x64():
    """x64 系统 sizeof(INPUT) 必须 == 40（含 MOUSEINPUT 的完整 union）。

    旧实现缺 MOUSEINPUT 导致 sizeof==32，SendInput 返回 0 且
    ERROR_INVALID_PARAMETER —— Ctrl+C 注入从未生效。
    """
    union_size = ctypes.sizeof(sel._INPUTUNION)
    assert union_size >= ctypes.sizeof(sel._KEYBDINPUT)
    assert ctypes.sizeof(sel._INPUT) > union_size          # type 字段 + 对齐
    if sys.maxsize > 2 ** 32:                       # 64 位 Python
        assert ctypes.sizeof(sel._INPUT) == 40      # 必须与系统 sizeof(INPUT) 一致
        assert hasattr(sel._INPUTUNION, "mi")       # union 必须含 MOUSEINPUT


def test_app_strategy_mapping():
    """常见应用映射：浏览器/IDE→uia、通讯软件→clip、老旧程序→wmcopy。"""
    m = sel.SelectionCapturer._APP_STRATEGY
    assert m["chrome.exe"] == "uia"
    assert m["msedge.exe"] == "uia"
    assert m["code.exe"] == "uia"
    assert m["wechat.exe"] == "clip"
    assert m["qq.exe"] == "clip"
    assert m["winrar.exe"] == "wmcopy"
    # 未收录的应用 → auto（默认链）
    ch = sel.SelectionCapturer._STRATEGY_CHAIN
    assert ch.get(m.get("unknown.exe", "auto")) == ch["auto"]


def test_strategy_chain_covers_all_strategies():
    """每条策略链都覆盖 uia/clip/wmcopy 三种方式（只是顺序不同）。"""
    ch = sel.SelectionCapturer._STRATEGY_CHAIN
    for name, chain in ch.items():
        assert set(chain) == {"uia", "clip", "wmcopy"}, name
        if name != "auto":                          # auto 链即默认序，首项 uia
            assert chain[0] == name                 # 首选与策略名一致
    # auto 链与 uia 链同序（默认 UIA 优先）
    assert ch["auto"] == ("uia", "clip", "wmcopy")


def test_capture_reorders_chain_by_app(monkeypatch):
    """微信（clip 优先）时即使 UIA 有文本也先走剪贴板 —— 验证策略链重排生效。"""
    _patch_helpers(monkeypatch, uia_text="UIA selected")
    monkeypatch.setattr(sel, "foreground_app_name", lambda: "wechat.exe")
    monkeypatch.setattr(sel, "wait_modifiers_released", lambda timeout=1.0: True)

    cb = _FakeClipboard(["clip text"])
    monkeypatch.setattr(sel, "poll_clipboard_text",
                        lambda reader, timeout=1.0, interval=0.1: reader())
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=cb)
    assert src == "clip"                            # 非 uia：证明链被重排
    assert text == "clip text"


def test_capture_default_chain_uia_first(monkeypatch):
    """未收录应用走 auto 链：UIA 优先（有文本时不碰剪贴板）。"""
    _patch_helpers(monkeypatch, uia_text="UIA selected")
    monkeypatch.setattr(sel, "foreground_app_name", lambda: "whatever.exe")
    cap = sel.SelectionCapturer()
    text, src = cap.capture(clipboard=_FakeClipboard([]))
    assert (text, src) == ("UIA selected", "uia")


class _FakeGuiInfo:
    def __init__(self, focus=0, active=0):
        self.hwndFocus = focus
        self.hwndActive = active


def test_resolve_copy_target_prefers_focus():
    """WM_COPY 目标：hwndFocus 优先 → hwndActive 兜底 → 顶层窗口。"""
    fg = 111
    fn = lambda tid: (True, _FakeGuiInfo(focus=222, active=333))
    assert sel._resolve_copy_target(fg, thread_info_fn=fn) == 222
    fn = lambda tid: (True, _FakeGuiInfo(focus=0, active=333))
    assert sel._resolve_copy_target(fg, thread_info_fn=fn) == 333
    fn = lambda tid: (True, _FakeGuiInfo(focus=0, active=0))
    assert sel._resolve_copy_target(fg, thread_info_fn=fn) == fg
    # 探测失败 / 抛异常 → 退回顶层窗口
    assert sel._resolve_copy_target(fg, thread_info_fn=lambda tid: (False, None)) == fg
    def boom(tid):
        raise RuntimeError("x")
    assert sel._resolve_copy_target(fg, thread_info_fn=boom) == fg


# ---- UIA 后代扫描（有界 BFS）单测：注入假 uiautomation 模块 ----
class _FakeTextRange:
    def __init__(self, text):
        self._t = text

    def GetText(self, _n):
        return self._t


class _FakeTextPattern:
    def __init__(self, text):
        self._t = text

    def GetSelection(self):
        return [_FakeTextRange(self._t)]


class _FakeNode:
    def __init__(self, text="", children=(), parent=None):
        self._text = text
        self._kids = list(children)
        self._parent = parent

    def GetPattern(self, _pid):
        return _FakeTextPattern(self._text) if self._text else None

    def GetParentControl(self):
        return self._parent

    def GetChildren(self):
        return list(self._kids)


def _install_fake_uia(monkeypatch, focus_node):
    fake = types.SimpleNamespace(
        PatternId=types.SimpleNamespace(TextPattern=42),
        GetFocusedControl=lambda: focus_node,
    )
    monkeypatch.setitem(sys.modules, "uiautomation", fake)


def test_uia_finds_text_in_descendants(monkeypatch):
    """选区挂在焦点控件的后代节点（祖先链全空）→ BFS 扫描命中。"""
    leaf = _FakeNode(text="desc text")
    mid = _FakeNode(children=[leaf])
    focus = _FakeNode(children=[mid])
    _install_fake_uia(monkeypatch, focus)
    assert sel.read_uia_selection() == "desc text"


def test_uia_finds_text_in_ancestor(monkeypatch):
    """选区挂在祖先（浏览器/PDF 常见）→ 向上 3 层命中。"""
    parent = _FakeNode(text="anc text")
    focus = _FakeNode(parent=parent)
    _install_fake_uia(monkeypatch, focus)
    assert sel.read_uia_selection() == "anc text"


def test_uia_descendant_scan_bounded(monkeypatch):
    """后代扫描有界（24 节点）：文本藏在第 30 个孩子里不拖垮取词。"""
    kids = [_FakeNode() for _ in range(30)]
    kids[29] = _FakeNode(text="far away")
    focus = _FakeNode(children=kids)
    _install_fake_uia(monkeypatch, focus)
    assert sel.read_uia_selection() == ""           # 超界文本不命中、不报错


def test_uia_no_focus_returns_empty(monkeypatch):
    """取不到焦点控件（桌面切换瞬间）→ 安静返回空。"""
    _install_fake_uia(monkeypatch, None)
    assert sel.read_uia_selection() == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
