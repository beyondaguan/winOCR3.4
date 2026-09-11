"""取消机制（P1-8）回归：长任务期间可中断，而非苦等看门狗或重启。

覆盖：run_async 在 cancel_event 置位后丢弃结果 / 发布『已取消』；
"结果检查→回调"时窗内的取消同样不回填（3.4.29 二次校验分支）；
TkUi.do_cancel 立刻释放 busy 锁并置位取消令牌。
"""
import threading
import time

from winocr.core.app import App
from winocr.core.event_bus import Events
from winocr.ui.tk.app import TkUi


class _FakeWindow:
    def __init__(self):
        self.busy = False
        self.status_text = ""

    def set_busy(self, b):
        self.busy = b

    def set_status(self, t):
        self.status_text = t

    def show_sticker(self, *a, **k):
        pass


def test_run_async_respects_cancel_event():
    """置位 cancel_event 后，fn 的结果被丢弃，不回调 on_done，并发布『已取消』。"""
    app = App().build()
    captured = {}
    app.bus.subscribe(Events.STATUS,
                      lambda m: captured.setdefault("status", []).append(m))

    done = {}
    ev = threading.Event()

    def slow():
        time.sleep(0.3)
        return "result"

    t = app.pipeline.run_async(slow,
                               on_done=lambda r: done.setdefault("done", r),
                               cancel_event=ev)
    ev.set()                       # 任务未跑完就取消
    t.join(timeout=5)

    assert "done" not in done, "取消后不应回调 on_done（结果应被丢弃）"
    assert any("已取消" in s for s in captured.get("status", [])), \
        "应发布『已取消』状态"


def test_run_async_without_cancel_still_calls_on_done():
    """对照组：未取消时正常回调 on_done。"""
    app = App().build()
    done = {}
    app.pipeline.run_async(lambda: "ok",
                           on_done=lambda r: done.setdefault("done", r)).join(timeout=5)
    assert done.get("done") == "ok"


def test_run_async_cancel_between_check_and_callback():
    """时窗竞态：第一次取消检查通过后、on_done 执行前被取消 → 不回填。

    用只在第二次 is_set() 才报告已取消的探针事件，精确命中 run_async
    里"结果检查→回调"之间的二次校验分支：删掉该分支（直接 on_done）
    本用例必失败，防止修复被回退。
    """
    app = App().build()
    captured = {}
    app.bus.subscribe(Events.STATUS,
                      lambda m: captured.setdefault("status", []).append(m))

    class _LateCancel:
        """第一次 is_set() → False（结果检查放行），之后 → True（回调前取消）。"""

        def __init__(self):
            self.n = 0

        def is_set(self):
            self.n += 1
            return self.n >= 2

    done = {}
    t = app.pipeline.run_async(lambda: "result",
                               on_done=lambda r: done.setdefault("done", r),
                               cancel_event=_LateCancel())
    t.join(timeout=5)

    assert "done" not in done, "回调前取消不应执行 on_done（过期结果不得回填）"
    assert any("已取消" in s for s in captured.get("status", [])), \
        "应发布『已取消』状态"


def test_do_cancel_releases_busy_and_signals():
    """do_cancel：立刻释放 busy、置位取消令牌、状态栏提示『已取消』。"""
    ui = TkUi()
    ui.app = App().build()
    ui._busy.set()
    ui.window = _FakeWindow()
    ui.post = lambda fn, *a, **k: fn(*a, **k)    # headless：替身为立即执行

    ui.do_cancel()

    assert not ui._busy.is_set(), "取消后应释放 busy 锁"
    assert ui._cancel_event.is_set(), "取消后应置位令牌"
    assert "已取消" in ui.window.status_text


def test_do_cancel_noop_when_idle():
    """空闲（无进行中任务）时 do_cancel 不应误置位令牌。"""
    ui = TkUi()
    ui.app = App().build()
    ui.window = _FakeWindow()
    ui.post = lambda fn, *a, **k: fn(*a, **k)

    ui.do_cancel()

    assert not ui._cancel_event.is_set(), "空闲时取消不应置位令牌"
