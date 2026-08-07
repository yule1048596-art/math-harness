from __future__ import annotations

import time

import pytest
import sympy as sp

from math_harness.checks import ComputationTimeout, call_with_timeout

# 这些辅助函数必须定义在模块顶层：sandbox 用 `spawn` 上下文，子进程要按
# 模块名 + 限定名重新导入目标函数。闭包和 lambda 传不过去——这是刻意接受的约束，
# 换成 `fork` 虽然能传闭包，但后端跑着记忆整理线程，在有线程的进程里 fork 不安全。


def _hangs() -> object:
    k = sp.Symbol("k")
    return sp.Sum(1 / k**2, (k, 1, 10**7)).doit()


def _quick() -> object:
    x = sp.Symbol("x")
    return sp.expand((x + 1) ** 3)


def _raises() -> object:
    raise ValueError("worker blew up")


def _echo(value: str, suffix: str = "!") -> str:
    return value + suffix


def test_hanging_computation_is_killed():
    """本机实测 Sum 上界 1e7 会跑到 8 秒不返回，解析器的节点上限拦不住它。"""

    started = time.perf_counter()

    with pytest.raises(ComputationTimeout):
        call_with_timeout(_hangs, timeout_seconds=2)

    # 必须真的在超时附近返回，而不是等它自己算完。
    assert time.perf_counter() - started < 8


def test_normal_computation_returns_its_value():
    result = call_with_timeout(_quick, timeout_seconds=20)

    assert sp.expand(result) == sp.expand((sp.Symbol("x") + 1) ** 3)


def test_arguments_and_keywords_are_passed_through():
    assert call_with_timeout(_echo, "ok", suffix="?", timeout_seconds=20) == "ok?"


def test_worker_exception_is_not_a_timeout():
    """子进程里的异常和超时必须分得开——调用方对两者的处置不同。"""

    with pytest.raises(RuntimeError) as excinfo:
        call_with_timeout(_raises, timeout_seconds=20)

    assert not isinstance(excinfo.value, ComputationTimeout)
    assert "ValueError" in str(excinfo.value)


def test_timeout_has_its_own_type_so_callers_can_map_it_to_errored():
    """超时是「检查没跑完」，不是「断言为假」，不该触发重跑。"""

    with pytest.raises(ComputationTimeout):
        call_with_timeout(_hangs, timeout_seconds=1)


def test_overhead_stays_within_budget():
    """spawn 每次约 0.25 秒。

    这条钉住的是一个设计约束：沙箱要按「一条检查」为粒度，N 次实例化必须在同一个
    子进程里跑完。按次 spawn 会让实例化检查慢到不可用。
    """

    started = time.perf_counter()
    call_with_timeout(_quick, timeout_seconds=20)

    assert time.perf_counter() - started < 3
