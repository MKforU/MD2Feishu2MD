# -*- coding: utf-8 -*-
"""飞书 OpenAPI 封装 —— 所有飞书 HTTP 调用的统一入口。

- 凭据：``get_credentials()``（env 优先 → config.json 回退）
- token：``FeishuClient`` 内部缓存 tenant_access_token（提前 5 分钟刷新）
- 限流：全局线程安全最小间隔限流器 ``RateLimiter``（默认 QPS=2.5 留余量），
  所有请求统一过 ``_request``，识别飞书限流错误（按响应体错误码 99991400 判断，
  HTTP 400/429 均覆盖）后指数退避重试 3 次
- 文档操作：create_doc / add_children / create_table / upload_images / read_blocks /
  download_image / delete_doc
- 目标路径：知识库（wiki）/ 云空间文件夹双分支定位 + 文件夹树 / 知识库树

参考实现：C:\\Users\\13555\\.claude\\skills\\md-to-feishu-docx\\scripts\\md2feishu.py
"""
import json
import os
import re
import tempfile
import threading
import time

import httpx

from md2feishu import inline_elements

API = "https://open.feishu.cn/open-apis"
BATCH = 40  # 一次 children 请求的块数上限

# 飞书限流错误码：HTTP 400（旧版）/ 429（新版）+ 99991400「频率超限」
RATE_LIMIT_CODES = {99991400, 99991401}


class FeishuError(Exception):
    """飞书 API 错误：code 为飞书错误码（非 0），msg 为错误信息"""

    def __init__(self, code, msg=""):
        self.code = code
        self.msg = msg
        super().__init__(f"飞书错误 {code}: {msg}")


class RateLimiter:
    """线程安全最小间隔限流器（令牌桶容量=1 的保守形式）。

    acquire() 保证相邻请求间隔 >= 1/qps 秒，任意滑动窗口内 QPS 不超上限。
    比标准令牌桶（带突发额度）更保守，对飞书限流保护更安全。
    """

    def __init__(self, qps=2.5):
        self.interval = 1.0 / qps
        self._lock = threading.Lock()
        self._last = 0.0

    def set_qps(self, qps):
        if qps <= 0:
            raise ValueError("qps 必须大于 0")
        self.interval = 1.0 / qps

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.interval - now
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


# 全局共享限流器：所有 FeishuClient 默认共用，保证跨 worker 池的总 QPS ≤ 上限
_GLOBAL_LIMITER = RateLimiter(qps=2.5)


def set_global_qps(qps):
    """设置全局限流 QPS（UI 并发配置调用）"""
    _GLOBAL_LIMITER.set_qps(qps)


def get_credentials(config_path="config.json"):
    """获取飞书凭据：env 优先 → config.json 回退（桌面应用用 config.json）。

    config.json 字段：{"app_id": "...", "app_secret": "..."}
    找不到时抛 FeishuError。
    """
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret:
        return app_id, app_secret
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        app_id = (cfg.get("app_id") or "").strip()
        app_secret = (cfg.get("app_secret") or "").strip()
        if app_id and app_secret:
            return app_id, app_secret
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    raise FeishuError(
        "NO_CREDENTIALS",
        "缺少凭据：请设置环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET 或 config.json",
    )


