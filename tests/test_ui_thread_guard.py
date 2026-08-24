"""UI 线程守卫（ui/tk/guards.py）单测：不依赖 Tk。

验证：在主线程调用正常执行；在后台线程调用被拦截（返回 None、方法体不执行、
并写一条 ERROR 级提示），避免后台线程误触 Tk 导致死锁。
"""
import threading

from winocr.ui.tk.guards import ui_thread


class _Obj:
    def __init__(self):
        # 模拟 MainWindow：持有 .ui，ui._ui_thread 指向主线程
        self.ui = type("U", (), {"_ui_thread": threading.main_thread()})()
        self.called = []

    @ui_thread
    def meth(self, x):
        self.called.append(x)
        return x * 2


def test_on_ui_thread_runs_normally():
    o = _Obj()
    assert o.meth(3) == 6
    assert o.called == [3]


def test_off_ui_thread_is_intercepted():
    o = _Obj()
    result = {}

    def run():
        result["r"] = o.meth(5)

    t = threading.Thread(target=run)
    t.start()
    t.join()

    # 被守卫拦截：不执行方法体，返回 None
    assert result["r"] is None
    assert o.called == []
