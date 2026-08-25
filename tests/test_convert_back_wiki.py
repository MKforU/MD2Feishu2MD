"""回归测试：反向转换支持 /wiki/ 链接（2026-08-25）

用户实际知识库链接形如 https://my.feishu.cn/wiki/{node_token}?from=...，
需先 get_node 换 obj_token 再读块。此测试锁定解析与错误分支。
"""
import app as app_mod
from app import _extract_doc_ref


def test_extract_doc_ref_docx():
    assert _extract_doc_ref("https://feishu.cn/docx/ABC123def") == ("docx", "ABC123def")


def test_extract_doc_ref_wiki_with_query():
    url = "https://my.feishu.cn/wiki/F89JwCGKxipVVUk845bchDGDnbd?from=from_copylink"
    assert _extract_doc_ref(url) == ("wiki", "F89JwCGKxipVVUk845bchDGDnbd")


def test_extract_doc_ref_invalid():
    assert _extract_doc_ref("https://feishu.cn/sheet/ABC123") is None
    assert _extract_doc_ref("") is None
    assert _extract_doc_ref(None) is None


def test_convert_back_wiki_resolves_obj_token(monkeypatch):
    """wiki 链接：先 get_wiki_node 换 obj_token，再 read_blocks"""
    api = app_mod.Api.__new__(app_mod.Api)
    api.config = None
    api.client = object()
    api.queue = None

    calls = []

    class FakeClient:
        def get_wiki_node(self, node_token):
            calls.append(("get_wiki_node", node_token))
            return {"obj_token": "DOCTXTOBJ123", "obj_type": "docx"}

        def read_blocks(self, doc_id):
            calls.append(("read_blocks", doc_id))
            return [{"block_type": 1, "children": []}]  # 最小根块

    class FakeRender:
        @staticmethod
        def render_document(blocks):
            return "# t\n", []

    api.client = FakeClient()
    monkeypatch.setattr(app_mod, "render_document", lambda b: ("# t\n", []))
    monkeypatch.setattr(app_mod, "_extract_title", lambda b: "t")
    api.config = type("C", (), {
        "get_default_out_dir": lambda s: None,
        "add_history": lambda s, h: None,
    })()

    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        r = api.convert_back("https://my.feishu.cn/wiki/F89JwCGKxipVVUk845bchDGDnbd", td)
        assert r["ok"] is True, r
        assert calls[0] == ("get_wiki_node", "F89JwCGKxipVVUk845bchDGDnbd")
        assert calls[1] == ("read_blocks", "DOCTXTOBJ123"), "wiki 必须用 obj_token 读块"
        assert os.path.exists(r["path"])


def test_convert_back_wiki_rejects_non_docx(monkeypatch):
    """wiki 节点不是 docx（如 sheet）时给出明确错误"""
    api = app_mod.Api.__new__(app_mod.Api)
    api.queue = None

    class FakeClient:
        def get_wiki_node(self, node_token):
            return {"obj_token": "SHEET123", "obj_type": "sheet"}

    api.client = FakeClient()
    r = api.convert_back("https://my.feishu.cn/wiki/AAA", "/tmp")
    assert r["ok"] is False
    assert "sheet" in r["error"]


def test_convert_back_wiki_no_obj_token(monkeypatch):
    class FakeClient:
        def get_wiki_node(self, node_token):
            return {}

    api = app_mod.Api.__new__(app_mod.Api)
    api.queue = None
    api.client = FakeClient()
    r = api.convert_back("https://my.feishu.cn/wiki/AAA", "/tmp")
    assert r["ok"] is False
    assert "obj_token" in r["error"]
