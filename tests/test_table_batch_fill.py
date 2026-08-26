"""回归测试：表格批量填充优化（2026-08-27）

旧实现逐格 POST children 填充表格，两个问题：
1. 慢：每个 cell 一个请求（140 格 = 140 请求，卡在 QPS 限流上）
2. 空块残留 bug：新建空表每个 cell 已自带空 text block，逐格 POST
   会再新增一个块 → 每个 cell 多一个空块

优化：读回 cell 自带 text block，用 batch_update update_text_elements
批量更新内容。内容等价（cell 里一个 text block，内容同原 md 行内文本），
且消除了空块残留。
"""
import feishu_api as fa


def _client_with_table(monkeypatch, rows, cell_children):
    """构造 FeishuClient，mock 建表/拉块/批量更新。返回 (client, calls)。

    cell_children: cell block_id → 自带 children 列表（可空模拟"无自带块"）
    """
    client = fa.FeishuClient("id", "secret")
    calls = {"batches": [], "post_children": [], "read_blocks": 0}

    n_cells = len(rows) * max(len(r) for r in rows)
    cells = [f"C{i}" for i in range(n_cells)]

    def fake_add_children(doc_id, children):
        return [{"block_id": "TABLE1"}]

    def fake_batch_update(doc_id, requests):
        calls["batches"].append(requests)  # 每次调用一批

    def fake_get_block(doc_id, tid):
        return {"data": {"block": {"table": {"cells": cells}}}}

    def fake_read_blocks(doc_id):
        calls["read_blocks"] += 1
        blocks = []
        for cid in cells:
            ch = cell_children.get(cid)
            if ch is None:  # 默认自带一个 text block
                ch = [cid + "_t"]
            if ch:
                blocks.append({"block_id": cid, "block_type": 32, "children": ch})
        return blocks

    def fake_request(method, url, **kwargs):
        calls["post_children"].append(url)

    monkeypatch.setattr(client, "add_children", fake_add_children)
    monkeypatch.setattr(client, "_batch_update", fake_batch_update)
    monkeypatch.setattr(client, "_get_block", fake_get_block)
    monkeypatch.setattr(client, "read_blocks", fake_read_blocks)
    monkeypatch.setattr(client, "_request", fake_request)
    return client, calls


def _flatten(calls):
    return [r for batch in calls["batches"] for r in batch]


def test_create_table_batch_fills_with_correct_content(monkeypatch):
    client, calls = _client_with_table(monkeypatch, [["a", "b"], ["c", "d"]], {})
    client.create_table("DOC", {"rows": [["a", "b"], ["c", "d"]]})

    # 全部走 batch_update，不逐格 POST
    assert calls["post_children"] == [], "不应逐格 POST children"
    flat = _flatten(calls)
    assert len(flat) == 4

    # 内容与 block_id 正确映射
    contents = [r["update_text_elements"]["elements"][0]["text_run"]["content"] for r in flat]
    assert contents == ["a", "b", "c", "d"], contents
    ids = [r["block_id"] for r in flat]
    assert ids == ["C0_t", "C1_t", "C2_t", "C3_t"], ids


def test_create_table_fallback_when_cell_has_no_text_block(monkeypatch):
    # C1 无自带 text block → 该 cell 回退 POST children
    client, calls = _client_with_table(
        monkeypatch, [["a", "b"]], {"C0": ["C0_t"], "C1": []}
    )
    client.create_table("DOC", {"rows": [["a", "b"]]})

    assert len(calls["post_children"]) == 1, "无自带块的 cell 应回退逐格新建"
    # 有自带块的走 batch_update
    assert len(_flatten(calls)) == 1


def test_create_table_splits_batch_over_100(monkeypatch):
    # 10 行 x 11 列 = 110 格（超 100）→ 填充分 2 批（100 + 10）
    rows = [["x"] * 11 for _ in range(10)]
    client, calls = _client_with_table(monkeypatch, rows, {})
    client.create_table("DOC", {"rows": rows})
    # 过滤出填充批次（含 update_text_elements；扩表批次是 insert_table_row/column）
    fill_batches = [b for b in calls["batches"] if b and "update_text_elements" in b[0]]
    assert len(fill_batches) == 2, f"填充应分 2 批，实际 {len(fill_batches)}"
    assert len(fill_batches[0]) == 100
    assert len(fill_batches[1]) == 10


def test_empty_cell_uses_nonempty_elements(monkeypatch):
    """审核建议：某行短于 n_cols 时补齐的空 cell，elements 应为 [text_run("")] 而非空列表"""
    rows = [["a"], ["x", "y"]]  # n_cols=2，第一行第 2 列补齐为空
    client, calls = _client_with_table(monkeypatch, rows, {})
    client.create_table("DOC", {"rows": rows})
    flat = _flatten(calls)
    contents = [r["update_text_elements"]["elements"][0]["text_run"]["content"] for r in flat]
    assert contents == ["a", "", "x", "y"], contents
    # 空 cell 传含空 content 的 text_run（语义明确），而非空 elements 列表
    assert flat[1]["update_text_elements"]["elements"] == [{"text_run": {"content": ""}}]


def test_read_blocks_called_once(monkeypatch):
    """审核建议：单次 create_table 只应全量拉取 read_blocks 一次"""
    client, calls = _client_with_table(monkeypatch, [["a", "b"]], {})
    client.create_table("DOC", {"rows": [["a", "b"]]})
    assert calls["read_blocks"] == 1, "create_table 应只 read_blocks 一次"
