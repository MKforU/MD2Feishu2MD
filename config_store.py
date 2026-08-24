# -*- coding: utf-8 -*-
"""config.json 读写 —— 凭据/设置/历史的本地持久化。

- 原子写（tmp + os.replace），避免写一半损坏
- 损坏/缺失时重置为空配置，不崩溃
- app_secret 仅存本地文件，不写日志、不打印、不进任何返回值以外的结构

字段：app_id / app_secret / concurrency（文档并发数）/ default_out_dir / history（最近 N 条）
"""
import json
import os

HISTORY_LIMIT = 50  # 历史记录上限（最近 N 条转换）


class ConfigStore:
    def __init__(self, path="config.json"):
        self.path = path
        self._config = None

    # ------------------------------------------------------------ 读写
    def load(self):
        """加载配置。文件缺失/损坏/非 dict 时返回空配置，不崩溃。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        self._config = cfg
        return cfg

    def save(self):
        """原子保存配置到文件。"""
        cfg = self._config if isinstance(self._config, dict) else {}
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ 通用 get/set
    def get(self, key, default=None):
        cfg = self._config if isinstance(self._config, dict) else {}
        return cfg.get(key, default)

    def set(self, key, value):
        if not isinstance(self._config, dict):
            self._config = {}
        self._config[key] = value
        self.save()

    # ------------------------------------------------------------ 凭据
    def get_credentials(self):
        """返回 (app_id, app_secret)"""
        return self.get("app_id", ""), self.get("app_secret", "")

    def set_credentials(self, app_id, app_secret):
        self._config = self._config if isinstance(self._config, dict) else {}
        self._config["app_id"] = app_id
        self._config["app_secret"] = app_secret
        self.save()

    # ------------------------------------------------------------ 设置项
    def get_concurrency(self, default=3):
        """文档并发数（设计文档要求 1~5，越界回退默认值）"""
        v = self.get("concurrency", default)
        return v if isinstance(v, int) and 1 <= v <= 5 else default

    def set_concurrency(self, n):
        self.set("concurrency", n)

    def get_default_out_dir(self, default=""):
        return self.get("default_out_dir", default) or default

    def set_default_out_dir(self, d):
        self.set("default_out_dir", d)

    # ------------------------------------------------------------ 历史记录
    def get_history(self) -> list:
        h = self.get("history", [])
        return h if isinstance(h, list) else []

    def add_history(self, entry):
        """插入一条历史记录到最前，截断到 HISTORY_LIMIT 条"""
        if not isinstance(self._config, dict):
            self._config = {}
        history = list(self.get_history())
        history.insert(0, entry)
        self._config["history"] = history[:HISTORY_LIMIT]
        self.save()
