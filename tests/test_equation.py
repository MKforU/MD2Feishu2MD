"""回归测试：数学公式转飞书 equation（2026-08-27）

md 的 $...$ 行内公式、$$...$$ 块级公式应转成飞书 equation 元素
（{"equation": {"content": "KaTeX 源码"}}），而非原样保留 $ 符号。

飞书新版 docx API：equation 是 TextElement 的顶层类型（与 text_run 并列），
content 字段为 KaTeX 语法（required）。实测验证过读回完整保留。
"""
from md2feishu import inline_elements, item_to_child, parse_md_text


def test_inline_equation_in_text():
    """行内 $...$ 嵌在文本里 → equation 元素"""
    els = inline_elements("奖励用 $r(x,y)$ 表示")
    assert {"equation": {"content": "r(x,y)"}} in els, els
    # 前后文本保留为 text_run
    assert any(e.get("text_run", {}).get("content", "") == "奖励用 " for e in els)
    assert any(e.get("text_run", {}).get("content", "") == " 表示" for e in els)


def test_inline_display_equation():
    """行内 $$...$$ → equation 元素（内容为 KaTeX）"""
    els = inline_elements("(循环：$$S_0,A_0$$)")
    assert {"equation": {"content": "S_0,A_0"}} in els, els


def test_inline_equation_no_dollar_leftover():
    """公式转换后不残留 $ 符号"""
    els = inline_elements("值 $x^2$ 大")
    all_text = "".join(
        e.get("text_run", {}).get("content", "") or e.get("equation", {}).get("content", "")
        for e in els
    )
    assert "$" not in all_text, f"残留 $ 符号: {all_text!r}"


def test_block_equation_single_line():
    """块级单行公式 $$...$$ → equation item"""
    items = parse_md_text("$$\\mathbb{E}[x]=\\sum_xxp(x)$$\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1
    assert eqs[0]["text"] == "\\mathbb{E}[x]=\\sum_xxp(x)"
    child = item_to_child(eqs[0])
    assert child["block_type"] == 2
    assert child["text"]["elements"] == [{"equation": {"content": "\\mathbb{E}[x]=\\sum_xxp(x)"}}]


def test_block_equation_multi_line():
    """块级多行公式（$$ 独占行包裹）→ 单个 equation item"""
    md = "$$\n\\begin{aligned}\na &= b + c \\\\\n&= e + f\n\\end{aligned}\n$$\n"
    items = parse_md_text(md)
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1, items
    assert "\\begin{aligned}" in eqs[0]["text"]
    assert "a &= b + c" in eqs[0]["text"]


def test_plain_dollar_not_treated_as_equation():
    """不配对/无闭合的 $ 不误识别为公式"""
    els = inline_elements("价格 $5 美元")
    # 单个 $ 无闭合 → 原样文本
    assert all("equation" not in e for e in els), els


def test_block_equation_with_trailing_text_not_dropped():
    """P0：$$...$$ 后带尾随文本时，公式+文字都不能丢"""
    items = parse_md_text("$$E=mc^2$$ 这是公式\n")
    # 应落为普通 text，而非 equation（丢数据）
    eqs = [it for it in items if it["kind"] == "equation"]
    assert eqs == [], f"不应判为块级公式而丢文字: {eqs}"
    texts = [it for it in items if it["kind"] == "text"]
    assert texts, "应落为普通 text"
    els = inline_elements(texts[0]["text"])
    # 公式部分转 equation，尾随文字保留
    assert {"equation": {"content": "E=mc^2"}} in els, els
    assert any(e.get("text_run", {}).get("content", "") == " 这是公式" for e in els), els


def test_two_unpaired_dollars_not_equation():
    """P1：一行两个未闭合 $（货币）不误判为公式"""
    els = inline_elements("价格 $5 和 $10 美元")
    assert all("equation" not in e for e in els), els


def test_unpaired_dollar_dollar_not_equation():
    """P2：不配对 $$abc$ 不产出假公式"""
    els = inline_elements("$$abc$")
    assert all("equation" not in e for e in els), els


def test_empty_block_equation_not_created():
    """P3：空公式 $$ $$ 不产出空 content 方程"""
    items = parse_md_text("$$ $$\n下一行文本\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert eqs == [], f"空公式不应产出 equation: {eqs}"
    texts = [it for it in items if it["kind"] == "text"]
    assert any("下一行文本" in it["text"] for it in texts), "后续文本不能被吞进空公式"


def test_dollar_dollar_dollar_dollar_not_swallow():
    """P3：$$$$ 不吞后续行"""
    items = parse_md_text("$$$$\n正文内容\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert eqs == [], items
    texts = [it for it in items if it["kind"] == "text"]
    assert any("正文内容" in it["text"] for it in texts), "正文不能被 $$$$ 吞掉"


def test_inline_equation_with_spaces():
    """含空格的行内公式（如 $a | s$、$\boxed{G_t = R_t + ...}$）应识别为公式"""
    els = inline_elements("即 $a | s$ 表示")
    assert {"equation": {"content": "a | s"}} in els, els


def test_inline_equation_boxed_with_spaces():
    els = inline_elements("$\\boxed{G_t = R_t + \\gamma R_{t+1} + \\cdots}$")
    assert any("equation" in e and "G_t = R_t" in e["equation"]["content"] for e in els), els


def test_single_dollar_block_equation():
    """单 $ 包裹独占一行的公式（Typora 风格）→ 块级 equation"""
    items = parse_md_text("$\\mathbb{E}[x]=\\sum_x x p(x)$\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1, items
    assert eqs[0]["text"] == "\\mathbb{E}[x]=\\sum_x x p(x)"
    child = item_to_child(eqs[0])
    assert child["text"]["elements"] == [{"equation": {"content": "\\mathbb{E}[x]=\\sum_x x p(x)"}}]
