# -*- coding: utf-8 -*-
"""P2-9 / P2-10 日志卫生回归测试。

验证：
  - winocr.selection 默认 WARNING（生产静默，DEBUG 诊断被吞）；
  - WINOCR_DEBUG=1 时升级为 DEBUG（诊断落盘 selection.log 可见）；
  - 5 个 P2-10 模块都已接入 logging，不再静默吞异常。
"""
import logging
import os


def _reset(logger):
    logger._winocr_ready = False
    logger.handlers = []


def test_sel_log_default_silent():
    from winocr.services.capture import selection

    os.environ.pop("WINOCR_DEBUG", None)
    _reset(selection._SEL_LOGGER)
    selection._ensure_sel_logging()

    assert selection._SEL_LOGGER.level == logging.WARNING

    rec = []

    class _H(logging.Handler):
        def emit(self, r):
            rec.append(r)

    h = _H()
    selection._SEL_LOGGER.addHandler(h)
    try:
        selection._SEL_LOGGER.debug("silent-debug")
        assert len(rec) == 0, "默认应吞掉 DEBUG，不写任何 handler"
    finally:
        selection._SEL_LOGGER.removeHandler(h)


def test_sel_log_debug_visible_with_env():
    from unittest import mock

    from winocr.services.capture import selection

    os.environ["WINOCR_DEBUG"] = "1"
    _reset(selection._SEL_LOGGER)
    # basicConfig 会往 root 挂 StreamHandler，测试里 mock 掉避免污染控制台
    with mock.patch("logging.basicConfig"):
        selection._ensure_sel_logging()

    assert selection._SEL_LOGGER.level == logging.DEBUG

    rec = []

    class _H(logging.Handler):
        def emit(self, r):
            rec.append(r)

    h = _H()
    selection._SEL_LOGGER.addHandler(h)
    try:
        selection._SEL_LOGGER.debug("visible-debug")
        assert len(rec) == 1
        assert rec[0].message == "visible-debug"
    finally:
        selection._SEL_LOGGER.removeHandler(h)
        os.environ.pop("WINOCR_DEBUG", None)
        _reset(selection._SEL_LOGGER)


def test_modules_expose_logger():
    import winocr.core.capsule as capsule
    import winocr.core.config as config
    import winocr.core.paths as paths
    import winocr.core.pipeline as pipeline
    import winocr.services.win32_hotkey as hotkey

    for m in (capsule, paths, config, pipeline, hotkey):
        assert hasattr(m, "logger"), "%s 应接入 logging.logger" % m.__name__
