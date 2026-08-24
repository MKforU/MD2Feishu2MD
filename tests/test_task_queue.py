# -*- coding: utf-8 -*-
"""task_queue 单测：并发数、状态流转、失败重试（mock 任务函数，不连飞书）"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from task_queue import TaskQueue, PENDING, RUNNING, SUCCESS, FAILED


class TestConcurrency:
    def test_max_workers_validation(self):
        """max_workers < 1 应抛 ValueError"""
        with pytest.raises(ValueError):
            TaskQueue(max_workers=0)

    def test_concurrency_limit(self):
        """max_workers=3 时同时运行任务数峰值不超过 3，且确实有并发"""
        active = 0
        peak = 0
        lock = threading.Lock()

        def work():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with lock:
                active -= 1

        q = TaskQueue(max_workers=3)
        for _ in range(8):
            q.submit(work)
        q.wait()
        q.shutdown()
        assert peak <= 3, f"并发峰值 {peak} 超过 max_workers=3"
        assert peak >= 2, f"并发峰值 {peak} 过小，疑似串行"

    def test_serial_when_one_worker(self):
        """max_workers=1 时完全串行"""
        active = 0
        peak = 0
        lock = threading.Lock()

        def work():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1

        q = TaskQueue(max_workers=1)
        for _ in range(4):
            q.submit(work)
        q.wait()
        q.shutdown()
        assert peak == 1


class TestStatusTransitions:
    def test_status_flow(self):
        """pending → running → success 状态流转"""
        observed = []
        q = TaskQueue(max_workers=2, on_change=lambda t: observed.append(t["status"]))

        def ok():
            return "done"

        tid = q.submit(ok)
        q.wait()
        assert q.status(tid) == SUCCESS
        assert q.get(tid).result == "done"
        assert RUNNING in observed
        assert SUCCESS in observed

    def test_failure_flow(self):
        """失败任务记录错误信息"""
        q = TaskQueue(max_workers=2)

        def boom():
            raise ValueError("boom error")

        tid = q.submit(boom)
        q.wait()
        assert q.status(tid) == FAILED
        assert "boom error" in q.get(tid).error


class TestRetry:
    def test_retry_success(self):
        """失败 → retry → 成功"""
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return "ok"

        q = TaskQueue(max_workers=2)
        tid = q.submit(flaky)
        q.wait()
        assert q.status(tid) == FAILED
        assert "transient" in q.get(tid).error

        assert q.retry(tid) is True
        q.wait()
        assert q.status(tid) == SUCCESS
        assert q.get(tid).result == "ok"
        assert q.get(tid).retries == 1

    def test_retry_returns_false(self):
        """非失败态或不存在任务 retry 返回 False"""
        q = TaskQueue(max_workers=2)
        tid = q.submit(lambda: "x")
        q.wait()
        assert q.status(tid) == SUCCESS
        assert q.retry(tid) is False       # 成功态不可重试
        assert q.retry(99999) is False     # 不存在
        q.shutdown()


class TestQuery:
    def test_list_order_and_ids(self):
        q = TaskQueue(max_workers=3)
        ids = [q.submit(lambda: i) for i in range(5)]
        q.wait()
        tasks = q.list()
        assert [t.id for t in tasks] == ids
        assert all(t.status == SUCCESS for t in tasks)

    def test_to_dict(self):
        q = TaskQueue(max_workers=2)
        q.submit(lambda: 1)
        q.wait()
        d = q.to_dict()
        assert isinstance(d, list)
        assert d[0]["status"] == SUCCESS
        assert d[0]["result"] == 1
        q.shutdown()


class TestShutdown:
    def test_submit_after_shutdown_raises(self):
        q = TaskQueue(max_workers=2)
        q.shutdown()
        with pytest.raises(RuntimeError):
            q.submit(lambda: 1)

    def test_retry_after_shutdown_false(self):
        q = TaskQueue(max_workers=2)
        q.shutdown()
        assert q.retry(1) is False


class TestWait:
    def test_wait_timeout(self):
        q = TaskQueue(max_workers=2)

        def slow():
            time.sleep(0.4)

        tid = q.submit(slow)
        q.wait(timeout=0.05)
        assert q.status(tid) == RUNNING  # 尚未完成
        q.wait()  # 无限等待直到完成
        assert q.status(tid) == SUCCESS
        q.shutdown()
