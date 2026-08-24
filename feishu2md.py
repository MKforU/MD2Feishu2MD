# -*- coding: utf-8 -*-
"""飞书块列表 → md 文本（纯函数，无网络依赖）

- ``render_document(blocks) -> (md_text, image_refs)``  全量块 → md 文本 + 待下载图片信息
- ``render_block(block, blocks_by_id=None) -> str``     单个块 → md 文本

image_refs 元素: {"file_token": str, "name": str, "block_id": str}，
供调用方（feishu_api）下载图片到本地 images/ 目录。
"""
import re

# 飞书 block_type → 反向 md 标记
_HEADING_BLOCK_TYPES = {3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9}

# 图片扩展名与 content-type 的粗略映射（下载时按实际内容判断）
IMAGE_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "bmp")


def element_to_md(el):
    """单个 TextElement → md 文本（还原行内样式）"""
    run = el.get("text_run") or {}
    content = run.get("content", "")
    style = run.get("text_element_style") or {}
    if style.get("inline_code"):
        return f"`{content}`"
    if style.get("bold") and style.get("italic"):
        return f"***{content}***"
    if style.get("bold"):
        return f"**{content}**"
    if style.get("italic"):
        return f"*{content}*"
    if style.get("strikethrough"):
        return f"~~{content}~~"
    link = style.get("link") or {}
    if link.get("url"):
        return f"[{content}]({link['url']})"
    return content


def elements_to_md(elements):
    """TextElement 列表 → md 文本"""
    return "".join(element_to_md(el) for el in (elements or []))


def _lang_id_to_name(lang_id):
    """飞书 language id → 语言名（反向 LANG 映射，找不到返回 ''）"""
    return _LANG_REVERSE.get(lang_id, "")


# 从 md2feishu.LANG 生成反向映射（避免重复维护两份表）
def _build_lang_reverse():
    from md2feishu import LANG

    rev = {}
    for name, lid in LANG.items():
        rev.setdefault(lid, name)  # 取第一个名字（如 "text" 而非 "txt"）
    return rev


_LANG_REVERSE = _build_lang_reverse()


def render_block(block, blocks_by_id=None):
    """单个飞书 block → md 文本行。

    blocks_by_id: block_id → block 映射（表格解析 cell 内容用）
    """
    btype = block.get("block_type")
    if btype in _HEADING_BLOCK_TYPES:
        level = _HEADING_BLOCK_TYPES[btype]
        return "#" * level + " " + elements_to_md(block.get(f"heading{level}", {}).get("elements"))
    if btype == 2:  # text
        return elements_to_md(block.get("text", {}).get("elements"))
    if btype == 12:  # bullet
        return "- " + elements_to_md(block.get("bullet", {}).get("elements"))
    if btype == 13:  # ordered
        return "1. " + elements_to_md(block.get("ordered", {}).get("elements"))
    if btype == 17:  # todo
        todo = block.get("todo", {})
        done = "x" if (todo.get("style") or {}).get("done") else " "
        return f"- [{done}] " + elements_to_md(todo.get("elements"))
    if btype == 15:  # quote
        return "> " + elements_to_md(block.get("quote", {}).get("elements"))
    if btype == 14:  # code
        code = block.get("code", {})
        lang = _lang_id_to_name((code.get("style") or {}).get("language"))
        content = "".join(
            (el.get("text_run") or {}).get("content", "")
            for el in (code.get("elements") or [])
        )
        if lang:
            return f"```{lang}\n{content}\n```"
        return f"```\n{content}\n```"
    if btype == 22:  # divider
        return "---"
    if btype == 31:  # table
        return _render_table(block, blocks_by_id or {})
    if btype == 27:  # image（内容由 render_document 收集，这里只返回占位）
        return ""
    # 未知块
    return "[未知块]"


def _render_table(block, blocks_by_id):
    """表格块 → markdown 表格文本"""
    table = block.get("table", {})
    prop = table.get("property") or {}
    row_size = prop.get("row_size", 0)
    col_size = prop.get("column_size", 0)
    cells = table.get("cells") or []
    if not row_size or not col_size:
        return "[空表格]"

    rows = []
    for ri in range(row_size):
        row_cells = []
        for ci in range(col_size):
            cell_id = cells[ri * col_size + ci] if ri * col_size + ci < len(cells) else None
            cell_block = blocks_by_id.get(cell_id) if cell_id else None
            if cell_block:
                # cell 块内的文本子块
                text = _cell_text(cell_block, blocks_by_id)
            else:
                text = ""
            row_cells.append(text.replace("|", "\\|").replace("\n", " "))
        rows.append("| " + " | ".join(row_cells) + " |")
    return "\n".join(rows)


def _cell_text(cell_block, blocks_by_id):
    """表格单元格块 → 文本（支持直接带 text 的块，或带 children 的容器块）"""
    # 直接是文本块（测试/部分 API 形态）
    if cell_block.get("text"):
        return elements_to_md(cell_block["text"].get("elements"))
    # 容器块：children 指向真正文本块
    parts = []
    for cid in cell_block.get("children") or []:
        child = blocks_by_id.get(cid)
        if child:
            parts.append(elements_to_md(child.get("text", {}).get("elements")))
    return "".join(parts)


def render_document(blocks):
    """全量飞书块 → (md 文本, image_refs)。

    image_refs: 按出现顺序收集，元素 {"file_token", "name", "block_id"}，
    图片块在 md 中渲染为 ![name](images/name)，调用方需下载对应图片。
    """
    blocks_by_id = {b.get("block_id"): b for b in blocks if b.get("block_id")}
    lines = []
    image_refs = []
    img_idx = 0

    for block in blocks:
        btype = block.get("block_type")
        if btype == 27:  # image
            img = block.get("image") or {}
            token = img.get("token") or img.get("file_token")
            if not token:
                lines.append("![图片缺失]()")
                continue
            img_idx += 1
            name = f"image_{img_idx}.png"
            # 根据 width/height 无法判断真实格式，统一用 png，下载后按实际类型修正
            lines.append(f"![图片{img_idx}](images/{name})")
            image_refs.append(
                {"file_token": token, "name": name, "block_id": block.get("block_id", "")}
            )
            continue
        line = render_block(block, blocks_by_id)
        if line:
            lines.append(line)

    return "\n".join(lines), image_refs
