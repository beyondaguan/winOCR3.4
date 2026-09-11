# -*- coding: utf-8 -*-
"""P2-11 常驻 SAPI 宿主的协议级测试（不依赖 Windows / powershell）。

做法：把 `_SapiHost._launch` monkeypatch 成一个 Python 假宿主，它实现与
真实 PowerShell 宿主**完全相同**的行协议：

    stdin 行  : <token>|<rate>|<vol>|<base64(text)>
    stdout 行 : DONE:<token>

从而在无 Windows 的 CI / Linux 上也能验证握手、取消-终止、死亡-回落逻辑。
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.services.tts import TtsService, _SapiHost

# 假宿主：逐行读 stdin，休眠模拟「朗读」，回写 DONE:<token>。
#   WINOCR_FAKE_SPEAK_SEC : 每句休眠秒数（默认 0.05；取消测试设很大）
#   WINOCR_FAKE_DIE=1     : 读到首句后立即退出（模拟宿主崩溃/死亡）
FAKE_HOST = r'''
import os, sys, time
sec = float(os.environ.get("WINOCR_FAKE_SPEAK_SEC", "0.05"))
die = os.environ.get("WINOCR_FAKE_DIE") == "1"
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if line == "QUIT":
        break
    parts = line.split("|")
    if len(parts) < 4:
        continue
    tok = parts[0]
    if die:
        sys.exit(0)
    time.sleep(sec)
    sys.stdout.write("DONE:%s\n" % tok)
    sys.stdout.flush()
'''


def _launch_fake(die=False):
    """启动一个 Python 假宿主进程，返回 Popen（与真实 _launch 同签名语义）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(FAKE_HOST)
        path = fh.name
    env = dict(os.environ)
    if die:
        env["WINOCR_FAKE_DIE"] = "1"
    else:
        env.pop("WINOCR_FAKE_DIE", None)
    env["WINOCR_FAKE_SPEAK_SEC"] = os.environ.get(
        "WINOCR_FAKE_SPEAK_SEC", "0.05")
    return subprocess.Popen(
        [sys.executable, path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1, env=env)


def _patch_host_launch(host, die=False):
    host._launch = lambda: _launch_fake(die=die)


def test_host_handshake_ok():
    """正常握手：假宿主回 DONE，speak 返回 True。"""
    host = _SapiHost()
    _patch_host_launch(host)
    cancel = threading.Event()
    try:
        assert host.speak("你好，世界。", cancel) is True
    finally:
        host.stop()


def test_host_cancel_terminates():
    """取消：宿主正在「朗读」时被 terminate，speak 返回 True 且进程已回收。"""
    host = _SapiHost()
    _patch_host_launch(host)
    os.environ["WINOCR_FAKE_SPEAK_SEC"] = "30"   # 假宿主会休眠 30s
    cancel = threading.Event()
    result = {}

    def run():
        result["v"] = host.speak("很长很长的一段朗读内容。", cancel)

    t = threading.Thread(target=run)
    t.start()
    # 等宿主真正启动（Popen 完成、_proc 非空）再取消，避免负载下 cancel
    # 先于宿主启动导致竞态；启动后再留 0.15s 让假宿主进入「朗读」(sleep)。
    deadline = time.time() + 5
    while host._proc is None and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.15)
    cancel.set()                     # 用户点取消
    t.join(timeout=10)
    os.environ.pop("WINOCR_FAKE_SPEAK_SEC", None)
    assert not t.is_alive(), "取消后 speak 应在超时内返回"
    assert result.get("v") is True
    assert host._proc is None, "取消后常驻宿主应被回收（下次重建）"


def test_host_dead_falls_back_to_oneshot():
    """宿主死亡：常驻路径返回 False，_speak_sapi 回落 _speak_sapi_oneshot。"""
    svc = TtsService()
    host = svc._get_sapi_host()
    _patch_host_launch(host, die=True)
    called = {"oneshot": False}

    def fake_oneshot(text, cancel, rate=0, vol=0):
        called["oneshot"] = True
        return True

    svc._speak_sapi_oneshot = fake_oneshot
    ok = svc._speak_sapi("测试文本", threading.Event())
    assert ok is True
    assert called["oneshot"] is True, "常驻死亡后应回落一次性 subprocess"


def test_host_singleton():
    """_get_sapi_host 返回同一单例（跨多次朗读复用，不重建进程）。"""
    svc = TtsService()
    a = svc._get_sapi_host()
    b = svc._get_sapi_host()
    assert a is b
    svc._stop_host()
    assert svc._sapi_host is None


def test_stop_keeps_host_then_stop_host_reclaims():
    """stop() 保留常驻宿主复用（3.4.14 起）；_stop_host() 才回收置空。

    历史（2026-09-12 勘误）：本用例原名 test_stop_reclaims_host，断言
    stop() 走 _stop_host 回收宿主——那是常驻宿主改版前的旧语义。改版后
    stop() 只掐取消位+播放器，宿主保留供下次朗读复用（省 1.4s 冷启动），
    回收职责移至 _stop_host()（atexit / 显式调用）。此前长期被误判为
    "SAPI COM 环境问题"，实为测试未随设计演进。
    """
    svc = TtsService()
    host = svc._get_sapi_host()
    _patch_host_launch(host)          # 仅创建对象，不主动 launch
    svc.stop()
    assert svc._sapi_host is host, "stop() 应保留常驻宿主供下次朗读复用"
    svc._stop_host()                  # atexit / 显式回收路径
    assert svc._sapi_host is None, "_stop_host() 应回收宿主（引用置空）"
