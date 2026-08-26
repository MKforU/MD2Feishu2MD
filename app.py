# -*- coding: utf-8 -*-
"""md2feishu2md 桌面应用入口 —— pywebview 窗口 + Python↔JS 桥（js_api）。

Api 类暴露给前端（window.pywebview.api.xxx）：
- test_connection()            测试凭据是否有效
- convert_md(path, title, dest)  正向：单篇 md → 飞书文档（同步）
- convert_batch(paths, dest)     批量：提交到任务队列（异步，返回 task_ids）
- get_tasks() / retry_task()     批量任务状态查询 / 重试
- convert_back(url, out_dir)     反向：飞书文档 → md（同步）
- list_dest_tree()               目标路径树（知识库 + 云空间文件夹）
- get_config() / set_config()    配置读写
- get_history()                  历史记录

转换逻辑（纯函数 md2feishu/feishu2md）与网络（feishu_api）、调度（task_queue）解耦。
"""
import logging
import os
import re
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler

import webview

from feishu_api import BATCH, FeishuClient, FeishuError
from md2feishu import item_to_child, parse_md
from feishu2md import render_document
from config_store import ConfigStore
from task_queue import TaskQueue

SECRET_MASKS = ("", "****", "********", "*" * 8)


def _setup_logging():
    """日志写到 exe 同目录 app.log（滚动 2MB×3）；目录只读时回退 %TEMP%，绝不因日志失败而崩"""
    fmt = "%(asctime)s [%(threadName)s] %(levelname)s %(message)s"
    candidates = (
        os.path.join(os.path.dirname(get_config_path()), "app.log"),
        os.path.join(tempfile.gettempdir(), "md2feishu2md-app.log"),
    )
    for path in candidates:
        try:
            handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        except OSError:
            continue
        logging.basicConfig(level=logging.DEBUG, format=fmt, handlers=[handler])
        break
    logging.getLogger("md2feishu2md").info("应用启动")


