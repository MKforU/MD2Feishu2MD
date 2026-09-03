"""回归测试：反向转换（飞书→md）公式与高亮块支持

用户实测《pi0》文档反向转换发现三类问题：
1. 行内公式（equation 元素）被 element_to_md 丢弃 → 正文断句空洞（"给定，，其中为"）
2. callout/quote_container 高亮块（type 34）落 [未知块]
3. 独立公式块（type 16）无分支
"""
from feishu2md import element_to_md, render_block, render_document


def test_inline_equation_restored():
    """行内公式元素 → $...$ LaTeX"""
    el = {"equation": {"content": "\\pi_{0}"}}
    assert element_to_md(el) == "$\\pi_{0}$"


def test_text_run_still_works():
    """text_run 不受影响"""
    el = {"text_run": {"content": "hello", "text_element_style": {"bold": True}}}
    assert element_to_md(el) == "**hello**"


def test_quote_container_not_unknown():
    """quote_container 高亮块（type 34）→ 透明容器（children 正常递归），不出 [未知块]"""
    block = {"block_id": "Q1", "block_type": 34, "children": ["C1"],
             "quote_container": {}}
    assert render_block(block) == ""


def test_equation_block():
    """独立公式块（type 16）→ $$...$$"""
    block = {"block_id": "E1", "block_type": 16,
             "equation": {"content": "E=mc^2"}}
    assert render_block(block) == "$$\nE=mc^2\n$$"


def test_document_with_callout_and_equation():
    """文档级：callout 内文本 + 行内公式 + 独立公式块全部还原，无 [未知块]"""
    blocks = [
        {"block_id": "P", "block_type": 1, "parent_id": "", "children": ["T1", "Q1", "C1", "E1"]},
        {"block_id": "T1", "block_type": 2, "parent_id": "P",
         "text": {"elements": [
             {"text_run": {"content": "给定"}},
             {"equation": {"content": "\\pi_{0}"}},
             {"text_run": {"content": "类似"}},
         ]}},
        {"block_id": "Q1", "block_type": 34, "parent_id": "P",
         "children": ["C1x"], "quote_container": {}},
        {"block_id": "C1x", "block_type": 2, "parent_id": "Q1",
         "text": {"elements": [{"text_run": {"content": "高亮内容"}}]}},
        {"block_id": "C1", "block_type": 2, "parent_id": "P",
         "text": {"elements": [{"text_run": {"content": "普通文本"}}]}},
        {"block_id": "E1", "block_type": 16, "parent_id": "P",
         "equation": {"content": "LOSS=1"}},
    ]
    md, img_refs = render_document(blocks)
    assert "[未知块]" not in md
    assert "给定$\\pi_{0}$类似" in md
    assert "高亮内容" in md  # callout 子块渲染
    assert "$$\nLOSS=1\n$$" in md
    assert img_refs == []
