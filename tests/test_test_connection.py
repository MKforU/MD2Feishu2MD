"""回归测试：test_connection 优先测输入框传入凭据（不落盘），UX 修正（2026-08-25）

用户反馈"应先测试连接有效，再决定保存"。此测试锁定：
1. 传入凭据时用临时 client 验证，不写 config、不替换 self.client
2. 未传凭据时用已保存配置（原行为）
"""
import app as app_mod


class _FakeStore:
    def __init__(self):
        self.saved = ("", "")

    def get_credentials(self):
        return self.saved


def test_test_connection_uses_passed_credentials_without_saving(monkeypatch):
    api = app_mod.Api.__new__(app_mod.Api)  # 跳过 __init__（避免碰真实 config）
    api.config = _FakeStore()
    api.client = None
    api.queue = None

    called = {}

    class FakeClient:
        def __init__(self, app_id, app_secret, limiter=None):
            called["creds"] = (app_id, app_secret)

        def _headers(self):
            called["headers"] = True

    monkeypatch.setattr(app_mod, "FeishuClient", FakeClient)

    r = api.test_connection("cli_test_id", "test_secret")
    assert r == {"ok": True}
    assert called["creds"] == ("cli_test_id", "test_secret"), "必须用传入凭据构造临时 client"
    assert called.get("headers") is True
    assert api.client is None, "传入凭据测试不得污染 self.client"
    assert api.config.saved == ("", ""), "测试连接不得写 config"


def test_test_connection_falls_back_to_saved(monkeypatch):
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeStore()
    api.config.saved = ("cli_saved", "saved_secret")
    api.client = None
    api.queue = None

    created = {}

    class FakeClient:
        def __init__(self, app_id, app_secret, limiter=None):
            created["creds"] = (app_id, app_secret)

        def _headers(self):
            pass

    monkeypatch.setattr(app_mod, "FeishuClient", FakeClient)

    r = api.test_connection()
    assert r == {"ok": True}
    # 走 _get_client()：用已保存凭据构造并缓存到 self.client
    assert created["creds"] == ("cli_saved", "saved_secret")


def test_test_connection_error_wrapped(monkeypatch):
    from feishu_api import FeishuError

    class BoomClient:
        def __init__(self, *a, **k):
            pass

        def _headers(self):
            raise FeishuError("BAD_CREDENTIALS", "app_id 无效")

    monkeypatch.setattr(app_mod, "FeishuClient", BoomClient)
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeStore()
    api.client = None
    api.queue = None
    r = api.test_connection("x", "y")
    assert r["ok"] is False
    assert "BAD_CREDENTIALS" in r["error"]
