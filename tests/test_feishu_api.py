# -*- coding: utf-8 -*-
"""feishu_api 测试：令牌桶单测 + 集成测试（真连飞书，临时文档测完删）"""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from feishu_api import FeishuClient, FeishuError, RateLimiter, get_credentials

# 1x1 透明 PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# ---------------------------------------------------------------- 令牌桶单测
class TestRateLimiter:
    def test_throttles_requests(self):
        """qps=3 时 10 次 acquire 应 >= 3 秒（无限制下几乎为 0）"""
        rl = RateLimiter(qps=3)
        start = time.monotonic()
        for _ in range(10):
            rl.acquire()
        elapsed = time.monotonic() - start
        # 9 个间隔 * (1/3)s = 3s，留计时余量
        assert elapsed >= 2.8, f"限流未生效，10 次仅耗时 {elapsed:.2f}s"

    def test_first_call_immediate(self):
        """第一次 acquire 应立即返回（无历史间隔）"""
        rl = RateLimiter(qps=3)
        start = time.monotonic()
        rl.acquire()
        assert time.monotonic() - start < 0.1

    def test_no_negative_wait(self):
        """间隔过后的 acquire 不应 sleep（wait 为负时直接通过）"""
        rl = RateLimiter(qps=3)
        rl.acquire()
        time.sleep(0.4)  # 超过 1/3s 间隔
        start = time.monotonic()
        rl.acquire()
        assert time.monotonic() - start < 0.1


# ---------------------------------------------------------------- 凭据
def _make_client():
    try:
        app_id, app_secret = get_credentials()
        return FeishuClient(app_id, app_secret)
    except FeishuError:
        pytest.skip("无飞书凭据，跳过集成测试")


# ---------------------------------------------------------------- 集成测试
@pytest.mark.integration
class TestFeishuIntegration:
    def test_create_add_read_delete(self):
        """建文档 → 加块 → 读块 → 删文档"""
        client = _make_client()
        doc_id = client.create_doc("md2feishu2md 集成测试")
        try:
            created = client.add_children(doc_id, [
                {"block_type": 3, "heading1": {"elements": [{"text_run": {"content": "标题"}}]}},
                {"block_type": 2, "text": {"elements": [{"text_run": {"content": "正文"}}]}},
            ])
            assert len(created) == 2
            blocks = client.read_blocks(doc_id)
            assert len(blocks) >= 2
            texts = [
                el["text_run"]["content"]
                for b in blocks
                for el in (b.get("heading1") or b.get("text") or {}).get("elements", [])
            ]
            assert "标题" in texts
            assert "正文" in texts
        finally:
            client.delete_doc(doc_id)

    def test_upload_image_token(self):
        """上传图片 → 读块验证 image.token 非空"""
        client = _make_client()
        doc_id = client.create_doc("md2feishu2md 图片测试")
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(PNG_1X1)
                img_path = f.name
            try:
                created = client.add_children(doc_id, [
                    {"block_type": 27, "image": {}},
                ])
                bid = created[0]["block_id"]
                ok = client.upload_images(doc_id, [(img_path, bid)], os.path.dirname(img_path))
                assert ok == 1
                blocks = client.read_blocks(doc_id)
                img_block = next(b for b in blocks if b.get("block_type") == 27)
                token = (img_block.get("image") or {}).get("token")
                assert token, "图片 token 应为非空"
            finally:
                os.unlink(img_path)
        finally:
            client.delete_doc(doc_id)

    def test_list_folder_tree(self):
        """列云空间根目录文件夹树（只读，不删）"""
        client = _make_client()
        tree = client.list_folder_tree()
        assert isinstance(tree, list)

    def test_list_wiki_spaces(self):
        """列知识库列表（可能无权限，仅验证不崩或抛可识别错误）"""
        client = _make_client()
        try:
            spaces = client.list_wiki_spaces()
            assert isinstance(spaces, list)
        except FeishuError:
            pass  # 无知识库权限属预期，测试不失败

    def test_create_doc_in_dest_root(self):
        """不指定目标路径时创建在根目录"""
        client = _make_client()
        doc_id = client.create_doc_in_dest("md2feishu2md 根目录测试", None)
        try:
            assert doc_id
        finally:
            client.delete_doc(doc_id)
