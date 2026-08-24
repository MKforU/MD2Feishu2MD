# -*- coding: utf-8 -*-
"""md2feishu 纯函数单测：parse_md + item_to_child + 行内解析"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from md2feishu import (
    parse_md,
    item_to_child,
    inline_elements,
    split_table_row,
    is_table_separator,
)


# ---------------------------------------------------------------- parse_md
class TestParseMd:
    def test_headings_levels(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("# H1\n## H2\n###### H6", encoding="utf-8")
        items = parse_md(str(p))
        assert [it["kind"] for it in items] == ["heading", "heading", "heading"]
        assert [it["level"] for it in items] == [1, 2, 6]
        assert [it["text"] for it in items] == ["H1", "H2", "H6"]

    def test_bullet_ordered_todo(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("- a\n* b\n+ c\n1. one\n2. two\n- [x] done\n- [ ] todo", encoding="utf-8")
        items = parse_md(str(p))
        kinds = [it["kind"] for it in items]
        assert kinds == ["bullet", "bullet", "bullet", "ordered", "ordered", "todo", "todo"]
        assert items[5]["done"] is True
        assert items[6]["done"] is False
        assert items[0]["text"] == "a"

    def test_quote(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("> quoted", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0] == {"kind": "quote", "text": "quoted"}

    def test_code_block_with_lang(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("```python\nprint(1)\n```", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0]["kind"] == "code"
        assert items[0]["lang"] == "python"
        assert items[0]["text"] == "print(1)"

    def test_code_block_multiline_and_empty(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("```\nline1\n\nline3\n```", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0]["kind"] == "code"
        assert items[0]["lang"] == ""
        assert items[0]["text"] == "line1\n\nline3"

    def test_unclosed_code_fence(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("```python\nprint(1)", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0]["kind"] == "code"
        assert items[0]["text"] == "print(1)"

    def test_divider(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("---", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0] == {"kind": "divider"}

    def test_table(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("| a | b |\n|---|---|\n| 1 | 2 |", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0]["kind"] == "table"
        assert items[0]["rows"] == [["a", "b"], ["1", "2"]]

    def test_table_without_header_separator(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("| a | b |\n| 1 | 2 |", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0]["kind"] == "table"
        assert items[0]["rows"] == [["a", "b"], ["1", "2"]]

    def test_image(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("![alt](img/foo.png)", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0] == {"kind": "image", "src": "img/foo.png"}

    def test_remote_image(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("![x](https://example.com/a.jpg)", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0] == {"kind": "image", "src": "https://example.com/a.jpg"}

    def test_plain_text(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("hello world", encoding="utf-8")
        items = parse_md(str(p))
        assert items[0] == {"kind": "text", "text": "hello world"}

    def test_empty_lines_skipped(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("\n\ntext\n\n", encoding="utf-8")
        items = parse_md(str(p))
        assert len(items) == 1
        assert items[0]["kind"] == "text"

    def test_blank_md(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("", encoding="utf-8")
        assert parse_md(str(p)) == []


# ---------------------------------------------------------------- item_to_child
class TestItemToChild:
    def test_heading_block_type(self):
        child = item_to_child({"kind": "heading", "level": 1, "text": "T"})
        assert child["block_type"] == 3  # 2 + level

    def test_heading_level_capped(self):
        child = item_to_child({"kind": "heading", "level": 20, "text": "T"})
        assert child["block_type"] == 11  # heading9

    def test_bullet(self):
        child = item_to_child({"kind": "bullet", "text": "x"})
        assert child["block_type"] == 12

    def test_ordered(self):
        child = item_to_child({"kind": "ordered", "text": "x"})
        assert child["block_type"] == 13

    def test_todo_done_style(self):
        child = item_to_child({"kind": "todo", "done": True, "text": "x"})
        assert child["block_type"] == 17
        assert child["todo"]["style"]["done"] is True

    def test_todo_undone_no_style(self):
        child = item_to_child({"kind": "todo", "done": False, "text": "x"})
        assert "style" not in child["todo"]

    def test_quote(self):
        child = item_to_child({"kind": "quote", "text": "q"})
        assert child["block_type"] == 15

    def test_code_with_lang(self):
        child = item_to_child({"kind": "code", "lang": "python", "text": "print(1)"})
        assert child["block_type"] == 14
        assert child["code"]["style"]["language"] == 49

    def test_code_unknown_lang(self):
        child = item_to_child({"kind": "code", "lang": "nosuchlang", "text": "x"})
        assert child["block_type"] == 14
        assert "style" not in child["code"]

    def test_divider(self):
        child = item_to_child({"kind": "divider"})
        assert child["block_type"] == 22

    def test_image(self):
        child = item_to_child({"kind": "image", "src": "x.png"})
        assert child["block_type"] == 27

    def test_text(self):
        child = item_to_child({"kind": "text", "text": "hi"})
        assert child["block_type"] == 2

    def test_table_raises(self):
        # table 块必须走 feishu_api.create_table 两步创建，不能 item_to_child
        with pytest.raises(ValueError):
            item_to_child({"kind": "table", "rows": [["a"]]})


# ---------------------------------------------------------------- 行内解析
class TestInlineElements:
    def test_plain(self):
        els = inline_elements("hello")
        assert els == [{"text_run": {"content": "hello"}}]

    def test_bold(self):
        els = inline_elements("a **b** c")
        assert els[0] == {"text_run": {"content": "a "}}
        assert els[1] == {"text_run": {"content": "b", "text_element_style": {"bold": True}}}

    def test_italic(self):
        els = inline_elements("*i*")
        assert els[0]["text_run"]["text_element_style"] == {"italic": True}

    def test_inline_code(self):
        els = inline_elements("`code`")
        assert els[0]["text_run"]["text_element_style"] == {"inline_code": True}

    def test_strikethrough(self):
        els = inline_elements("~~del~~")
        assert els[0]["text_run"]["text_element_style"] == {"strikethrough": True}

    def test_link(self):
        els = inline_elements("[text](https://x.com)")
        assert els[0]["text_run"]["text_element_style"] == {
            "link": {"url": "https://x.com"}
        }

    def test_anchor_link_plain(self):
        # 锚点链接不是合法 URL，降级为纯文本
        els = inline_elements("[a](#sec)")
        assert els[0] == {"text_run": {"content": "a"}}

    def test_empty(self):
        assert inline_elements("") == [{"text_run": {"content": ""}}]


# ---------------------------------------------------------------- 表格工具
class TestTableHelpers:
    def test_split_table_row(self):
        assert split_table_row("| a | b |") == ["a", "b"]

    def test_is_table_separator(self):
        assert is_table_separator("|---|")
        assert is_table_separator("| :--- | ---: |")
        assert not is_table_separator("| a | b |")

    def test_split_table_row_escaped_pipe(self):
        # 转义竖线 \| 应保持为字面量，不被当作分隔符
        assert split_table_row("| a\\|b | c |") == ["a|b", "c"]
