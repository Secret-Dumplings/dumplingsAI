# -*- coding: utf-8 -*-
"""
Agent 全局任务队列
==================

设计目标
--------
- **防止递归栈过深**：`ask_for_help` 改为入队 + Event.wait，不再同步递归调用
- **顺次响应**：worker pool 中的每个 worker 一次只处理一个 Job
- **循环检测**：每个 Job 携带 caller chain，target 在 chain 里就拒绝
- **深度限制**：超过 max_depth 拒绝入队，避免恶意/失控循环

工作模型
--------
```
Driver thread            Worker pool (默认 2 worker)
─────────────            ───────────────────────────
agent.conv("...")  ────►  enqueue Job A
  wait Event A                │
                              ▼ worker 0 picks Job A
                              target.conv("...")
                                  │
                                  ├─ LLM 返 ask_for_help
                                  ├─ enqueue Job B
                                  ├─ worker 0 blocks (Event B)
                                  │
                                  │  worker 1 picks Job B
                                  │  other.conv("...")
                                  │  returns → set Event B
                                  │
                              ◀── worker 0 resumes
                              target continues
                              attempt_completion
                              returns → set Event A
  ◀── driver resumes
final answer
```

只要 worker pool ≥ 2 就能突破"单层递归限制"；深度限 + 循环检测提供
最后一道防线。
"""
from __future__ import annotations

import queue
import threading
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ============================================================================
# Job 数据结构
# ============================================================================

@dataclass
class _Job:
    id: str
    target_uuid: str
    call_fn: Callable[[], str]
    chain: List[str]
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[str] = None
    error: Optional[BaseException] = None


# ============================================================================
# Agent 队列
# ============================================================================

class AgentQueue:
    """
    全局 Agent 任务队列。

    配置项：
        max_depth:   单条调用链最大深度（默认 8）
        workers:     worker 线程数（默认 2）
        idle_timeout: worker 空闲多少秒后退出（默认 60s）

    线程安全：所有公共方法（submit / shutdown）都可重入。
    """

    def __init__(self, max_depth: int = 8, workers: int = 2, idle_timeout: float = 60.0):
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self._max_depth = max_depth
        self._workers_target = workers
        self._idle_timeout = idle_timeout

        self._queue: "queue.Queue[_Job]" = queue.Queue()
        self._lock = threading.Lock()
        self._workers: List[threading.Thread] = []
        self._shutting_down = False

    # ---- 公共 API ----

    def submit(
        self,
        target_uuid: str,
        call_fn: Callable[[], str],
        caller_chain: Optional[List[str]] = None,
    ) -> str:
        """
        提交一个 Job，阻塞直到它完成，返回结果字符串。

        Args:
            target_uuid: 目标 Agent 的 UUID（用于循环检测）
            call_fn:     一个 0 参 callable，调用时执行目标 Agent 的对话
            caller_chain: 当前调用链（uuid 列表），用于循环与深度检测
        """
        if self._shutting_down:
            raise RuntimeError("AgentQueue 已关闭")

        chain = list(caller_chain or [])

        # 1. 循环检测
        if target_uuid in chain:
            return (
                f"[agent_queue] 循环调用被拒绝：{target_uuid} 已在调用链 {chain} 中。"
                f"请改用 attempt_completion 终止递归。"
            )

        # 2. 深度限制
        if len(chain) >= self._max_depth:
            return (
                f"[agent_queue] 达到最大调用深度 {self._max_depth}（chain={chain}）。"
                f"已拒绝入队，避免栈爆炸。"
            )

        # 3. 提交 Job
        job = _Job(
            id=str(_uuid.uuid4()),
            target_uuid=target_uuid,
            call_fn=call_fn,
            chain=chain + [target_uuid],
        )
        self._queue.put(job)
        self._ensure_workers()

        # 4. 等待结果
        job.event.wait()
        if job.error is not None:
            return f"[agent_queue] 调用失败：{job.error}"
        return job.result or ""

    def pending(self) -> int:
        """当前队列中待处理 Job 数（监控用）"""
        return self._queue.qsize()

    def worker_count(self) -> int:
        """当前存活 worker 数"""
        with self._lock:
            return sum(1 for w in self._workers if w.is_alive())

    def shutdown(self, timeout: float = 5.0) -> None:
        """关闭队列，等待 worker 退出（测试用）"""
        self._shutting_down = True
        # 塞若干 sentinel 让 worker 退出
        for _ in range(self._workers_target):
            self._queue.put(None)
        with self._lock:
            workers = list(self._workers)
        for w in workers:
            w.join(timeout=timeout)
        with self._lock:
            self._workers.clear()

    # ---- 内部 ----

    def _ensure_workers(self) -> None:
        """根据当前负载补足 worker 线程"""
        with self._lock:
            alive = [w for w in self._workers if w.is_alive()]
            self._workers = alive
            need = max(0, self._workers_target - len(alive))
            for _ in range(need):
                t = threading.Thread(
                    target=self._worker_loop,
                    name=f"AgentQueue-Worker-{len(self._workers)}",
                    daemon=True,
                )
                t.start()
                self._workers.append(t)

    def _worker_loop(self) -> None:
        """worker 主循环：取 Job → 设置 thread-local chain → 执行 → 设 event → 清 chain"""
        while True:
            try:
                job = self._queue.get(timeout=self._idle_timeout)
            except queue.Empty:
                # 长时间无任务，worker 退出
                return

            if job is None:  # sentinel
                self._queue.task_done()
                return

            # 把 Job 的调用链注入 worker 线程的 thread-local，
            # 后续 ask_for_help 可以从 _get_call_chain() 读到
            set_call_chain(list(job.chain))
            try:
                job.result = job.call_fn()
            except BaseException as e:  # noqa: BLE001
                job.error = e
            finally:
                set_call_chain([])
                job.event.set()
                self._queue.task_done()


# ============================================================================
# Thread-local 调用链
# ============================================================================

_call_chain_local = threading.local()


def set_call_chain(chain: List[str]) -> None:
    """worker 在执行 Job 前调用，把当前 Job 的调用链注入 thread-local。"""
    _call_chain_local.chain = list(chain)


def get_call_chain() -> List[str]:
    """任意线程都可读：返回当前 Job 的调用链（不在队列里就返回空）。"""
    return getattr(_call_chain_local, "chain", [])


# ============================================================================
# 模块级单例
# ============================================================================

_default_queue: Optional[AgentQueue] = None
_default_lock = threading.Lock()


def get_default_queue() -> AgentQueue:
    """获取进程级默认队列（懒加载单例）"""
    global _default_queue
    if _default_queue is None:
        with _default_lock:
            if _default_queue is None:
                _default_queue = AgentQueue()
    return _default_queue


def submit(target_uuid: str, call_fn: Callable[[], str],
           caller_chain: Optional[List[str]] = None) -> str:
    """便捷：把任务扔到默认队列并等结果"""
    return get_default_queue().submit(target_uuid, call_fn, caller_chain)
