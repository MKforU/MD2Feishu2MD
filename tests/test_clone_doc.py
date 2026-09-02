"""回归测试：复刻只读文档（clone_doc）

用户只能阅读的文档：应用凭自身权限读出内容，经 md 中转生成一份
归用户所有的新文档（可编辑）。

链路：read_blocks → render_document → (图片下载到临时目录) →
写临时 md → convert_md（复用上传/表格/公式/owner 转移逻辑）→ 清理临时文件。
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
    def __init__(self, blocks):
        self.blocks = blocks
        self.read_calls = 0

    def read_blocks(self, doc_id):
        self.read_calls += 1
        return self.blocks

    def download_images(self, image_refs, out_dir):
        return {n: n for n in image_refs}, []

    def create_doc_in_dest(self, title, dest):
        return "NEWDOC1"

    def add_children(self, doc_id, children):
        return [{"block_id": f"B{i}"} for i in range(len(children))]

    def upload_images(self, doc_id, img_slots, md_dir):
        return {"ok": 0, "total": 0, "failures": []}

    def transfer_owner(self, doc_id, open_id):
        pass


def _blocks():
    return [
        {"block_id": "P", "block_type": 1, "parent_id": "", "children": ["T1", "H1"]},
        {"block_id": "T1", "block_type": 2, "parent_id": "P",
         "text": {"elements": [{"text_run": {"content": "正文段落"}}]}},
        {"block_id": "H1", "block_type": 3, "parent_id": "P",
         "heading1": {"elements": [{"text_run": {"content": "标题一"}}]}},
    ]


def test_clone_doc_creates_new_doc(monkeypatch):
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg(owner="ou_user1")
    api.queue = None
    client = _FakeClient(_blocks())
    api.client = client

    monkeypatch.setattr(app_mod, "_extract_title", lambda blocks: "源文档")

    r = api.clone_doc("https://feishu.cn/docx/SRCDOC123", None)
    assert r["ok"] is True, r
    assert r["url"].endswith("NEWDOC1")
    # 新文档由 convert_md 创建（fake client 的 create_doc_in_dest 被调用）
    assert client.read_calls == 1
    # 历史记录登记为副本
    assert any("（副本）" in h["title"] for h in api.config.history)


def test_clone_doc_bad_url(monkeypatch):
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg()
    api.queue = None
    api.client = _FakeClient(_blocks())
    r = api.clone_doc("https://example.com/not-feishu", None)
    assert r["ok"] is False
    assert "无法从链接解析文档 ID" in r["error"]


def test_clone_doc_records_image_download_failures(monkeypatch):
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg()
    api.queue = None
    blocks = _blocks()
    blocks[0]["children"].append("IMG1")
    blocks.append({
        "block_id": "IMG1", "block_type": 27, "parent_id": "P",
        "image": {"token": "ftok1"},
    })
    client = _FakeClient(blocks)
    client.download_images = lambda refs, out_dir: ({}, [{"name": "img1.png"}])
    api.client = client
    monkeypatch.setattr(app_mod, "_extract_title", lambda blocks: "带图文档")

    r = api.clone_doc("https://feishu.cn/docx/SRCDOC123", None)
    assert r["ok"] is True, r
    assert "图片下载失败" in r.get("warning", ""), r
