# -*- coding: utf-8 -*-
"""回归测试：有序列表反向导出序号递增"""
from feishu2md import render_document, render_block


def _doc():
    return [
        {"block_id": "P", "block_type": 1, "parent_id": "", "children": ["O1", "O2", "O3", "T1", "O4"]},
        {"block_id": "O1", "block_type": 13, "parent_id": "P", "ordered": {"elements": [{"text_run": {"content": "一"}}], "style": {}}},
        {"block_id": "O2", "block_type": 13, "parent_id": "P", "ordered": {"elements": [{"text_run": {"content": "二"}}], "style": {}}},
        {"block_id": "O3", "block_type": 13, "parent_id": "P", "ordered": {"elements": [{"text_run": {"content": "三"}}], "style": {}}},
        {"block_id": "T1", "block_type": 2, "parent_id": "P", "text": {"elements": [{"text_run": {"content": "打断段落"}}]}},
        {"block_id": "O4", "block_type": 13, "parent_id": "P", "ordered": {"elements": [{"text_run": {"content": "新列表"}}], "style": {}}},
    ]


def test_ordered_sequence_increments():
    """连续有序列表 1. 2. 3. 递增"""
    md, _ = render_document(_doc())
    assert "1. 一" in md
    assert "2. 二" in md
    assert "3. 三" in md


def test_ordered_resets_after_interruption():
    """被普通块打断后新列表从 1. 重新计数"""
    md, _ = render_document(_doc())
    assert "1. 新列表" in md


def test_render_block_default_ordered_num():
    """直接调 render_block 不传序号 → 默认 1（向后兼容）"""
    block = {"block_id": "X", "block_type": 13,
             "ordered": {"elements": [{"text_run": {"content": "项"}}], "style": {}}}
    assert render_block(block).startswith("1. ")


def test_nested_ordered_by_depth():
    """不同层级各自计数（子列表从 1 开始）"""
    blocks = [
        {"block_id": "P", "block_type": 1, "parent_id": "", "children": ["O1", "C1"]},
        {"block_id": "O1", "block_type": 13, "parent_id": "P", "children": ["C1"],
         "ordered": {"elements": [{"text_run": {"content": "父一"}}], "style": {}}},
        {"block_id": "C1", "block_type": 13, "parent_id": "O1",
         "ordered": {"elements": [{"text_run": {"content": "子一"}}], "style": {}}},
    ]
    md, _ = render_document(blocks)
    assert "1. 父一" in md
    assert "1. 子一" in md  # 子层级重置
