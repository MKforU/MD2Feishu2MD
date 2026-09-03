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


def test_url_encoded_link_roundtrip():
    """URL 编码链接（飞书反向导出形态）往返不丢：unquote 后含 :// → 保留 link"""
    els = inline_elements("[github](https%3A%2F%2Fgithub.com%2FPhysical-Intelligence%2Fopenpi)")
    assert len(els) == 1, els
    run = els[0]["text_run"]
    assert run["content"] == "github"
    # 解码后的真实 URL 存入飞书
    assert run["text_element_style"]["link"]["url"] == "https://github.com/Physical-Intelligence/openpi"


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


def test_single_dollar_block_softbreak():
    """单 $ 块级公式内容含软换行（Shift+Enter）→ 收集为单个 equation"""
    md = "$\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}$\n"
    items = parse_md_text(md)
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1, items
    assert "\\begin{aligned}" in eqs[0]["text"]
    assert "c &= d" in eqs[0]["text"]


def test_dollar_dollar_block_softbreak():
    """$$ 紧跟内容跨软换行 → 收集为单个 equation"""
    md = "$$\\begin{aligned}\na &= b\n\\end{aligned}$$\n"
    items = parse_md_text(md)
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1, items
    assert "\\begin{aligned}" in eqs[0]["text"]
    assert "a &= b" in eqs[0]["text"]


def test_single_dollar_own_line_multiline():
    """单 $ 独占行包裹的多行公式 → equation"""
    items = parse_md_text("$\nE = mc^2\n$\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1, items
    assert eqs[0]["text"] == "E = mc^2"


def test_chinese_note_after_dollar_not_swallowed():
    """$$ 后紧跟中文说明（非公式）→ 不跨行吞后续内容，落为普通 text"""
    items = parse_md_text("$$公式说明：\n下面这段是正文\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert eqs == [], f"中文说明不应误判为公式: {eqs}"
    texts = " ".join(it["text"] for it in items if it["kind"] == "text")
    assert "正文" in texts, "正文不能被吞进公式"


def test_closed_marker_with_trailing_text():
    """闭合 $$ 后有正文（"$$ 也叫做贝尔曼方程"）→ 公式 + 正文分离，正文不吞"""
    md = ("$$\\begin{aligned}\nV_\\pi &= \\mathbb{E}[x] \\\\\\\n"
          "\\end{aligned}$$ 也叫做贝尔曼方程。\n下面这段是正文。\n")
    items = parse_md_text(md)
    eqs = [it for it in items if it["kind"] == "equation"]
    assert len(eqs) == 1, items
    assert "\\begin{aligned}" in eqs[0]["text"]
    assert "V_\\pi" in eqs[0]["text"]
    # 正文保留，且不被吞进公式
    texts = " ".join(it["text"] for it in items if it["kind"] == "text")
    assert "也叫做贝尔曼方程" in texts, texts
    assert "下面这段是正文" in texts, texts
    assert "下面这段是正文" not in eqs[0]["text"], "正文不能吞进公式"


def test_currency_after_dollar_not_swallowed():
    """$ 后紧跟数字（货币 $5）→ 不跨行收集，落为普通 text"""
    items = parse_md_text("$5 和 $10\n")
    eqs = [it for it in items if it["kind"] == "equation"]
    assert eqs == [], items
