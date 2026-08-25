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
import os
import re
import sys
import time

import webview

from feishu_api import BATCH, FeishuClient, FeishuError
from md2feishu import item_to_child, parse_md
from feishu2md import render_document
from config_store import ConfigStore
from task_queue import TaskQueue

SECRET_MASKS = ("", "****", "********", "*" * 8)


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


def _extract_doc_id(url):
    """从飞书文档链接提取 doc_id（支持 /docx/{id} 形式）"""
    m = re.search(r"/docx/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


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


class Api:
    def __init__(self):
        self.config = ConfigStore(get_config_path())
        self.config.load()
        self.client = None
        self.queue = None
        self.window = None  # 供 evaluate_js 推送（如需）

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
    def test_connection(self):
        """测试凭据：尝试获取 tenant token"""
        try:
            self._get_client()._headers()
            return {"ok": True}
        except Exception as e:
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

            if img_slots:
                client.upload_images(doc_id, img_slots, md_dir)

            self.config.add_history({"title": title, "url": url, "time": int(time.time())})
            return {"ok": True, "url": url}
        except Exception as e:
            return self._err(e)

    def _convert_one(self, path, title, dest):
        """批量任务函数：成功返回 dict，失败抛异常（由 task_queue 捕获）"""
        r = self.convert_md(path, title, dest)
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "转换失败"))
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
        """飞书文档 → 本地 md（同步阻塞）。返回 {"ok", "path"} 或错误"""
        try:
            client = self._get_client()
            doc_id = _extract_doc_id(url or "")
            if not doc_id:
                return {"ok": False, "error": "无法从链接解析文档 ID（需包含 /docx/{id}）"}

            blocks = client.read_blocks(doc_id)
            md_text, image_refs = render_document(blocks)

            out_dir = out_dir or self.config.get_default_out_dir() or os.getcwd()
            os.makedirs(out_dir, exist_ok=True)
            if image_refs:
                mapping = client.download_images(image_refs, out_dir)
                for old_name, real_name in mapping.items():
                    if old_name != real_name:
                        md_text = md_text.replace(f"images/{old_name}", f"images/{real_name}")

            title = _extract_title(blocks) or doc_id
            safe_title = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or doc_id
            md_path = os.path.join(out_dir, f"{safe_title}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_text)

            self.config.add_history({"title": title, "path": md_path, "time": int(time.time())})
            return {"ok": True, "path": md_path}
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
        """返回当前窗口（优先 self.window，兜底全局首个窗口）"""
        if self.window is not None:
            return self.window
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
    api = Api()
    window = webview.create_window(
        "md2feishu2md", get_ui_path(), js_api=api, width=1100, height=760, min_size=(900, 600)
    )
    api.window = window  # 供后续 evaluate_js 推送（如需）
    webview.start()


if __name__ == "__main__":
    main()
