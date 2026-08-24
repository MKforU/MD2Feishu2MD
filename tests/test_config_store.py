# -*- coding: utf-8 -*-
"""config_store 单测：读写往返、损坏恢复、历史截断、app_secret 不泄漏"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_store import ConfigStore, HISTORY_LIMIT


class TestLoadSave:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "config.json"
        cs = ConfigStore(str(p))
        cs.set_credentials("app123", "secret456")
        cs.set("concurrency", 3)
        cs.set("default_out_dir", "D:/out")

        cs2 = ConfigStore(str(p))
        cs2.load()
        assert cs2.get_credentials() == ("app123", "secret456")
        assert cs2.get("concurrency") == 3
        assert cs2.get("default_out_dir") == "D:/out"

    def test_missing_file_resets(self, tmp_path):
        p = tmp_path / "nonexistent.json"
        cs = ConfigStore(str(p))
        cfg = cs.load()
        assert cfg == {}
        assert cs.get_credentials() == ("", "")

    def test_corrupted_json_resets(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("{ this is not valid json ", encoding="utf-8")
        cs = ConfigStore(str(p))
        cfg = cs.load()
        assert cfg == {}
        assert cs.get("app_id") is None

    def test_non_dict_json_resets(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        cs = ConfigStore(str(p))
        assert cs.load() == {}

    def test_save_creates_valid_json(self, tmp_path):
        p = tmp_path / "config.json"
        cs = ConfigStore(str(p))
        cs.load()
        cs.set("app_id", "x")
        # 文件应存在且为合法 JSON
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["app_id"] == "x"

    def test_load_then_save_keeps_other_fields(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"app_id": "a", "app_secret": "s", "concurrency": 5}))
        cs = ConfigStore(str(p))
        cs.load()
        cs.set("default_out_dir", "X")
        cs.load()  # 重新读回
        assert cs.get("app_id") == "a"
        assert cs.get("concurrency") == 5


class TestHistory:
    def test_history_roundtrip(self, tmp_path):
        p = tmp_path / "config.json"
        cs = ConfigStore(str(p))
        cs.load()
        cs.add_history({"title": "t1", "url": "u1"})
        cs.add_history({"title": "t2", "url": "u2"})
        # 最新的在最前
        assert cs.get_history()[0]["title"] == "t2"

    def test_history_truncated(self, tmp_path):
        p = tmp_path / "config.json"
        cs = ConfigStore(str(p))
        cs.load()
        for i in range(HISTORY_LIMIT + 10):
            cs.add_history({"title": f"t{i}"})
        assert len(cs.get_history()) == HISTORY_LIMIT
        assert cs.get_history()[0]["title"] == f"t{HISTORY_LIMIT + 9}"


class TestNoSecretLeak:
    def test_secret_not_in_repr(self, tmp_path):
        # app_secret 只进 config 文件，不进任何日志输出结构
        p = tmp_path / "config.json"
        cs = ConfigStore(str(p))
        cs.set_credentials("app", "super-secret-value")
        # 文件内容包含 secret（这是唯一的存储点）
        assert "super-secret-value" in p.read_text(encoding="utf-8")
        # 类本身不打印任何东西（无 print/logging 调用）
        assert not hasattr(ConfigStore, "__repr__") or "secret" not in repr(cs)
