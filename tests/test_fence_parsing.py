"""回归测试：代码围栏解析修复（2026-08-26，用户 Pytorch.md 案例）

两个 bug：
1. 作者忘写闭合围栏时，后续 ```lang 被误判为闭合 → 代码/正文边界级联错位
   （实测：SGD 代码块未闭合，后面 600+ 行全部错乱）
   修复：只有裸 ``` 才能闭合代码块。
2. 缩进围栏（Typora 导出风格 \t```python）内容带公共 tab 前缀，未剥除
   修复：剥掉公共缩进，保留代码自身相对缩进。
"""
import os
import tempfile

from md2feishu import parse_md, parse_md_text


def _parse(text):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return parse_md(path)
    finally:
        os.unlink(path)


def test_unclosed_fence_does_not_cascade():
    """忘写闭合围栏：错误必须就地隔离（到下一个裸 ``` 为止），不能传染后续内容。

    结构镜像真实 Pytorch.md 的 SGD 块案例：坏块未闭合 → 中间正文被吸入 →
    下一个 ```python 视为内容（非闭合）→ 直到训练循环自带的裸 ``` 才闭合。
    """
    text = (
        "# 标题\n\n"
        "1. SGD优化器\n\n"
        "\t```python\n"          # 开围栏（未闭合，作者忘写 ```）
        "\tclass SGD(Optimizer):\n"
        "\t    pass\n"
        "\n"
        "2. 动量法。\n"            # 被吸入坏块的正文
        "\n"
        "\t#开始训练模型\n"
        "\t```python\n"           # 新开围栏 = 内容（非闭合，CommonMark 语义）
        "\tfor epoch in range(20):\n"
        "\t    pass\n"
        "\t```\n"                 # 裸 ``` → 在这里闭合整个坏块
        "\n"
        "## 第二章\n\n"           # 必须恢复为标题
        "正文恢复。\n"
    )
    items = _parse(text)
    headings = [it["text"] for it in items if it["kind"] == "heading"]
    assert "第二章" in headings, f"级联错位未修复: {headings}"
    # 坏块吸收了正文 + 后续围栏内容（只脏它自己），但语言标记保留首个
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    assert "class SGD" in codes[0]["text"]
    assert "动量法" in codes[0]["text"], "未闭合内容应被吸入坏块（就地隔离）"
    assert "for epoch" in codes[0]["text"]
    # 坏块之后的正文正常解析
    assert any(it["kind"] == "text" and "正文恢复" in it["text"] for it in items)


def test_indented_fence_strips_common_prefix():
    """缩进围栏：剥掉公共 tab 前缀，保留代码内部相对缩进"""
    text = (
        "1. 列表项\n\n"
        "\t```python\n"
        "\timport torch\n"
        "\tdef f():\n"
        "\t    return 1\n"
        "\t```\n"
    )
    items = _parse(text)
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    body = codes[0]["text"]
    assert body.startswith("import torch"), f"公共前缀未剥除: {body[:30]!r}"
    assert "    return 1" in body, "内部相对缩进被破坏"
    assert "\tdef f" not in body, "tab 前缀残留"


def test_plain_fence_still_works():
    """普通顶格围栏回归：行为不变"""
    text = "# t\n\n```python\nx = 1\n```\n\n正文\n"
    items = _parse(text)
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    assert codes[0]["text"] == "x = 1"
    assert codes[0]["lang"] == "python"


def test_eof_unclosed_indented_fence_flushes_and_strips():
    """EOF 未闭合缩进围栏：EOF flush 触发 + 公共前缀剥除（审核建议补充）"""
    text = "# t\n\n\t```python\n\tfoo()\n\tbar()\n"  # 文件结束，无闭合围栏
    items = _parse(text)
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    assert codes[0]["text"] == "foo()\nbar()", f"EOF flush 未剥除: {codes[0]['text']!r}"


def test_mixed_tab_space_indent_partial_strip():
    """tab+空格混合缩进：公共前缀（\\t）剥除，行内额外空格保留（审核建议补充）"""
    text = "\t```python\n\tfoo()\n\t  bar()\n\t```"
    items = _parse(text)
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    # 公共 \t 剥掉，第二行多出的 2 空格（相对缩进）保留
    assert codes[0]["text"] == "foo()\n  bar()", f"实际: {codes[0]['text']!r}"


def test_no_common_prefix_kept_verbatim():
    """完全无公共前缀（\\t 行与空格行并存）：保守原样保留，不误剥"""
    text = "\t```python\n\tfoo()\n  bar()\n\t```"  # \tfoo vs ␣␣bar 无公共前缀
    items = _parse(text)
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    assert "\tfoo()" in codes[0]["text"], "无公共前缀时不应剥除"


def test_plain_fence_indented_content_stripped():
    """顶格围栏 + 整体缩进内容（粘贴自 IDE）：也剥公共缩进（审核观察项2，锁定新行为）"""
    text = "```python\n    import os\n    def f():\n        return 1\n```"
    items = _parse(text)
    codes = [it for it in items if it["kind"] == "code"]
    assert len(codes) == 1
    assert codes[0]["text"].startswith("import os"), f"公共缩进未剥: {codes[0]['text'][:20]!r}"
    assert "    return 1" in codes[0]["text"], "相对缩进被破坏"
