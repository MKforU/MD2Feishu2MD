# -*- coding: utf-8 -*-
"""md → 飞书块列表（纯函数，无网络依赖）

支持：标题、行内格式（**粗体** / *斜体* / `行内代码` / ~~删除线~~ / [链接](url)）、
数学公式（$...$ 行内 / $$...$$ 块级，转飞书 equation）、
无序/有序/任务列表、引用、代码块（带语言高亮）、分割线、表格、图片。

- ``parse_md(path) -> list[item]``  解析 md 文件为 item 列表
- ``item_to_child(item) -> dict``   单个 item → 飞书 children JSON
- ``inline_elements(text) -> list``  行内 markdown → 飞书 TextElement 列表

item kind: heading / bullet / ordered / todo / quote / code / divider / table / image / equation / text

参考实现：C:\\Users\\13555\\.claude\\skills\\md-to-feishu-docx\\scripts\\md2feishu.py
（此处仅保留纯转换部分，凭据/HTTP 在 feishu_api.py）
"""
import os
import re

# 飞书 CodeLanguage 枚举（官方 docx-v1 数据结构文档），语言不在表内时不传 style.language
LANG = {
    "text": 1, "txt": 1, "plaintext": 1,
    "abap": 2, "ada": 3, "apache": 4, "apex": 5, "asm": 6, "assembly": 6,
    "bash": 7, "sh": 7, "zsh": 7, "shell": 7,
    "csharp": 8, "cs": 8, "c#": 8, "c++": 9, "cpp": 9, "c": 10,
    "css": 12, "coffeescript": 13, "d": 14, "dart": 15, "delphi": 16,
    "django": 17, "dockerfile": 18, "erlang": 19, "fortran": 20,
    "go": 22, "golang": 22, "groovy": 23, "html": 24, "http": 26,
    "haskell": 27, "json": 28, "java": 29, "javascript": 30, "js": 30,
    "julia": 31, "kotlin": 32, "kt": 32, "latex": 33, "tex": 33,
    "lisp": 34, "lua": 36, "matlab": 37, "makefile": 38,
    "markdown": 39, "md": 39, "nginx": 40, "objectivec": 41, "objc": 41,
    "php": 43, "perl": 44, "powershell": 46, "ps1": 46,
    "prolog": 47, "protobuf": 48, "proto": 48,
    "python": 49, "py": 49, "r": 50, "ruby": 52, "rb": 52,
    "rust": 53, "rs": 53, "scss": 55, "sql": 56, "scala": 57,
    "swift": 61, "typescript": 63, "ts": 63, "vb": 65, "vba": 65,
    "xml": 66, "yaml": 67, "yml": 67, "cmake": 68, "diff": 69,
    "graphql": 71, "toml": 75,
}

# 顺序：粗体 → 斜体 → 删除线 → 行内代码 → 链接 → 公式（$$ 优先于 $）
# （内容不含同类标记，不支持嵌套）
INLINE_RE = re.compile(
    r"(\*\*[^*\n]+\*\*"                      # **bold**
    r"|\*[^*\n]+\*"                           # *italic*
    r"|~~[^~\n]+~~"                           # ~~strike~~
    r"|`[^`\n]+`"                             # `code`
    r"|\[[^\]\n]+\]\([^)\n]+\)"               # [text](url)
    r"|\$\$[^$\n]+\$\$"                       # $$...$$ 行内公式
    r"|(?<!\$)\$[^\d\s$][^$\n]*\$"            # $...$ 行内公式（首字符非数字防货币误判；允许内部空格）
    ")"
)
LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)?")


def text_run(content, bold=False, italic=False, code=False, strike=False, link=None):
    tr = {"content": content}
    style = {}
    if bold:
        style["bold"] = True
    if italic:
        style["italic"] = True
    if code:
        style["inline_code"] = True
    if strike:
        style["strikethrough"] = True
    if link:
        style["link"] = {"url": link}
    if style:
        tr["text_element_style"] = style
    return {"text_run": tr}


