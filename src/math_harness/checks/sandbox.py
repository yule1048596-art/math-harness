from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from typing import Any

# 把可能长时间运行的符号计算放进可杀死的子进程。
#
# 为什么必须有：阶段 B 要给安全解析器放开 `Sum` / `Integral` / 矩阵，而本机实测
# `Sum(1/k**2, (k, 1, 10**7)).doit()` 和 12 阶符号矩阵行列式都能跑超过 8 秒不返回。
# 解析器的节点数与字符数上限挡不住这类——它们的表达式很短，代价全在求值。
#
# 信号超时（SIGALRM）不够用：它只在主线程有效，而且打断不了陷在 C 扩展里的 SymPy
# 调用。子进程可以直接杀。
#
# 这也顺带关掉 README 里挂了很久的一条已知边界——「符号计算仍在主进程运行」。这不是
# 完整沙箱：它不限制内存、不隔离文件系统，只保证**算不完的能被杀掉**。面向不受信任的
# 多用户服务仍需更强隔离。
#
# 两条使用约束，都由测试钉住：
#
#   1. **目标函数必须是模块顶层函数。** 用 `spawn` 而不是 `fork`，子进程按模块名 +
#      限定名重新导入它，闭包与 lambda 传不过去。选 spawn 是因为后端跑着记忆整理
#      线程，在有线程的进程里 fork 不安全。
#   2. **按「一条检查」为粒度调用，不要按「一次实例化」。** 本机实测 spawn 每次约
#      0.25 秒，N 次采样必须在同一个子进程里跑完，否则实例化检查会慢到不可用。


DEFAULT_TIMEOUT_SECONDS = 5.0


class ComputationTimeout(RuntimeError):
    """符号计算超时并被终止。**不等于断言为假**，调用方应记为 ERRORED。"""


def _worker(
    queue: Any, function: Callable[..., Any], args: tuple, kwargs: dict
) -> None:
    try:
        queue.put(("ok", function(*args, **kwargs)))
    except Exception as exc:  # noqa: BLE001
        # 异常要跨进程传回，但异常对象本身未必可 pickle，所以只传类型与文本。
        queue.put(("error", f"{exc.__class__.__name__}: {exc}"))


def call_with_timeout[T](
    function: Callable[..., T],
    *args: Any,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> T:
    """在子进程里执行，超时即杀。

    返回值必须可 pickle——SymPy 表达式可以。不可 pickle 的返回值会被当成执行失败，
    这是刻意的：调用方应当只把可序列化的结果跨出边界。
    """

    # fork 会复制父进程状态，在某些平台上与线程共存不安全；spawn 更慢但可预期。
    context = multiprocessing.get_context("spawn")
    queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_worker, args=(queue, function, args, kwargs), daemon=True
    )
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(1.0)
        if process.is_alive():
            process.kill()
            process.join(1.0)
        raise ComputationTimeout(f"符号计算超过 {timeout_seconds} 秒未完成，已终止。")

    if queue.empty():
        raise ComputationTimeout("子进程未返回结果即退出。")

    status, payload = queue.get()
    if status == "error":
        raise RuntimeError(payload)
    return payload
