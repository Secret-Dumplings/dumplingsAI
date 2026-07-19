# -*- coding: utf-8 -*-
"""
ToolRunner — 工具执行的超时 + 异步后端
======================================

为什么需要
----------
原 ``_dispatch_tool`` 直接 ``tool_func(**args)`` 同步执行，AI 跑长线任务
（爬一个慢接口、跑一个 shell 命令、批量处理文件…）会把整个
``conversation_with_tool`` 阻塞住。再加上 ``max_tool_turns=16`` 这种
"防止递归过深"的熔断，长任务根本跑不完。

ToolRunner 把工具执行放到独立的 ``ThreadPoolExecutor`` 里跑：
- 设置单次 ``timeout``，超时即视为"该任务转入异步后端"
- 超时后 **不** 取消 future，而是把它登记到 ``_background_tasks`` 里
  继续跑，AI 看到的是一个 ``task_id`` 占位符，可以继续做别的事
- 后续可以通过 ``check_background_task(task_id)`` 或等下次 ask_for_help
  来收割结果

用法
----
::

    runner = ToolRunner(timeout=60.0, max_workers=8)

    # 同步路径：直接拿结果
    result, async_id = runner.submit(agent.ask_for_help, "x", "y")

    # 异步路径：超时后拿到 task_id
    if async_id:
        # 任务在后台跑；稍后用 get_status 检查
        status = runner.get_status(async_id)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid as _uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class _Task:
    id: str
    future: Future
    tool_name: str
    arguments: Dict[str, Any]
    submitted_at: float
    result: Any = None
    error: Optional[BaseException] = None
    done: bool = False
    done_at: Optional[float] = None


class ToolRunner:
    """
    工具执行调度器：超时即转异步后端。

    线程安全。
    """

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        max_workers: int = 8,
        on_task_done: Optional[Callable[[_Task], None]] = None,
    ):
        self.default_timeout = float(timeout)
        self.max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="ToolRunner",
        )
        self._lock = threading.Lock()
        self._tasks: Dict[str, _Task] = {}

        def _watcher(task: _Task) -> None:
            try:
                result = task.future.result()
                task.result = result
            except BaseException as e:  # noqa: BLE001
                task.error = e
            finally:
                task.done = True
                task.done_at = time.time()
                if on_task_done:
                    try:
                        on_task_done(task)
                    except Exception as cb_err:
                        logger.error(f"ToolRunner on_task_done callback failed: {cb_err}")

        self._on_task_done = _watcher

    def __del__(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=False)
        except Exception:
            pass

    # ---- 公共 API ----

    def submit(
        self,
        tool_func: Callable[..., Any],
        /,
        *args: Any,
        tool_name: str = "",
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Tuple[Any, Optional[str]]:
        """
        提交一个工具调用。

        Returns:
            (result, None)   — 在 timeout 内完成
            (None, task_id)  — 超时，转为后台 future

        Raises:
            直接抛 tool_func 抛出的异常（如果同步完成）
        """
        eff_timeout = self.default_timeout if timeout is None else float(timeout)
        future = self._executor.submit(tool_func, *args, **kwargs)
        task_id = str(_uuid.uuid4())
        task = _Task(
            id=task_id,
            future=future,
            tool_name=tool_name,
            arguments=kwargs,
            submitted_at=time.time(),
        )
        with self._lock:
            self._tasks[task_id] = task

        try:
            result = future.result(timeout=eff_timeout)
        except FutTimeout:
            # 超时 → 转为后台 future
            logger.warning(
                f"ToolRunner: {tool_name or tool_func.__name__} "
                f"timed out after {eff_timeout}s, moved to background as {task_id}"
            )
            return None, task_id
        else:
            # 同步完成：从登记里清掉
            with self._lock:
                self._tasks.pop(task_id, None)
            return result, None

    def get_status(self, task_id: str) -> Optional[dict]:
        """查询后台任务当前状态

        Returns:
            None — 任务不存在
            dict — 状态：``{"done": bool, "result": Any, "error": str|None, "elapsed": float}``
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None
        if not task.done and task.future.done():
            # future 完成了但 watcher 还没跑——拉一下结果
            try:
                task.result = task.future.result()
            except BaseException as e:  # noqa: BLE001
                task.error = e
            finally:
                task.done = True
                task.done_at = time.time()
                with self._lock:
                    self._tasks.pop(task_id, None)
        return {
            "id": task_id,
            "tool_name": task.tool_name,
            "done": task.done,
            "result": str(task.result) if task.result is not None else None,
            "error": str(task.error) if task.error is not None else None,
            "elapsed": (task.done_at or time.time()) - task.submitted_at,
        }

    def wait(self, task_id: str, *, timeout: Optional[float] = None) -> Any:
        """同步等待一个后台任务完成（最多 timeout 秒），返回结果或抛错"""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"task_id not found: {task_id}")
        try:
            result = task.future.result(timeout=timeout)
        except FutTimeout:
            raise TimeoutError(f"task {task_id} still running after {timeout}s")
        else:
            with self._lock:
                self._tasks.pop(task_id, None)
            return result

    def shutdown(self, wait: bool = True) -> None:
        """关闭执行器（通常 Agent 退出时调用）"""
        self._executor.shutdown(wait=wait)
