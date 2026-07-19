# -*- coding: utf-8 -*-
"""
agent_queue 单测

通过 stub Agent 覆盖：
1. 基本入队 + worker 执行
2. 循环调用被检测并拒绝
3. 达到 max_depth 拒绝
4. worker 异常被捕获并返回错误字符串
"""
import threading
import time

from dumplingsAI.agent_queue import AgentQueue, get_call_chain


class _StubAgent:
    """一个轻量 stub，模拟 Agent.conversation_with_tool 的同步行为"""

    def __init__(self, uuid, *, raises: Exception | None = None, sleep: float = 0):
        self.uuid = uuid
        self.name = f"agent_{uuid[:6]}"
        self._raises = raises
        self._sleep = sleep
        self.calls = 0

    def conversation_with_tool(self, message: str) -> str:
        self.calls += 1
        if self._sleep:
            time.sleep(self._sleep)
        if self._raises:
            raise self._raises
        return f"reply_from_{self.uuid} to: {message}"


def test_basic_submit_runs_call_fn_and_returns_result():
    q = AgentQueue(workers=1)
    a = _StubAgent("u-aaa")
    try:
        result = q.submit(target_uuid="u-aaa",
                          call_fn=lambda: a.conversation_with_tool("hi"))
    finally:
        q.shutdown(timeout=3)

    assert result == "reply_from_u-aaa to: hi"
    assert a.calls == 1
    assert q.worker_count() == 0  # idle timeout 后 worker 退出


def test_call_chain_in_thread_local_after_submit():
    """worker 在执行 call_fn 时应把 Job 的 chain 注入到 thread-local

    第一次 submit 模拟"用户→AgentA"，chain 是空（用户不在任何 Job 链里）。
    Job 自己的 chain 应该是 [agent.uuid]。
    """
    q = AgentQueue(workers=1)
    captured: dict = {}

    def capture():
        captured["chain"] = list(get_call_chain())
        return "ok"

    try:
        q.submit(target_uuid="u-aaa", call_fn=capture, caller_chain=[])
    finally:
        q.shutdown(timeout=3)

    assert captured["chain"] == ["u-aaa"]


def test_nested_call_chain_propagates():
    """worker 在跑 Job A 时，chain=[A]；里面 submit Job B 后
    B 的 worker 看到的 chain 是 [A, B]（通过 thread-local 继承）。"""
    q = AgentQueue(workers=2)
    captured: dict = {}

    def call_a():
        # 第一次进 A 的 worker，chain 是 [A]
        captured["a_chain"] = list(get_call_chain())
        # 在 A 内部 submit 到 B
        result_b = q.submit(
            target_uuid="u-bbb",
            call_fn=call_b,
            caller_chain=list(get_call_chain()),
        )
        return f"A got: {result_b}"

    def call_b():
        # 进 B 的 worker 时，chain 应是 [A, B]
        captured["b_chain"] = list(get_call_chain())
        return "reply_from_u-bbb"

    try:
        outer = q.submit(
            target_uuid="u-aaa",
            call_fn=call_a,
            caller_chain=[],
        )
    finally:
        q.shutdown(timeout=5)

    assert "A got: reply_from_u-bbb" in outer
    assert captured["a_chain"] == ["u-aaa"]
    assert captured["b_chain"] == ["u-aaa", "u-bbb"]


def test_cycle_rejected():
    q = AgentQueue(workers=1)
    a = _StubAgent("u-aaa")
    result = q.submit(
        target_uuid="u-aaa",
        call_fn=lambda: a.conversation_with_tool("x"),
        caller_chain=["u-aaa", "u-bbb"],
    )
    q.shutdown(timeout=3)
    assert "循环调用" in result
    assert a.calls == 0


def test_max_depth_rejected():
    q = AgentQueue(max_depth=3, workers=1)
    a = _StubAgent("u-zzz")
    result = q.submit(
        target_uuid="u-zzz",
        call_fn=lambda: a.conversation_with_tool("x"),
        caller_chain=["a", "b", "c"],  # 长度 3 == max_depth
    )
    q.shutdown(timeout=3)
    assert "最大调用深度" in result
    assert a.calls == 0


def test_call_fn_exception_returns_error_string():
    q = AgentQueue(workers=1)
    a = _StubAgent("u-aaa", raises=RuntimeError("boom"))
    result = q.submit(
        target_uuid="u-aaa",
        call_fn=lambda: a.conversation_with_tool("x"),
    )
    q.shutdown(timeout=3)
    assert "调用失败" in result
    assert "boom" in result


def test_two_workers_run_in_parallel():
    """验证 pool=2 时两个 Job 可以并发执行（用 sleep 串证）"""
    q = AgentQueue(workers=2)
    a = _StubAgent("u-aaa", sleep=0.2)
    b = _StubAgent("u-bbb", sleep=0.2)
    start = time.monotonic()
    try:
        # 一次提交两个 Job（手动起两个线程去 submit）
        results = {}

        def t1():
            results["a"] = q.submit(
                target_uuid="u-aaa",
                call_fn=lambda: a.conversation_with_tool("x"),
            )

        def t2():
            results["b"] = q.submit(
                target_uuid="u-bbb",
                call_fn=lambda: b.conversation_with_tool("y"),
            )

        th1 = threading.Thread(target=t1)
        th2 = threading.Thread(target=t2)
        th1.start()
        th2.start()
        th1.join()
        th2.join()
    finally:
        q.shutdown(timeout=3)
    elapsed = time.monotonic() - start

    # 两个 0.2s Job 如果串行需要 ~0.4s，并行应 < 0.35s
    assert elapsed < 0.35, f"应并发执行，但耗时 {elapsed:.3f}s"
    assert "u-aaa" in results["a"]
    assert "u-bbb" in results["b"]
