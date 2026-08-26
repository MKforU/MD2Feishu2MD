"""回归测试：图片上传/下载失败时的用户提示（2026-08-25）

改动：upload_images/download_images 返回详细结果（含 failures），
convert_md/convert_back 把失败汇总进返回值 warning 字段，前端显示警告条。
此测试锁定数据流。
"""
import app as app_mod
from feishu_api import FeishuClient
import os
import tempfile


class _FakeCfg:
    def get_default_out_dir(self):
        return None

    def add_history(self, h):
        pass


class _FakeUploadClient:
    """模拟 upload_images：1 成功 2 失败"""
    def create_doc_in_dest(self, title, dest):
        return "DOC123"

    def add_children(self, doc_id, children):
        return [{"block_id": f"B{i}"} for i in range(len(children))]

    def upload_images(self, doc_id, img_slots, md_dir):
        return {
            "ok": 1, "total": 3,
            "failures": [
                {"src": "images/missing1.png", "reason": "文件不存在"},
                {"src": "images/missing2.png", "reason": "文件不存在"},
            ],
        }


def _md_with_images(tmp):
    # 3 张图：1 张存在，2 张不存在
    os.makedirs(os.path.join(tmp, "images"), exist_ok=True)
    with open(os.path.join(tmp, "images", "ok.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")  # PNG 头
    path = os.path.join(tmp, "t.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 标题\n\n![a](images/ok.png)\n\n![b](images/missing1.png)\n\n![c](images/missing2.png)\n")
    return path


def test_convert_md_returns_warning_on_image_failures(monkeypatch):
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg()
    api.client = _FakeUploadClient()
    api.queue = None

    with tempfile.TemporaryDirectory() as tmp:
        r = api.convert_md(_md_with_images(tmp), None, None)
        assert r["ok"] is True, r
        assert "warning" in r
        assert "1/3" in r["warning"], r["warning"]
        assert "missing1.png" in r["warning"]
        assert "missing2.png" in r["warning"]


def test_convert_md_no_warning_when_all_ok(monkeypatch):
    class _AllOkClient(_FakeUploadClient):
        def upload_images(self, doc_id, img_slots, md_dir):
            return {"ok": len(img_slots), "total": len(img_slots), "failures": []}

    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg()
    api.client = _AllOkClient()
    api.queue = None

    with tempfile.TemporaryDirectory() as tmp:
        # 全部成功场景：图都存在
        os.makedirs(os.path.join(tmp, "images"), exist_ok=True)
        with open(os.path.join(tmp, "images", "a.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        path = os.path.join(tmp, "t.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# t\n\n![a](images/a.png)\n")
        r = api.convert_md(path, None, None)
        assert r["ok"] is True
        assert "warning" not in r


def test_convert_back_returns_warning_on_download_failures(monkeypatch):
    class _FailDownClient:
        def get_wiki_node(self, tok):
            return {"obj_token": "OBJ1", "obj_type": "docx"}

        def read_blocks(self, doc_id):
            return [{"block_type": 1, "children": []}]

        def download_images(self, image_refs, out_dir):
            return {}, [{"name": "img0.png", "reason": "FeishuError: 403"}]

    api = app_mod.Api.__new__(app_mod.Api)
    api.config = _FakeCfg()
    api.client = _FailDownClient()
    api.queue = None
    monkeypatch.setattr(app_mod, "render_document", lambda b: ("# t\n", [{"file_token": "F1", "name": "img0.png"}]))
    monkeypatch.setattr(app_mod, "_extract_title", lambda b: "t")

    with tempfile.TemporaryDirectory() as tmp:
        r = api.convert_back("https://my.feishu.cn/wiki/AAA", tmp)
        assert r["ok"] is True, r
        assert "img0.png" in r["warning"]
