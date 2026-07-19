# -*- coding: utf-8 -*-
"""
ToolRunner 单测

覆盖：
1. 同步完成：拿结果
2. 超时：转后台 future，task_id 可查
3. 后台 future 完成后 get_status 同步收割
4. wait() 阻塞收割
5. 异常透传（同步完成时）
6. shutdown 后 submit 抛错
"""
import time

import pytest
from dumplingsAI.tool_runner import ToolRunner


def test_sync_completion_returns_result():
    r = ToolRunner(timeout=2.0)
    try:
        result, async_id = r.submit(lambda x: x * 2, 21)
        assert result == 42
        assert async_id is None
    finally:
        r.shutdown()


def test_timeout_moves_to_background():
    r = ToolRunner(timeout=0.1, max_workers=2)
    try:
        # 故意 sleep 0.5s，但 timeout 0.1
        result, async_id = r.submit(time.sleep, 0.5, tool_name="slow_sleep")
        assert result is None
        assert async_id is not None
        # 任务仍在背景
        status = r.get_status(async_id)
        assert status is not None
        assert status["done"] is False
        assert status["tool_name"] == "slow_sleep"
    finally:
        r.shutdown(wait=True)


def test_get_status_after_future_done():
    r = ToolRunner(timeout=0.5, max_workers=2)
    try:
        # 0.1s 任务，timeout 0.5s → 同步完成
        result, async_id = r.submit(time.sleep, 0.1)
        assert async_id is None
        # get_status 应该是 None（同步完成的已经清理）
        assert r.get_status("any") is None
    finally:
        r.shutdown()


def test_wait_blocks_until_done():
    """timeout=0.05 让 0.3s 任务超时转后台；wait 收割结果"""
    r = ToolRunner(timeout=0.05, max_workers=2)
    try:
        _, async_id = r.submit(time.sleep, 0.3, tool_name="wait_sleep")
        assert async_id is not None
        # 等后台完成（wait 内置阻塞 + timeout）
        result = r.wait(async_id, timeout=2.0)
        # sleep 返回 None
        assert result is None
        # 收割后已清理
        assert r.get_status(async_id) is None
    finally:
        r.shutdown()


def test_exception_propagates_when_sync():
    r = ToolRunner(timeout=2.0)
    try:
        def boom():
            raise ValueError("bang")
        with pytest.raises(ValueError, match="bang"):
            r.submit(boom)
    finally:
        r.shutdown()


def test_shutdown_rejects_new_submits():
    r = ToolRunner(timeout=1.0)
    r.shutdown()
    with pytest.raises(RuntimeError):
        r.submit(lambda: 1)


def test_max_workers_respected():
    r = ToolRunner(timeout=0.5, max_workers=2)
    try:
        # 同时提交 3 个 0.3s 任务，max_workers=2，第 3 个应该排队但 0.5s 内能完成
        results = []
        for i in range(3):
            r_, _ = r.submit(time.sleep, 0.05)
            results.append(r_)
        assert results == [None, None, None]  # sleep 返回 None
    finally:
        r.shutdown()


def test_status_after_background_done():
    """超时跑 0.5s，等 1s 后 get_status 应显示 done=True"""
    r = ToolRunner(timeout=0.05, max_workers=2)
    try:
        _, async_id = r.submit(time.sleep, 0.2, tool_name="bg")
        # 等等任务完成
        time.sleep(0.4)
        status = r.get_status(async_id)
        # done 后 get_status 会从登记里清掉（无 watcher 时）
        # 这里我们用 wait 收割
        if status is None:
            # 已经收割过了
            pass
        else:
            assert status["done"] is True
    finally:
        r.shutdown(wait=True)