def get_config_path():
    """config.json 路径：打包后放 exe 同目录，开发时放项目目录"""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "config.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def get_ui_path():
    """ui/index.html 路径：打包后从 PyInstaller 解压目录读，开发时从项目目录读"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", ""), "ui", "index.html")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")


def _extract_doc_ref(url):
    """从飞书链接提取文档引用。返回 ("docx", id) / ("wiki", node_token) / None"""
    if not url:
        return None
    m = re.search(r"/docx/([A-Za-z0-9]+)", url)
    if m:
        return ("docx", m.group(1))
    m = re.search(r"/wiki/([A-Za-z0-9]+)", url)
    if m:
        return ("wiki", m.group(1))
    return None


def _extract_title(blocks):
    """从块列表提取第一个标题文本作为文档标题"""
    for b in blocks:
        for level in range(1, 10):
            key = f"heading{level}"
            if b.get(key):
                els = b[key].get("elements") or []
                text = "".join((e.get("text_run") or {}).get("content", "") for e in els)
                if text.strip():
                    return text.strip()
    return None


_window_ref = None  # 模块级窗口引用（供文件对话框等）。绝不能挂在 Api 实例上：pywebview 会递归遍历 js_api 属性，window.native(.NET 控件)会无限递归导致卡死


class Api:
    def __init__(self):
        self.config = ConfigStore(get_config_path())
        self.config.load()
        self.client = None
        self.queue = None

    # ------------------------------------------------------------ 内部
    def _get_client(self):
        if self.client is None:
            app_id, app_secret = self.config.get_credentials()
            if not app_id or not app_secret:
                app_id = os.environ.get("FEISHU_APP_ID", "").strip()
                app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
            if not app_id or not app_secret:
                raise FeishuError("NO_CREDENTIALS", "请先在设置页配置 App ID / App Secret")
            self.client = FeishuClient(app_id, app_secret)
        return self.client

    def _get_queue(self):
        if self.queue is None:
            self.queue = TaskQueue(max_workers=self.config.get_concurrency())
        return self.queue

    @staticmethod
    def _err(e):
        if isinstance(e, FeishuError):
            return {"ok": False, "error": f"{e.code}: {e.msg}"}
        return {"ok": False, "error": repr(e)}

    # ------------------------------------------------------------ 连接
    def test_connection(self, app_id=None, app_secret=None):
        """测试凭据：优先用传入值（不落盘不缓存）；未传则用已保存配置。返回 {"ok"}"""
        log = logging.getLogger("md2feishu2md")
        log.info("test_connection 开始（%s）", "传入凭据" if app_id else "已保存凭据")
        try:
            if app_id:
                # 临时 client，只验证凭据有效性，绝不写入 config 或缓存
                FeishuClient(app_id.strip(), (app_secret or "").strip())._headers()
            else:
                client = self._get_client()
                log.info("_get_client 完成")
                client._headers()
                log.info("_headers 完成，token OK")
            return {"ok": True}
        except Exception as e:
            log.error("test_connection 异常: %r", e)
            return self._err(e)

    # ------------------------------------------------------------ 正向转换
    def convert_md(self, path, title, dest):
        """单篇 md → 飞书文档（同步阻塞）。返回 {"ok", "url"} 或 {"ok": False, "error"}"""
        try:
            if not os.path.isfile(path):
                return {"ok": False, "error": f"文件不存在: {path}"}
            items = parse_md(path)
            if not items:
                return {"ok": False, "error": "md 文件为空"}

            client = self._get_client()
            md_dir = os.path.dirname(os.path.abspath(path))
            title = (title or "").strip() or os.path.splitext(os.path.basename(path))[0]

            doc_id = client.create_doc_in_dest(title, dest)
            url = f"https://feishu.cn/docx/{doc_id}"

            img_slots, pending = [], []

            def flush():
                nonlocal pending
                if not pending:
                    return
                children = [item_to_child(it) for it in pending]
                created = client.add_children(doc_id, children)
                for it, blk in zip(pending, created):
                    if it["kind"] == "image":
                        img_slots.append((it["src"], blk["block_id"]))
                pending = []

            for it in items:
                if it["kind"] == "table":  # 表格单独两步创建，且先 flush 保持顺序
                    flush()
                    client.create_table(doc_id, it)
                else:
                    pending.append(it)
                    if len(pending) >= BATCH:
                        flush()
            flush()

            img_summary = None
            if img_slots:
                img_summary = client.upload_images(doc_id, img_slots, md_dir)

            self.config.add_history({"title": title, "url": url, "time": int(time.time())})
            result = {"ok": True, "url": url}
            if img_summary and img_summary["failures"]:
                names = "、".join(f["src"] for f in img_summary["failures"][:5])
                more = len(img_summary["failures"]) - 5
                suffix = f" 等 {more} 张" if more > 0 else ""
                result["warning"] = (
                    f"图片 {img_summary['ok']}/{img_summary['total']} 张上传成功，"
                    f"失败: {names}{suffix}（文档中会留空图占位）"
                )
            return result
        except Exception as e:
            return self._err(e)

    def _convert_one(self, path, title, dest):
        """批量任务函数：成功返回 dict，失败抛异常（由 task_queue 捕获）"""
        r = self.convert_md(path, title, dest)
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "转换失败"))
        # 图片部分失败不判任务失败，但要把 warning 带给前端
        if r.get("warning"):
            r["error"] = r["warning"]  # task_queue 的 error 字段用于展示，成功态也显示
        return r

    def convert_batch(self, paths, dest):
        """批量转换：提交到任务队列，返回 task_ids（前端轮询 get_tasks 获取状态）"""
        try:
            q = self._get_queue()
            task_ids = []
            for p in paths:
                title = os.path.splitext(os.path.basename(p))[0]
                task_ids.append(q.submit(self._convert_one, p, title, dest))
            return {"ok": True, "task_ids": task_ids}
        except Exception as e:
            return self._err(e)

    def get_tasks(self):
        """批量任务状态列表"""
        if self.queue is None:
            return []
        return self.queue.to_dict()

    def retry_task(self, task_id):
        """重试失败任务"""
        if self.queue is None:
            return {"ok": False, "error": "无任务队列"}
        return {"ok": self.queue.retry(task_id)}

    # ------------------------------------------------------------ 反向转换
    def convert_back(self, url, out_dir):
        """飞书文档 → 本地 md（同步阻塞）。支持 /docx/{id} 与 /wiki/{node_token} 链接"""
        try:
            client = self._get_client()
            ref = _extract_doc_ref(url or "")
            if not ref:
                return {"ok": False, "error": "无法从链接解析文档 ID（需为 /docx/{id} 或 /wiki/{token} 链接）"}

            kind, token = ref
            if kind == "wiki":
                node = client.get_wiki_node(token)
                obj_token, obj_type = node.get("obj_token"), node.get("obj_type")
                if obj_type and obj_type != "docx":
                    return {"ok": False, "error": f"该知识库节点不是文档（obj_type={obj_type}），暂不支持"}
                if not obj_token:
                    return {"ok": False, "error": "知识库节点解析失败：未返回 obj_token"}
                doc_id = obj_token
            else:
                doc_id = token

            blocks = client.read_blocks(doc_id)
            md_text, image_refs = render_document(blocks)

            out_dir = out_dir or self.config.get_default_out_dir() or os.getcwd()
            os.makedirs(out_dir, exist_ok=True)
            img_failures = []
            if image_refs:
                mapping, img_failures = client.download_images(image_refs, out_dir)
                for old_name, real_name in mapping.items():
                    if old_name != real_name:
                        md_text = md_text.replace(f"images/{old_name}", f"images/{real_name}")

            title = _extract_title(blocks) or doc_id
            safe_title = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or doc_id
            md_path = os.path.join(out_dir, f"{safe_title}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_text)

            self.config.add_history({"title": title, "path": md_path, "time": int(time.time())})
            result = {"ok": True, "path": md_path}
            if img_failures:
                names = "、".join(f["name"] for f in img_failures[:5])
                more = len(img_failures) - 5
                suffix = f" 等 {more} 张" if more > 0 else ""
                result["warning"] = f"图片下载失败 {len(img_failures)} 张: {names}{suffix}（md 中保留占位引用）"
            return result
        except Exception as e:
            return self._err(e)

    # ------------------------------------------------------------ 目标路径
    def list_dest_tree(self):
        """目标路径第一层：知识库列表 + 根目录文件夹（不递归节点树，避免大量请求卡顿）。

        深层路径（知识库>节点、子文件夹）走「手动输入」文本框，由 create_doc_in_dest 解析。
        """
        try:
            client = self._get_client()
            wiki = []
            try:
                wiki = [
                    {"name": (s.get("name") or "").strip(), "space_id": s.get("space_id")}
                    for s in client.list_wiki_spaces()
                ]
            except FeishuError:
                pass  # 无知识库权限时忽略
            folders = [
                {"name": f.get("name"), "token": f.get("token")}
                for f in client.list_folder_children("")
                if f.get("type") == "folder"
            ]
            return {"ok": True, "wiki": wiki, "folders": folders}
        except Exception as e:
            return self._err(e)

    # ------------------------------------------------------------ 配置/历史
    def get_config(self):
        app_id, app_secret = self.config.get_credentials()
        return {
            "app_id": app_id,
            "app_secret": "****" if app_secret else "",  # 掩码，不返回明文
            "concurrency": self.config.get_concurrency(),
            "default_out_dir": self.config.get_default_out_dir(),
        }

    def set_config(self, app_id=None, app_secret="", concurrency=None, default_out_dir=None):
        """保存配置。app_id=None 表示不改凭据；app_id="" 表示清空凭据；
        app_secret 为掩码或空时保留原 secret（除非 app_id 变了）"""
        try:
            if app_id is not None:
                if app_id:
                    cur_id, cur_secret = self.config.get_credentials()
                    if not app_secret or app_secret in SECRET_MASKS:
                        app_secret = cur_secret if cur_id == app_id else ""
                    self.config.set_credentials(app_id, app_secret)
                else:
                    self.config.set_credentials("", "")  # 清空凭据
                self.client = None  # 凭据变化，重置 client 重新获取 token
            if concurrency is not None:
                self.config.set_concurrency(concurrency)
                if self.queue is not None:
                    self.queue.shutdown()  # 旧任务跑完，重建队列应用新并发数
                self.queue = None
            if default_out_dir is not None:
                self.config.set_default_out_dir(default_out_dir)
            return {"ok": True}
        except Exception as e:
            return self._err(e)

    def get_history(self):
        return self.config.get_history()

    # ------------------------------------------------------------ 文件对话框
    def _win(self):
        """返回当前窗口（优先模块级引用，兜底全局首个窗口）"""
        if _window_ref is not None:
            return _window_ref
        return webview.windows[0] if webview.windows else None

    def open_file_dialog(self):
        """选择单个 md 文件，返回路径或 None"""
        win = self._win()
        if win is None:
            return None
        result = win.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("Markdown (*.md)", "All files (*.*)"),
        )
        return (result or [None])[0]

    def open_files_dialog(self):
        """多选 md 文件，返回路径列表"""
        win = self._win()
        if win is None:
            return []
        result = win.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("Markdown (*.md)", "All files (*.*)"),
        )
        return result or []

    def open_folder_dialog(self):
        """选择目录，返回路径或 None"""
        win = self._win()
        if win is None:
            return None
        result = win.create_file_dialog(webview.FOLDER_DIALOG)
        return (result or [None])[0]


def main():
    _setup_logging()
    api = Api()
    window = webview.create_window(
        "md2feishu2md", get_ui_path(), js_api=api, width=1100, height=760, min_size=(900, 600)
    )
    global _window_ref
    _window_ref = window  # 模块级引用，供文件对话框取窗口；勿赋给 api（见 _window_ref 注释）
    webview.start()


if __name__ == "__main__":
    main()
