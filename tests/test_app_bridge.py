"""回归测试：pywebview js_bridge 递归卡死 bug（2026-08-25）

根因：Api 实例持有 window 属性 → pywebview get_functions 递归遍历
window.native（.NET 控件）→ 无限递归 → 启动卡死。
此测试防止任何人把 window（或其他危险属性）挂回 Api 实例。
"""
import inspect

import app


def _walk_like_pywebview(obj, seen, depth=0):
    """复制 pywebview util.py get_functions 的递归遍历逻辑（带保险深度限制）"""
    assert depth < 200, f"递归深度异常（{depth} 层），疑似 js_bridge 无限递归复发"
    obj_id = id(obj)
    if obj_id in seen:
        return
    seen.add(obj_id)
    for name in dir(obj):
        if name.startswith("_"):
            continue
        attr = getattr(obj, name)
        if not getattr(attr, "_serializable", True):
            continue
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            continue
        if inspect.isclass(attr) or (
            isinstance(attr, object) and not callable(attr) and hasattr(attr, "__module__")
        ):
            _walk_like_pywebview(attr, seen, depth + 1)


def test_api_has_no_window_attribute():
    """Api 实例绝不能持有 window/native 属性（pywebview 会递归遍历导致卡死）"""
    api = app.Api()
    assert not hasattr(api, "window"), "Api.window 会导致 pywebview js_bridge 无限递归卡死（见 _window_ref 注释）"
    assert not hasattr(api, "native"), "Api.native 会导致 pywebview js_bridge 无限递归卡死"


def test_internal_classes_not_serializable():
    """ConfigStore/FeishuClient/TaskQueue 必须 _serializable=False，防止内部方法（含明文凭据）暴露给 JS"""
    from config_store import ConfigStore
    from feishu_api import FeishuClient
    from task_queue import TaskQueue

    assert ConfigStore._serializable is False
    assert FeishuClient._serializable is False
    assert TaskQueue._serializable is False


def test_api_walk_terminates():
    """模拟 pywebview get_functions 遍历真实 Api 实例（含 client/queue 就绪态），必须正常终止"""
    api = app.Api()
    api.client = object.__new__(__import__("feishu_api").FeishuClient)  # 不真正发请求，只造实例形状
    api.queue = object.__new__(__import__("task_queue").TaskQueue)
    _walk_like_pywebview(api, set())