def inline_elements(text):
    """一行 markdown → 飞书 TextElement 列表"""
    els, pos = [], 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            els.append(text_run(text[pos:m.start()]))
        t = m.group(0)
        if t.startswith("**"):
            els.append(text_run(t[2:-2], bold=True))
        elif t.startswith("~~"):
            els.append(text_run(t[2:-2], strike=True))
        elif t.startswith("`"):
            els.append(text_run(t[1:-1], code=True))
        elif t.startswith("["):
            lm = LINK_RE.match(t)
            # 仅当 URL 合法（非锚点 #xxx、含 :// 协议头）才包 link 样式；
            # 其余（锚点、相对路径如 Pytorch/wps1.jpg）降级纯文本，
            # 否则飞书报 1770006 schema mismatch
            if lm and not lm.group(2).startswith("#") and "://" in lm.group(2):
                els.append(text_run(lm.group(1), link=lm.group(2)))
            elif lm:
                els.append(text_run(lm.group(1)))
            else:
                els.append(text_run(t))
        elif t.startswith("$$"):
            # 行内块级公式 $$...$$ → 飞书 equation 元素
            els.append({"equation": {"content": t[2:-2].strip()}})
        elif t.startswith("$"):
            # 行内公式 $...$ → 飞书 equation 元素
            els.append({"equation": {"content": t[1:-1].strip()}})
        else:
            els.append(text_run(t[1:-1], italic=True))
        pos = m.end()
    if pos < len(text):
        els.append(text_run(text[pos:]))
    return els or [text_run("")]


# ---------------------------------------------------------------- 块级解析
def split_table_row(line):
    # 支持转义竖线 \|：先保护再按裸 | 切分，最后还原（与 feishu2md 写出的 \| 对称）
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    body = body.replace("\\|", "\x00")
    return [c.strip().replace("\x00", "|") for c in body.split("|")]


def is_table_separator(line):
    body = line.strip().strip("|")
    return bool(re.fullmatch(r"[\s:|-]+", body)) and "-" in body


def _common_indent(lines):
    """返回代码块所有非空行的最长公共前导缩进（\t 或空格组成的串）。无公共缩进返回 ''"""
    indents = [l[:len(l) - len(l.lstrip("\t "))] for l in lines if l.strip()]
    if not indents:
        return ""
    common = indents[0]
    for ind in indents[1:]:
        # 逐个缩短到是所有人前缀为止
        while common and not ind.startswith(common):
            common = common[:-1]
    return common


