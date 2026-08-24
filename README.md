# md2feishu2md

Markdown ↔ 飞书云文档双向转换桌面应用。

- **正向**：本地 `.md`（含图片、表格、行内格式、代码块、任务列表）→ 飞书云文档，支持批量、指定目标路径（知识库/云空间文件夹）、任务队列与失败重试
- **反向**：飞书云文档 → 本地 `.md`，图片自动下载到 `images/`
- **形态**：pywebview 桌面应用，可打包为单 exe

## 快速开始

```bash
pip install -r requirements.txt
python app.py
```

## 开发

```bash
pip install -r requirements.txt pytest
pytest tests/ -q
```

> 详细文档见 [docs/superpowers/specs/2026-08-24-md2feishu2md-design.md](docs/superpowers/specs/2026-08-24-md2feishu2md-design.md)
