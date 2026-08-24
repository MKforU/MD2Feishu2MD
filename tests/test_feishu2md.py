# -*- coding: utf-8 -*-
"""feishu2md 纯函数单测：块 JSON → md 文本"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feishu2md import (
    element_to_md,
    elements_to_md,
    render_block,
    render_document,
)


def el(content, **style):
    """构造 TextElement"""
    run = {"content": content}
    if style:
        run["text_element_style"] = style
    return {"text_run": run}


class TestElementToMd:
    def test_plain(self):
        assert element_to_md(el("hi")) == "hi"

    def test_bold(self):
        assert element_to_md(el("b", bold=True)) == "**b**"

    def test_italic(self):
        assert element_to_md(el("i", italic=True)) == "*i*"

    def test_bold_italic(self):
        assert element_to_md(el("bi", bold=True, italic=True)) == "***bi***"

    def test_inline_code(self):
        assert element_to_md(el("c", inline_code=True)) == "`c`"

    def test_strikethrough(self):
        assert element_to_md(el("s", strikethrough=True)) == "~~s~~"

    def test_link(self):
        assert element_to_md(el("t", link={"url": "https://x.com"})) == "[t](https://x.com)"


class TestRenderBlock:
    def test_heading(self):
        b = {"block_type": 3, "heading1": {"elements": [el("T")]}}
        assert render_block(b) == "# T"

    def test_heading_h3(self):
        b = {"block_type": 5, "heading3": {"elements": [el("T")]}}
        assert render_block(b) == "### T"

    def test_text(self):
        b = {"block_type": 2, "text": {"elements": [el("hello")]}}
        assert render_block(b) == "hello"

    def test_text_with_inline(self):
        b = {"block_type": 2, "text": {"elements": [el("a "), el("b", bold=True)]}}
        assert render_block(b) == "a **b**"

    def test_bullet(self):
        b = {"block_type": 12, "bullet": {"elements": [el("x")]}}
        assert render_block(b) == "- x"

    def test_ordered(self):
        b = {"block_type": 13, "ordered": {"elements": [el("x")]}}
        assert render_block(b) == "1. x"

    def test_todo_done(self):
        b = {"block_type": 17, "todo": {"elements": [el("x")], "style": {"done": True}}}
        assert render_block(b) == "- [x] x"

    def test_todo_undone(self):
        b = {"block_type": 17, "todo": {"elements": [el("x")]}}
        assert render_block(b) == "- [ ] x"

    def test_quote(self):
        b = {"block_type": 15, "quote": {"elements": [el("q")]}}
        assert render_block(b) == "> q"

    def test_code_with_lang(self):
        b = {"block_type": 14, "code": {
            "elements": [el("print(1)")],
            "style": {"language": 49},
        }}
        assert render_block(b) == "```python\nprint(1)\n```"

    def test_code_no_lang(self):
        b = {"block_type": 14, "code": {"elements": [el("x")]}}
        assert render_block(b) == "```\nx\n```"

    def test_divider(self):
        b = {"block_type": 22, "divider": {}}
        assert render_block(b) == "---"

    def test_unknown_block(self):
        b = {"block_type": 999, "xxx": {}}
        assert render_block(b) == "[未知块]"


class TestRenderTable:
    def test_table(self):
        # cells 是 block_id 一维数组（行优先），需 blocks_by_id 解析内容
        cell_blocks = [
            {"block_id": "c00", "block_type": 2, "text": {"elements": [el("a")]}},
            {"block_id": "c01", "block_type": 2, "text": {"elements": [el("b")]}},
            {"block_id": "c10", "block_type": 2, "text": {"elements": [el("1")]}},
            {"block_id": "c11", "block_type": 2, "text": {"elements": [el("2")]}},
        ]
        table_block = {"block_type": 31, "table": {
            "property": {"row_size": 2, "column_size": 2},
            "cells": ["c00", "c01", "c10", "c11"],
        }}
        blocks_by_id = {cb["block_id"]: cb for cb in cell_blocks}
        assert render_block(table_block, blocks_by_id) == "| a | b |\n| 1 | 2 |"

    def test_table_pipe_escaped(self):
        cell_blocks = [
            {"block_id": "c00", "block_type": 2, "text": {"elements": [el("a|b")]}},
            {"block_id": "c01", "block_type": 2, "text": {"elements": [el("c")]}},
        ]
        table_block = {"block_type": 31, "table": {
            "property": {"row_size": 1, "column_size": 2},
            "cells": ["c00", "c01"],
        }}
        blocks_by_id = {cb["block_id"]: cb for cb in cell_blocks}
        assert render_block(table_block, blocks_by_id) == "| a\\|b | c |"

    def test_empty_table(self):
        b = {"block_type": 31, "table": {"property": {"row_size": 0, "column_size": 0}, "cells": []}}
        assert render_block(b) == "[空表格]"

    def test_table_container_cells(self):
        # 真实飞书形态：cell 是容器块，children 指向文本块
        cell_children = {
            "cc00": {"block_id": "cc00", "block_type": 2, "text": {"elements": [el("x")]}},
            "cc01": {"block_id": "cc01", "block_type": 2, "text": {"elements": [el("y")]}},
        }
        cell_blocks = [
            {"block_id": "c00", "block_type": 32, "children": ["cc00"]},
            {"block_id": "c01", "block_type": 32, "children": ["cc01"]},
        ]
        table_block = {"block_type": 31, "table": {
            "property": {"row_size": 1, "column_size": 2},
            "cells": ["c00", "c01"],
        }}
        blocks_by_id = {b["block_id"]: b for b in cell_blocks + list(cell_children.values())}
        assert render_block(table_block, blocks_by_id) == "| x | y |"


class TestRenderDocument:
    def test_full_document(self):
        blocks = [
            {"block_id": "b1", "block_type": 3, "heading1": {"elements": [el("Title")]}},
            {"block_id": "b2", "block_type": 2, "text": {"elements": [el("hello "), el("world", bold=True)]}},
            {"block_id": "b3", "block_type": 12, "bullet": {"elements": [el("item")]}},
            {"block_id": "b4", "block_type": 22, "divider": {}},
        ]
        md, refs = render_document(blocks)
        assert md == "# Title\nhello **world**\n- item\n---"
        assert refs == []

    def test_image_collected(self):
        blocks = [
            {"block_id": "b1", "block_type": 2, "text": {"elements": [el("before")]}},
            {"block_id": "b2", "block_type": 27, "image": {"token": "tok123", "width": 100, "height": 50}},
            {"block_id": "b3", "block_type": 2, "text": {"elements": [el("after")]}},
        ]
        md, refs = render_document(blocks)
        assert md == "before\n![图片1](images/image_1.png)\nafter"
        assert refs == [{"file_token": "tok123", "name": "image_1.png", "block_id": "b2"}]

    def test_image_missing_token(self):
        blocks = [{"block_id": "b1", "block_type": 27, "image": {}}]
        md, refs = render_document(blocks)
        assert md == "![图片缺失]()"
        assert refs == []

    def test_unknown_block_skipped_with_marker(self):
        blocks = [
            {"block_id": "b1", "block_type": 999, "xxx": {}},
        ]
        md, refs = render_document(blocks)
        assert md == "[未知块]"
        assert refs == []

    def test_nested_list(self):
        # 顶层 bullet 有 children（子 bullet），递归渲染并缩进
        blocks = [
            {"block_id": "b1", "block_type": 12, "bullet": {"elements": [el("parent")]}, "children": ["b2"]},
            {"block_id": "b2", "block_type": 12, "bullet": {"elements": [el("child")]}},
        ]
        md, refs = render_document(blocks)
        assert md == "- parent\n  - child"
        assert refs == []

    def test_table_cell_not_duplicated(self):
        # 保序 read_blocks 后 table/cell/text 子块都在扁平列表里，
        # render_document 应只渲染 table 一次，不重复渲染 cell 与 text
        cell_children = {
            "t1": {"block_id": "t1", "block_type": 2, "text": {"elements": [el("a")]}},
            "t2": {"block_id": "t2", "block_type": 2, "text": {"elements": [el("b")]}},
        }
        cell_blocks = [
            {"block_id": "c1", "block_type": 32, "children": ["t1"]},
            {"block_id": "c2", "block_type": 32, "children": ["t2"]},
        ]
        table_block = {
            "block_id": "tbl", "block_type": 31,
            "table": {"property": {"row_size": 1, "column_size": 2}, "cells": ["c1", "c2"]},
            "children": ["c1", "c2"],
        }
        blocks = [table_block] + cell_blocks + list(cell_children.values())
        md, refs = render_document(blocks)
        assert md == "| a | b |"
        assert refs == []

    def test_root_page_block(self):
        # 模拟真实飞书 /blocks 返回：根 page 块(type 1, parent_id="") + 标题 + 正文。
        # 应无 [未知块]、无整体缩进。
        blocks = [
            {"block_id": "root", "block_type": 1, "parent_id": "", "children": ["h1", "t1"]},
            {"block_id": "h1", "block_type": 3, "parent_id": "root", "heading1": {"elements": [el("标题")]}},
            {"block_id": "t1", "block_type": 2, "parent_id": "root", "text": {"elements": [el("正文")]}},
        ]
        md, refs = render_document(blocks)
        assert md == "# 标题\n正文"
        assert refs == []

    def test_root_page_block_nested_list(self):
        # 根 page 块 + 嵌套列表：只缩进嵌套项，不整体缩进
        blocks = [
            {"block_id": "root", "block_type": 1, "parent_id": "", "children": ["b1"]},
            {"block_id": "b1", "block_type": 12, "parent_id": "root", "bullet": {"elements": [el("parent")]}, "children": ["b2"]},
            {"block_id": "b2", "block_type": 12, "parent_id": "b1", "bullet": {"elements": [el("child")]}},
        ]
        md, refs = render_document(blocks)
        assert md == "- parent\n  - child"
        assert refs == []
