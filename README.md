# md2feishu2md

Markdown ↔ 飞书云文档双向转换桌面应用（Windows）。

- **正向**：本地 `.md`（含图片、表格、行内格式、代码块、任务列表）→ 飞书云文档，支持批量、指定目标路径（知识库/云空间文件夹）、任务队列与失败重试
- **反向**：飞书云文档 → 本地 `.md`，图片自动下载到 `images/`
- **形态**：pywebview 桌面应用（Python 主进程 + 内嵌网页 UI），可打包为单 exe

## 截图

> 待补充

## 下载

从 [Releases](../../releases) 页面下载最新 `md2feishu2md.exe`（无需安装 Python，双击即用）。

## 快速开始（源码运行）

```bash
pip install -r requirements.txt
python app.py
```

## 使用说明

### 1. 配置凭据（设置 Tab）

1. 打开 [飞书开放平台](https://open.feishu.cn/) → 创建自建应用
2. 获取 **App ID** 与 **App Secret**，填入「设置」Tab
3. 点击「测试连接」确认凭据有效

### 2. 开启权限

应用需开启以下权限（在「权限管理」页）：

| 权限 | 用途 |
|---|---|
| `docx:document` | 创建/读取/删除文档与块 |
| `docx:document:write` | 写入文档块 |
| `drive:drive` | 上传/下载图片、操作云空间文件夹 |
| `wiki:wiki`（可选） | 转换到知识库 |

> 注意：修改权限后需在「版本管理与发布」页 **创建版本并发布**，权限才会对应用生效。

### 3. 正向转换

「转换」Tab 选择 `.md` 文件 → 填标题 → 选目标路径（根目录/知识库/文件夹）→ 开始转换 → 得到飞书文档链接。

「批量」Tab 可多选文件批量转换，任务并发执行，失败的条目可单独重试。

### 4. 反向转换

「反向」Tab 粘贴飞书文档链接（形如 `https://xxx.feishu.cn/docx/xxxx`）→ 选输出目录 → 转换下载，得到 `.md` 文件与 `images/` 目录。

## 支持的 Markdown 语法

标题（1~6 级）、粗体/斜体/行内代码/删除线/链接、无序/有序/任务列表、引用、代码块（带语言高亮）、分割线、表格、图片（本地/外链）。

## 开发

```bash
pip install -r requirements.txt pytest
pytest tests/ -q                  # 纯单测（无网络）
pytest tests/ -m integration -v   # 集成测试（需飞书凭据）
```

- 核心转换逻辑为纯函数（`md2feishu.py` / `feishu2md.py`），与网络/UI 解耦
- 飞书 HTTP 调用集中在 `feishu_api.py`，内置全局限流（QPS≤3）
- 文档级并发由 `task_queue.py` 调度（默认并发 3，可配置 1~5）

### 本地打包

```bash
pip install pyinstaller
python build.py        # 产出 dist/md2feishu2md.exe
```

### CI 自动发布

push 一个 `v*` tag（如 `v1.0.0`）会自动触发 GitHub Actions 打包并上传 Release：

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## 目录结构

```
md2feishu2md/
├── app.py              # pywebview 入口 + js_api（Python↔JS 桥）
├── feishu_api.py       # 飞书 OpenAPI 封装：token/限流/文档/图片/目标路径
├── md2feishu.py        # md → 飞书块（纯函数）
├── feishu2md.py        # 飞书块 → md（纯函数，反向）
├── config_store.py     # config.json 读写（凭据/并发/历史）
├── task_queue.py       # 文档级并发任务队列
├── build.py            # 本地 PyInstaller 打包
├── ui/                 # 前端（原生 HTML/CSS/JS）
├── tests/              # 单测 + 集成测试
└── .github/workflows/release.yml
```

## 安全说明

- App Secret 仅存本地 `config.json`，不写日志、不打印、不进历史
- 配置文件已在 `.gitignore` 中排除，不会上传到仓库

## License

[MIT](LICENSE)