def parse_md(path):
    """逐行解析 md → 块 item 列表"""
    lines = open(path, encoding="utf-8").read().splitlines()
    items, i, in_code, code_buf, code_lang = [], 0, False, [], ""

    def flush_code():
        nonlocal code_buf
        if code_buf or code_lang:
            text = "\n".join(code_buf)
            # 缩进围栏（Typora 导出风格 \t```python）内容带公共前缀，剥掉它：
            # 围栏行的缩进 = 公共缩进，超出部分是代码自身的缩进，必须保留
            if code_buf and (code_buf[0].startswith("\t") or code_buf[0].startswith(" ")):
                common = _common_indent(code_buf)
                if common:
                    text = "\n".join(l[len(common):] if l.startswith(common) else l.lstrip() for l in code_buf)
            items.append({"kind": "code", "lang": code_lang, "text": text})
            code_buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if in_code:
            if stripped == "```":
                # 只有裸 ``` 才能闭合。```lang 形式只当开围栏：
                # 防止"作者忘写闭合围栏"时后续 ```lang 被误判为闭合，
                # 造成代码/正文边界级联错位（实测 Pytorch.md 2211-2229 行案例）
                flush_code()
                in_code = False
            else:
                code_buf.append(line)
            i += 1
            continue
        if stripped.startswith("```"):
            in_code = True
            code_lang = stripped[3:].strip()
            i += 1
            continue

        s = line.strip()
        if not s:
            i += 1
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)\s*$", s)
        if m:
            items.append({"kind": "image", "src": m.group(2)})
            i += 1
            continue
        if s.startswith("$$"):
            # 块级公式 $$...$$（display math）
            m = re.match(r"^\$\$(.+?)\$\$\s*$", s)
            if m and m.group(1).strip():
                # 纯单行块级公式：$$...$$（整行，无尾随文本）
                items.append({"kind": "equation", "text": m.group(1).strip()})
                i += 1
                continue
            if s == "$$":
                # 多行块级公式：$$ 独占一行开始，收集到下一个独占 $$
                eq_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() != "$$":
                    eq_lines.append(lines[i].strip())
                    i += 1
                i += 1  # 跳过结尾 $$
                eq_text = "\n".join(eq_lines).strip()
                if eq_text:
                    items.append({"kind": "equation", "text": eq_text})
                continue
            # $$ 开头但非纯块级公式（尾随文本 / 空公式 $$/$$$$）→ 落到下方普通 text，
            # 由 inline_elements 按行内公式处理，避免丢数据
        elif s.startswith("$"):
            # 单 $ 包裹的块级公式（独占一行，Typora 风格）：$...$
            m = re.match(r"^\$(.+?)\$\s*$", s)
            if m and m.group(1).strip():
                items.append({"kind": "equation", "text": m.group(1).strip()})
                i += 1
                continue
            # $ 开头但非纯公式（尾随文本等）→ 落到下方普通 text
        m = re.match(r"^(#{1,9})\s+(.*)", s)
        if m:
            items.append({"kind": "heading", "level": len(m.group(1)), "text": m.group(2)})
            i += 1
            continue
        m = re.match(r"^[-*+]\s+\[([ xX])\]\s+(.*)", s)
        if m:
            items.append({"kind": "todo", "done": m.group(1).lower() == "x", "text": m.group(2)})
            i += 1
            continue
        m = re.match(r"^[-*+]\s+(.*)", s)
        if m:
            items.append({"kind": "bullet", "text": m.group(1)})
            i += 1
            continue
        m = re.match(r"^\d+[.)]\s+(.*)", s)
        if m:
            items.append({"kind": "ordered", "text": m.group(1)})
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            items.append({"kind": "divider"})
            i += 1
            continue
        m = re.match(r"^>\s*(.*)", s)
        if m:
            items.append({"kind": "quote", "text": m.group(1)})
            i += 1
            continue
        if s.startswith("|") and s.endswith("|"):
            rows = []
            while i < len(lines):
                t = lines[i].strip()
                if not (t.startswith("|") and t.endswith("|")):
                    break
                if not is_table_separator(t):
                    rows.append(split_table_row(t))
                i += 1
            if rows:
                items.append({"kind": "table", "rows": rows})
            continue
        items.append({"kind": "text", "text": s})
        i += 1

    if in_code:
        flush_code()
    return items


# ---------------------------------------------------------------- item → children JSON
def item_to_child(item):
    kind = item["kind"]
    if kind == "heading":
        lvl = min(item["level"], 9)
        return {k: v for k, v in {
            "block_type": 2 + lvl,
            f"heading{lvl}": {"elements": inline_elements(item["text"])},
        }.items()}
    if kind == "bullet":
        return {"block_type": 12, "bullet": {"elements": inline_elements(item["text"])}}
    if kind == "ordered":
        return {"block_type": 13, "ordered": {"elements": inline_elements(item["text"])}}
    if kind == "todo":
        body: dict = {"elements": inline_elements(item["text"])}
        if item["done"]:
            body["style"] = {"done": True}
        return {"block_type": 17, "todo": body}
    if kind == "quote":
        return {"block_type": 15, "quote": {"elements": inline_elements(item["text"])}}
    if kind == "code":
        body = {"elements": [{"text_run": {"content": item["text"]}}]}
        lang_id = LANG.get(item["lang"].lower())
        if lang_id:
            body["style"] = {"language": lang_id}
        return {"block_type": 14, "code": body}
    if kind == "divider":
        return {"block_type": 22, "divider": {}}
    if kind == "image":
        return {"block_type": 27, "image": {}}
    if kind == "equation":
        # 块级公式：独立 text 块，elements 仅一个 equation 元素
        return {"block_type": 2, "text": {"elements": [{"equation": {"content": item["text"]}}]}}
    if kind == "table":
        raise ValueError("table 块不能通过 item_to_child 转换，请用 feishu_api.create_table 两步创建")
    return {"block_type": 2, "text": {"elements": inline_elements(item["text"])}}


def parse_md_text(text):
    """解析 md 文本（不落盘）→ item 列表。测试与内存场景用"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        return parse_md(tmp)
    finally:
        os.unlink(tmp)
