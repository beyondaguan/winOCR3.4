# -*- coding: utf-8 -*-
"""cpu_limit（Job Object CPU 占用率硬限制）单测。

限制值经 QueryInformationJobObject 从内核读回验证，确认配额真的写入。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from winocr.core.cpu_limit import (  # noqa: E402
    _MAX_PERCENT,
    _MIN_PERCENT,
    apply_cpu_limit,
    applied_percent,
    effective_percent,
    query_cpu_rate,
)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    """测试默认不受环境变量干扰。"""
    monkeypatch.delenv("WINOCR_CPU_LIMIT", raising=False)


def test_apply_and_readback():
    """70% 设置成功且内核读回 CpuRate == 7000。"""
    assert apply_cpu_limit(70) is True
    assert applied_percent() == 70
    assert query_cpu_rate() == 7000


def test_clamp_low():
    """低于 10% 钳到 10%（Job Object 最低配额）。"""
    assert apply_cpu_limit(1) is True
    assert applied_percent() == _MIN_PERCENT
    assert query_cpu_rate() == _MIN_PERCENT * 100


def test_clamp_high():
    """高于 95% 钳到 95%（100% 等于没限）。"""
    assert apply_cpu_limit(100) is True
    assert applied_percent() == _MAX_PERCENT
    assert query_cpu_rate() == _MAX_PERCENT * 100


def test_zero_ignored():
    """0 = 不限制，不创建 Job、不生效。"""
    assert apply_cpu_limit(0) is False


def test_idempotent_same_percent():
    """同百分比重复调用幂等（不重复设置）。"""
    assert apply_cpu_limit(60) is True
    assert apply_cpu_limit(60) is True
    assert applied_percent() == 60
    assert query_cpu_rate() == 6000


def test_change_percent_updates():
    """同进程可更新为不同百分比（Job 复用、配额重设）。"""
    assert apply_cpu_limit(70) is True
    assert apply_cpu_limit(50) is True
    assert applied_percent() == 50
    assert query_cpu_rate() == 5000


def test_env_override(monkeypatch):
    """环境变量 WINOCR_CPU_LIMIT 的读取。"""
    monkeypatch.setenv("WINOCR_CPU_LIMIT", "45")
    assert effective_percent() == 45
    monkeypatch.setenv("WINOCR_CPU_LIMIT", "abc")
    assert effective_percent() == 0  # 非法值按 0（不限）处理
    monkeypatch.delenv("WINOCR_CPU_LIMIT")
    assert effective_percent() == 0
