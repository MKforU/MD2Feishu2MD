"""回归测试：行内链接相对路径降级（2026-08-26，1770006 schema mismatch 案例）

用户 Pytorch.md 中 `![img](Pytorch/wps1.jpg)` 的行内图片引用被 LINK_RE
匹配为链接，link.url="Pytorch/wps1.jpg" 是相对路径，飞书 add_children
报 1770006 schema mismatch（已用真实 API 矩阵测试实锤：相对路径 FAIL，
http 链接 OK）。修复：非 :// 开头的 URL 降级为纯文本，不包 link 样式。
"""
from md2feishu import inline_elements


def _links(els):
    """提取 (content, link_url or None) 列表"""
    out = []
    for e in els:
        tr = e["text_run"]
        url = (tr.get("text_element_style") or {}).get("link", {}).get("url")
        out.append((tr["content"], url))
    return out


def test_relative_path_link_degraded_to_plain_text():
    """相对路径链接（行内图片引用）：降级纯文本，不带 link 样式"""
    els = inline_elements("gamma：底数，![img](Pytorch/wps1.jpg)")
    links = _links(els)
    # 无任何元素带 link
    assert all(u is None for _, u in links), links
    # 文本内容保留（img 文件名作为提示）
    assert any("gamma" in c for c, _ in links)
    assert any("img" in c for c, _ in links)


def test_http_link_still_works():
    """http(s) 链接：正常保留 link 样式"""
    els = inline_elements("[官网](https://pytorch.org/) 教程")
    links = _links(els)
    assert ("官网", "https://pytorch.org/") in links, links


def test_anchor_link_still_degraded():
    """锚点链接（#xxx）：保持降级行为（既有逻辑回归）"""
    els = inline_elements("[跳转](#section)")
    links = _links(els)
    assert all(u is None for _, u in links), links
