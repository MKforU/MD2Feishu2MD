# -*- coding: utf-8 -*-
"""文档级并发任务队列。

- ``TaskQueue(max_workers=3)``：线程池并发调度文档转换任务
- 任务状态机：pending → running → success / failed
- 失败保留错误信息，支持 retry
- 状态变更通过 ``on_change`` 回调通知（供 UI 更新进度）

文档任务内部串行（建文档→加块→图片）与图片小并发由任务函数自身实现，
飞书请求统一走 feishu_api 的全局限流器（QPS≤上限），此处只负责调度。
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class Task:
    __slots__ = ("id", "fn", "args", "kwargs", "status", "result", "error", "retries", "done")

    def __init__(self, task_id, fn, args, kwargs):
        self.id = task_id
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.status = PENDING
        self.result = None
        self.error = None
        self.retries = 0
        self.done = threading.Event()

    def to_dict(self):
        return {
            "id": self.id, "status": self.status, "error": self.error,
            "retries": self.retries, "result": self.result,
        }


class TaskQueue:
    def __init__(self, max_workers=3, on_change=None):
        if max_workers < 1:
            raise ValueError("max_workers 必须 >= 1")
        self.max_workers = max_workers
        self._tasks = {}
        self._futures = {}
        self._next_id = 0
        self._shutdown = False
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._on_change = on_change

    # ------------------------------------------------------------ 提交
    def submit(self, fn, *args, **kwargs):
        """提交一个任务并立即调度执行。返回 task_id"""
        with self._lock:
            if self._shutdown:
                raise RuntimeError("TaskQueue 已关闭，无法提交任务")
            self._next_id += 1
            tid = self._next_id
            task = Task(tid, fn, args, kwargs)
            self._tasks[tid] = task
        self._notify(task)
        self._launch(task)
        return tid

    def _launch(self, task):
        future = self._executor.submit(self._run, task)
        with self._lock:
            self._futures[task.id] = future

    def _run(self, task):
        self._set_status(task, RUNNING)
        try:
            task.result = task.fn(*task.args, **task.kwargs)
            self._set_status(task, SUCCESS)
        except Exception as e:  # noqa: BLE001 - 任务级兜底，错误记录在 task.error
            task.error = str(e) or type(e).__name__  # 存消息，空消息兜底类型名
            self._set_status(task, FAILED)
        finally:
            task.done.set()

    # ------------------------------------------------------------ 重试
    def retry(self, task_id):
        """重试失败任务。成功返回 True；任务不存在/非失败态/已关闭返回 False"""
        with self._lock:
            if self._shutdown:
                return False
            task = self._tasks.get(task_id)
        if task is None or task.status != FAILED:
            return False
        task.retries += 1
        task.error = None
        task.result = None
        task.done.clear()
        self._set_status(task, PENDING)
        self._launch(task)
        return True

    # ------------------------------------------------------------ 查询
    def get(self, task_id):
        with self._lock:
            return self._tasks.get(task_id)

    def status(self, task_id):
        t = self.get(task_id)
        return t.status if t else None

    def list(self):
        with self._lock:
            return list(self._tasks.values())

    def to_dict(self):
        return [t.to_dict() for t in self.list()]

    # ------------------------------------------------------------ 等待/关闭
    def wait(self, timeout=None):
        """等待所有任务完成。timeout 为总超时秒数（None=无限），超时后返回（任务可能未完成）"""
        deadline = None if timeout is None else time.monotonic() + timeout
        for t in self.list():
            if deadline is None:
                t.done.wait()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                t.done.wait(remaining)

    def shutdown(self):
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------ 内部
    def _set_status(self, task, status):
        task.status = status
        self._notify(task)

    def _notify(self, task):
        if self._on_change:
            try:
                self._on_change(task.to_dict())
            except Exception:
                pass  # 回调异常不影响任务执行