class FeishuClient:
    def __init__(self, app_id, app_secret, limiter=None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.limiter = limiter if limiter is not None else _GLOBAL_LIMITER
        self._token = None
        self._token_expire = 0.0
        self._token_lock = threading.Lock()

    # ------------------------------------------------------------ token
    def _headers(self):
        """获取带缓存的请求头（tenant_access_token 有效期约 2h，提前 5 分钟刷新）"""
        now = time.monotonic()
        if self._token and now < self._token_expire:
            return {"Authorization": f"Bearer {self._token}"}
        with self._token_lock:
            # double-check：拿到锁后可能已被其他线程刷新
            if self._token and time.monotonic() < self._token_expire:
                return {"Authorization": f"Bearer {self._token}"}
            try:
                r = httpx.post(
                    f"{API}/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                    timeout=15,
                ).json()
            except httpx.HTTPError as e:
                raise FeishuError("NETWORK_ERROR", f"获取 token 失败: {e}") from e
            if r.get("code") != 0:
                raise FeishuError(r.get("code"), r.get("msg", "获取 token 失败"))
            self._token = r["tenant_access_token"]
            self._token_expire = now + (r.get("expire", 7200) - 300)
            return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------ 请求入口
    def _request(self, method, url, *, retries=3, timeout=60, **kwargs):
        """统一请求入口：限流 + 识别限流错误指数退避重试。返回 httpx.Response。

        成功与否由业务层检查返回体的 code 字段；这里只管 HTTP 层与限流重试。
        网络/超时异常统一包装为 FeishuError。
        """
        last_code = None
        last_msg = ""
        resp = None
        for attempt in range(retries + 1):
            self.limiter.acquire()
            try:
                resp = httpx.request(method, url, headers=self._headers(), timeout=timeout, **kwargs)
            except httpx.HTTPError as e:
                raise FeishuError("NETWORK_ERROR", f"网络请求失败: {e}") from e
            if resp.status_code in (200, 201):
                return resp
            # 解析错误码判断是否限流
            try:
                data = resp.json()
            except Exception:
                data = {}
            code = data.get("code") if isinstance(data, dict) else None
            last_code, last_msg = code, (data.get("msg", "") if isinstance(data, dict) else "")
            if code in RATE_LIMIT_CODES and attempt < retries:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s 指数退避
                continue
            break
        status = resp.status_code if resp is not None else 0
        text = resp.text[:200] if resp is not None else ""
        raise FeishuError(last_code or status, last_msg or text)

    def _json(self, resp):
        """解析响应体并检查飞书业务 code，非 0 抛 FeishuError"""
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(data.get("code"), data.get("msg", ""))
        return data

    # ------------------------------------------------------------ 文档操作
    def create_doc(self, title, folder_token=None):
        """创建空文档，返回 doc_id"""
        resp = self._request("POST", f"{API}/docx/v1/documents", json={"title": title})
        data = self._json(resp)
        doc_id = data["data"]["document"]["document_id"]
        if folder_token:
            self._json(self._request(
                "POST", f"{API}/drive/v1/files/{doc_id}/move",
                json={"type": "docx", "folder_token": folder_token},
            ))
        return doc_id

    def add_children(self, doc_id, children):
        """批量添加子块，返回创建后的 block 列表（与 children 等长）"""
        resp = self._request(
            "POST", f"{API}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            json={"children": children, "index": -1},
        )
        return self._json(resp)["data"]["children"]

    def create_table(self, doc_id, item):
        """表格两步创建：先建空表（接口上限 9x9），超限逐行/列扩，再逐格填 text"""
        rows = item["rows"]
        n_rows = len(rows)
        n_cols = max(len(r) for r in rows)
        created = self.add_children(doc_id, [{
            "block_type": 31,
            "table": {"property": {"row_size": min(n_rows, 9), "column_size": min(n_cols, 9)}},
        }])
        tid = created[0]["block_id"]

        # 超过 9x9 的部分：batch_update 逐行/列扩表（同一 block 不能在一次 batch 里重复出现）
        for _ in range(n_rows - 9):
            self._batch_update(doc_id, [{"block_id": tid, "insert_table_row": {"row_index": -1}}])
        for _ in range(n_cols - 9):
            self._batch_update(doc_id, [{"block_id": tid, "insert_table_column": {"column_index": -1}}])

        # 扩表后 cells 已变，重新拉取
        data = self._get_block(doc_id, tid)
        cells = data["data"]["block"]["table"]["cells"]

        for ri, row in enumerate(rows):
            for ci in range(n_cols):
                txt = row[ci] if ci < len(row) else ""
                cell_id = cells[ri * n_cols + ci]
                self._request(
                    "POST", f"{API}/docx/v1/documents/{doc_id}/blocks/{cell_id}/children",
                    json={"children": [{"block_type": 2, "text": {"elements": inline_elements(txt)}}],
                          "index": -1},
                )
        return tid

    def _batch_update(self, doc_id, requests):
        resp = self._request(
            "PATCH", f"{API}/docx/v1/documents/{doc_id}/blocks/batch_update",
            json={"requests": requests},
        )
        return self._json(resp)

    def _get_block(self, doc_id, block_id):
        resp = self._request("GET", f"{API}/docx/v1/documents/{doc_id}/blocks/{block_id}")
        return self._json(resp)

    # ------------------------------------------------------------ 图片三步上传
    def upload_images(self, doc_id, img_slots, md_dir):
        """三步上传图片并绑定到图片块。

        img_slots: [(src, block_id), ...]，src 为本地相对路径或 http(s) 外链。
        返回成功数量。
        """
        ok = 0
        for src, bid in img_slots:
            tmp_path = None
            try:
                if src.startswith("http"):
                    try:
                        resp = httpx.get(src, timeout=30, follow_redirects=True)
                    except httpx.HTTPError:
                        continue  # 外链下载失败，跳过该图
                    if resp.status_code != 200:
                        continue
                    ext = _guess_ext(resp.headers.get("content-type", ""), src)
                    tmp_path = os.path.join(tempfile.gettempdir(), f"md2feishu_{bid}.{ext}")
                    with open(tmp_path, "wb") as f:
                        f.write(resp.content)
                    img_path, name = tmp_path, os.path.basename(tmp_path)
                else:
                    img_path = src if os.path.isabs(src) else os.path.join(md_dir, src)
                    name = os.path.basename(img_path)
                if not os.path.exists(img_path):
                    continue
                # 第 2 步：上传 media，parent_node 必须是图片块自身 block_id（不是 doc_id）
                with open(img_path, "rb") as f:
                    ur = self._request(
                        "POST", f"{API}/drive/v1/medias/upload_all",
                        data={"file_name": name, "parent_type": "docx_image", "parent_node": bid,
                              "size": str(os.path.getsize(img_path))},
                        files={"file": (name, f)},
                    )
                udata = self._json(ur)
                # 第 3 步：replace_image 绑定 token
                self._batch_update(
                    doc_id, [{"block_id": bid, "replace_image": {"token": udata["data"]["file_token"]}}],
                )
                ok += 1
            except (FeishuError, OSError, KeyError):
                continue  # 单张失败跳过，不中断整批
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        return ok

    # ------------------------------------------------------------ 读块
    def read_blocks(self, doc_id):
        """拉取文档全量块。

        飞书「获取文档所有块」接口（GET /blocks）本身已返回全部块（扁平、文档序），
        含根 page 块（block_type=1, parent_id=""），无需再递归拉子块。
        """
        all_blocks = []
        page_token = None
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            data = self._json(self._request(
                "GET", f"{API}/docx/v1/documents/{doc_id}/blocks", params=params,
            ))
            all_blocks.extend(data["data"]["items"])
            if not data["data"].get("has_more"):
                break
            page_token = data["data"]["page_token"]
        return all_blocks

    # ------------------------------------------------------------ 图片下载
    def download_image(self, file_token, save_path):
        """下载图片到 save_path，返回 (真实保存路径, 扩展名)。

        根据 content-type 推断真实格式，若与 save_path 扩展名不一致则修正。
        """
        resp = self._request("GET", f"{API}/drive/v1/medias/{file_token}/download")
        content = resp.content
        ctype = (resp.headers.get("content-type") or "").lower()
        ext = _guess_ext(ctype, file_token)
        real_path = save_path
        if ext and not save_path.lower().endswith("." + ext):
            base = os.path.splitext(save_path)[0]
            real_path = f"{base}.{ext}"
        with open(real_path, "wb") as f:
            f.write(content)
        return real_path, ext

    def download_images(self, image_refs, out_dir):
        """批量下载图片。image_refs 来自 feishu2md.render_document。

        返回 {原名: 真实文件名}，调用方据此更新 md 文本中的图片链接。
        """
        os.makedirs(out_dir, exist_ok=True)
        mapping = {}
        for ref in image_refs:
            token = ref["file_token"]
            name = ref["name"]
            save_path = os.path.join(out_dir, name)
            try:
                real_path, _ = self.download_image(token, save_path)
                mapping[name] = os.path.basename(real_path)
            except FeishuError:
                mapping[name] = name  # 下载失败保留原占位
        return mapping

    # ------------------------------------------------------------ 删除文档
    def delete_doc(self, doc_id):
        """删除文档（用于集成测试清理临时文档）"""
        resp = self._request(
            "DELETE", f"{API}/drive/v1/files/{doc_id}", params={"type": "docx"},
        )
        self._json(resp)

    # ------------------------------------------------------------ 目标路径定位
    def list_wiki_spaces(self):
        """列出知识库列表"""
        spaces, page = [], None
        while True:
            params = {"page_size": 50}
            if page:
                params["page_token"] = page
            data = self._json(self._request("GET", f"{API}/wiki/v2/spaces", params=params))
            spaces += data["data"].get("items", [])
            if not data["data"].get("has_more"):
                return spaces
            page = data["data"].get("page_token")

    def list_wiki_nodes(self, space_id, parent_node_token=None):
        """列出知识库某节点（None=顶层）的子节点"""
        nodes, page = [], None
        while True:
            params = {"page_size": 50}
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            if page:
                params["page_token"] = page
            data = self._json(self._request(
                "GET", f"{API}/wiki/v2/spaces/{space_id}/nodes", params=params,
            ))
            nodes += data["data"].get("items", [])
            if not data["data"].get("has_more"):
                return nodes
            page = data["data"].get("page_token")

    def list_folder_children(self, folder_token=""):
        """列出云空间文件夹内容（folder_token 空串 = 根目录）"""
        files, page = [], None
        while True:
            params = {"page_size": 200, "folder_token": folder_token}
            if page:
                params["page_token"] = page
            data = self._json(self._request("GET", f"{API}/drive/v1/files", params=params))
            files += data["data"].get("files", [])
            if not data["data"].get("has_more"):
                return files
            page = data["data"].get("page_token")

    def create_folder(self, name, parent_token=""):
        """创建云空间文件夹，返回 folder_token"""
        data = self._json(self._request(
            "POST", f"{API}/drive/v1/files/create_folder",
            json={"name": name, "folder_token": parent_token},
        ))
        return data["data"]["token"]

    def resolve_wiki_dest(self, segs):
        """路径第一段先按知识库名匹配；否则按各知识库顶层节点名匹配。

        返回 (node_token, space_id) 或 None（落回云空间分支）
        """
        try:
            spaces = self.list_wiki_spaces()
        except FeishuError as e:
            if "99991672" in str(e.code) or "Access denied" in str(e.msg):
                return None
            raise
        space = next((s for s in spaces if (s.get("name") or "").strip() == segs[0]), None)
        if space:
            return self._walk_wiki_nodes(space["space_id"], None, segs[1:]), space["space_id"]
        # 第一段不是知识库名 → 试各知识库的顶层节点
        for sp in spaces:
            try:
                nodes = self.list_wiki_nodes(sp["space_id"])
            except FeishuError:
                continue
            node = next((n for n in nodes if (n.get("title") or "").strip() == segs[0]), None)
            if node:
                return self._walk_wiki_nodes(sp["space_id"], node["node_token"], segs[1:]), sp["space_id"]
        return None

    def _walk_wiki_nodes(self, space_id, start_token, names):
        """从 start_token 起逐级匹配 names，返回最后的 node_token。缺失抛 FeishuError"""
        cur = start_token
        for name in names:
            nodes = self.list_wiki_nodes(space_id, cur)
            node = next((n for n in nodes if (n.get("title") or "").strip() == name), None)
            if not node:
                siblings = [n.get("title") for n in nodes][:20]
                raise FeishuError(
                    "WIKI_NODE_MISSING",
                    f"知识库节点缺失「{name}」，该层现有节点: {siblings}（知识库节点树不能自动创建）",
                )
            cur = node["node_token"]
        return cur

    def resolve_folder_dest(self, segs, auto_create=True):
        """云空间文件夹逐级匹配，缺失自动创建。返回目标 folder_token"""
        folder_token = ""
        for name in segs:
            files = self.list_folder_children(folder_token)
            sub = next((f for f in files if f.get("type") == "folder" and f.get("name") == name), None)
            if sub:
                folder_token = sub["token"]
            elif auto_create:
                folder_token = self.create_folder(name, folder_token)
            else:
                raise FeishuError("FOLDER_MISSING", f"文件夹缺失: {name}")
        return folder_token

    def create_doc_in_dest(self, title, dest):
        """按 --dest 路径创建文档。返回 doc_id（wiki 场景 = 节点挂载的 obj_token）"""
        if not dest:
            return self.create_doc(title)

        segs = [s.strip() for s in re.split(r"[>/]+", dest) if s.strip()]
        wiki = self.resolve_wiki_dest(segs)  # 先试知识库
        if wiki:
            node_token, space_id = wiki
            body = {"obj_type": "docx", "node_type": "origin", "title": title}
            if node_token:
                body["parent_node_token"] = node_token
            data = self._json(self._request(
                "POST", f"{API}/wiki/v2/spaces/{space_id}/nodes", json=body,
            ))
            return data["data"]["node"]["obj_token"]

        # 云空间文件夹分支：先创建再 move
        folder_token = self.resolve_folder_dest(segs)
        return self.create_doc(title, folder_token)

    # ------------------------------------------------------------ 树（供 UI）
    def list_folder_tree(self, folder_token="", depth=0, max_depth=5):
        """递归列云空间文件夹树（限制深度防大云盘卡死），返回 [{name, token, type, children}]"""
        if depth >= max_depth:
            return []
        nodes = []
        for f in self.list_folder_children(folder_token):
            if f.get("type") == "folder":
                node = {"name": f.get("name"), "token": f.get("token"), "type": "folder"}
                node["children"] = self.list_folder_tree(f.get("token"), depth + 1, max_depth)
                nodes.append(node)
        return nodes

    def list_wiki_tree(self):
        """知识库 → 节点树，返回 [{name, space_id, node_token, children}]"""
        trees = []
        for sp in self.list_wiki_spaces():
            node = {
                "name": (sp.get("name") or "").strip(),
                "space_id": sp.get("space_id"),
                "node_token": None,
            }
            node["children"] = self._wiki_node_tree(sp["space_id"], None)
            trees.append(node)
        return trees

    def _wiki_node_tree(self, space_id, parent_node_token, depth=0, max_depth=5):
        if depth >= max_depth:
            return []
        nodes = []
        for n in self.list_wiki_nodes(space_id, parent_node_token):
            # 只有能挂文档的节点才展开（doc/短链接节点跳过）
            if n.get("obj_type") in ("doc", "sheet"):
                continue
            item = {"name": (n.get("title") or "").strip(), "node_token": n.get("node_token")}
            if n.get("has_child"):
                item["children"] = self._wiki_node_tree(space_id, n.get("node_token"), depth + 1, max_depth)
            nodes.append(item)
        return nodes


# ---------------------------------------------------------------- 工具函数
def _guess_ext(ctype, name_or_url=""):
    """从 content-type 或文件名/URL 推断图片扩展名，兜底 jpg"""
    for ext in ("png", "jpeg", "jpg", "gif", "webp", "bmp"):
        if ext in ctype:
            return "jpg" if ext == "jpeg" else ext
    low = (name_or_url or "").lower().split("?")[0]
    m = re.search(r"\.(png|jpe?g|gif|webp|bmp)$", low)
    return m.group(1) if m else "jpg"
