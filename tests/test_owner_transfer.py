"""回归测试：转换后自动转移文档所有权给用户 open_id（2026-08-26）

需求：应用创建的文档 owner 是应用，用户端无法删除/移动。设置页配置
owner_open_id 后，convert_md 创建文档成功即调用 transfer_owner 转移。
失败不阻断转换（warning 提示）；未配置不调用。
"""
import os
import tempfile

import app as app_mod


class _FakeCfg:
    def __init__(self, owner=""):
        self.owner = owner
        self.history = []

    def get_default_out_dir(self):
        return None

    def get_owner_open_id(self):
        return self.owner

    def add_history(self, h):
        self.history.append(h)


class _FakeClient:
    def __init__(self):
        self.transfers = []
        self.fail_transfer = False

    def create_doc_in_dest(self, title, dest):
        return "DOC123"

    def add_children(self, doc_id, children):
        return [{"block_id": f"B{i}"} for i in range(len(children))]

    def upload_images(self, doc_id, img_slots, md_dir):
        return {"ok": 0, "total": 0, "failures": []}

    def transfer_owner(self, doc_id, open_id):
        if self.fail_transfer:
            raise RuntimeError("权限不足")
        self.transfers.append((doc_id, open_id))


def _mk_md(tmp):
    p = os.path.join(tmp, "t.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# 标题\n\n正文\n")
    return p


def test_transfer_owner_called_when_configured():
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg(owner="ou_test123")
    api.queue = None
    client = _FakeClient()
    api.client = client
    with tempfile.TemporaryDirectory() as tmp:
        r = api.convert_md(_mk_md(tmp), None, None)
    assert r["ok"] is True
    assert client.transfers == [("DOC123", "ou_test123")], "必须调用 transfer_owner"
    assert "warning" not in r, "成功转移不应有警告"


def test_transfer_owner_skipped_when_not_configured():
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg(owner="")
    api.queue = None
    client = _FakeClient()
    api.client = client
    with tempfile.TemporaryDirectory() as tmp:
        r = api.convert_md(_mk_md(tmp), None, None)
    assert r["ok"] is True
    assert client.transfers == [], "未配置 open_id 不应调用 transfer_owner"


def test_transfer_owner_failure_warns_but_keeps_ok():
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg(owner="ou_test123")
    api.queue = None
    client = _FakeClient()
    client.fail_transfer = True
    api.client = client
    with tempfile.TemporaryDirectory() as tmp:
        r = api.convert_md(_mk_md(tmp), None, None)
    assert r["ok"] is True, "转移失败不阻断转换"
    assert "所有权转移失败" in r.get("warning", ""), r.get("warning")


def test_set_config_persists_owner_open_id():
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg()
    api.client = None
    api.queue = None
    # set_config 用真实 ConfigStore 语义：改用最小桩验证参数透传
    api.config.set_owner_open_id = lambda v: setattr(api.config, "owner", v)
    r = api.set_config(owner_open_id="ou_new")
    assert r["ok"] is True
    assert api.config.owner == "ou_new"
