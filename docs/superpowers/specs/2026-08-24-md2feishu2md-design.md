# md2feishu2md 设计文档

- 日期：2026-08-24
- 状态：待用户 review

## 1. 概述

`md2feishu2md` 是一个桌面应用，用于 Markdown 与飞书云文档之间的双向转换。

- **正向**：本地 `.md`（含本地图片、表格、行内格式、代码高亮、任务列表）→ 飞书云文档，支持批量、指定目标路径（知识库/云空间文件夹）、任务队列与失败重试。
- **反向**：飞书云文档 → 本地 `.md`（图片下载到本地 `images/`）。
- **形态**：pywebview 桌面应用（Python 主进程 + 内嵌网页 UI），可打包为单 exe。
- **发布**：源码开源到 GitHub，exe 通过 GitHub Release 发布；本地打包与 CI 自动发布并存。

## 2. 技术选型

- 桌面壳：`pywebview`（`webview.create_window` + `js_api` 桥接）
- 前端：原生 HTML/CSS/JS（无 node 构建链）
- HTTP：`httpx`
- 打包：`PyInstaller`（`--onefile --windowed`）
- 目标平台：Windows（单 exe）

## 3. 架构与模块职责

```
md2feishu2md/
├── app.py              # pywebview 入口 + js_api（Python↔JS 桥）
├── feishu_api.py       # 飞书 OpenAPI 封装：token、建文档(带folder)、上传图片、读块、列文件夹、下载图片
├── md2feishu.py        # md → 飞书块（纯函数）
├── feishu2md.py        # 飞书块 → md（纯函数，反向）
├── config_store.py     # config.json 读写（凭据/并发数/最近文件夹/历史记录）
├── task_queue.py       # 文档级并发任务队列（worker 池 + 限流退避）
├── build.py            # 本地 PyInstaller 打包脚本
├── ui/
│   ├── index.html      # 单页，4 个 tab
│   ├── style.css
│   └── app.js          # 调 window.pywebview.api.xxx
├── tests/
│   ├── test_md2feishu.py
│   ├── test_feishu2md.py
│   ├── test_config_store.py
│   └── test_feishu_api.py   # 集成测试（连飞书，临时文档测完删）
├── .gitignore
├── .github/workflows/release.yml
├── README.md
├── LICENSE
└── docs/superpowers/specs/...
```

模块职责与依赖：

| 模块 | 职责 | 依赖 |
|---|---|---|
| `feishu_api.py` | 所有飞书 HTTP 调用，返回结构化数据；内置全局令牌桶限流（QPS≤3） | httpx |
| `md2feishu.py` | 纯转换：md → 块列表（含行内格式、表格、todo、代码语言枚举） | 无 |
| `feishu2md.py` | 纯转换：块列表 → md 文本 | 无 |
| `config_store.py` | 凭据/历史/设置的本地 JSON 持久化 | 无 |
| `task_queue.py` | 并发调度、限流退避、重试 | feishu_api |
| `app.py` | js_api 串联各模块，跑转换任务 | 以上全部 |

关键：转换逻辑（md↔块）为纯函数，与网络/UI 解耦，可独立单测。

## 4. 数据流

### 正向：md → 飞书

1. 用户选 md（可多选/拖拽）
2. 读文件 → `md2feishu.parse()` → 块列表（图片占位）
3. 任务入队（worker 池，默认并发 3，可配置 1~5）
4. 每个任务：
   - `feishu_api.create_doc(title, folder_token)` → doc_id
   - `feishu_api.add_blocks(doc_id, 块, 每批≤40)`
   - 图片：创建空块 → `upload_all(parent_node=图片块ID)` → `batch_update replace_image`
5. 完成 → 返回文档链接 → 前端显示 + 复制/打开
6. 失败 → 记录错误，可单独重试；已创建的半成品文档自动清理

### 反向：飞书 → md

1. 粘贴文档链接，解析 doc_id
2. `feishu_api.read_blocks(doc_id)` 递归拉全量块（分页）
3. `feishu2md.render(块)` → md 文本；图片块 → `![name](images/xxx.png)` + 下载到本地
4. 写入输出目录（xxx.md + images/）
5. 前端显示保存路径 + 打开文件夹

### 并发策略

| 维度 | 策略 |
|---|---|
| 文档之间 | 并发 3（worker 池） |
| 文档内部块 | 串行（依赖 block_id） |
| 文档内部图片 | 并发 2~3（独立上传） |
| 全局限流 | 令牌桶 rate limiter（feishu_api 内置），所有调用统一限速 QPS≤3，可配置（留余量，默认 2.5） |
| 限流保护 | 飞书限流为 HTTP 400 + 错误码（非 429），识别后指数退避重试 3 次 |

### Markdown 语法支持（md2feishu 解析能力，借鉴已验证的 Claude Code skill 实现）

| 语法 | 飞书块 |
|---|---|
| `#`~`######` 标题 | heading1-9（block_type 3-11） |
| `**粗体**` `*斜体*` `` `行内代码` `` `~~删除线~~` `[链接](url)` | text_run 行内样式（text_element_style） |
| `-`/`*`/`+` 无序列表 | bullet（12） |
| `1.` 有序列表 | ordered（13） |
| `- [ ]`/`- [x]` 任务列表 | todo（17，带 done） |
| `>` 引用 | quote（15） |
| ` ```lang ` 代码块 | code（14，语言枚举映射 70+，不在表内不传 language） |
| `---` 分割线 | divider（22） |
| `\|a\|b\|` 表格 | table（31，两步创建，9×9 上限超限扩容，cells 行优先） |
| `![alt](src)` 图片 | image（27，三步流程） |

### 目标路径定位（--dest，双分支自动选择）

1. **知识库（Wiki）优先**：路径第一段匹配知识库名 → 逐级匹配节点 → 在目标节点下直接创建 docx（`POST /wiki/v2/spaces/{id}/nodes`，`obj_type=docx`、`node_type=origin`）。前提：`wiki:wiki` 权限 + 应用是该知识库成员；节点树缺失不自动创建，报错列出现有节点。
2. **云空间文件夹回退**：第一段不是知识库 → 按文件夹逐级匹配，缺失自动 `create_folder`，文档创建后 `move` 移入。
3. 不指定 → 文档创建在应用云空间根目录。

UI 的目标路径选择器需同时支持知识库（名称 → 节点树）和云空间文件夹树两种。

## 5. 界面布局

单窗口：左侧窄侧边栏 4 tab + 右侧内容区 + 底部状态栏（版本/凭据状态/任务数）。
- **Tab 1 · 转换**：选 md + 标题输入（默认文件名）+ 目标路径选择器（知识库/云空间文件夹双分支，默认根目录）+ 开始转换 → 进度条 → 结果卡片（链接/复制/打开）
- **Tab 2 · 批量**：拖拽区/多选 → 文件清单（状态图标：排队/进行/成功/失败 + 错误提示 + 单条重试）+ 全选/清空/开始全部/暂停
- **Tab 3 · 反向**：粘贴链接 → 解析预览标题 → 输出目录选择 → 转换下载 → 显示保存路径
- **Tab 4 · 设置**：App ID / App Secret（掩码）+ 并发数滑块 + 默认输出目录 + 「测试连接」按钮

## 6. 错误处理与边界

| 错误 | 处理 |
|---|---|
| 凭据无效（10014） | 设置页红字 + 引导重填；转换前「测试连接」拦截 |
| 权限不足（99991672/914xx） | 报缺 XX 权限 + 开启指引（含"发布新版本"提示） |
| 限流（HTTP 400 + 限流错误码） | 识别后指数退避重试 3 次，仍失败则标记任务失败 |
| 单篇失败 | 标记失败 + 保留错误 + 单独重试 + 半成品清理 |
| 网络中断 | 可重试 |

边界情况：图片缺失/外链失败 → 占位符跳过；空 md → 拦截；表格 → markdown 表格，未知块 → `[未知块]` 跳过；目标文件夹无权限 → 回退根目录；config.json 损坏 → 重置空配置不崩溃。

安全：appSecret 只存本地 config.json，不写日志、不打印、不进历史；下载图片校验文件类型。

## 7. 测试

- `md2feishu.py` / `feishu2md.py`：纯单测（无网络）
- `config_store.py`：单测（含损坏恢复）
- `feishu_api.py`：集成测试（连飞书，临时文档测完删）
- UI：手动端到端（用一篇带图 md 全链路验证）

## 8. 开源与发布（A+B 并存）

- **仓库结构**：`.gitignore`（排除 config.json/venv/build/dist/__pycache__）、`README.md`（用法 + 权限指引 + Release 下载链接）、`LICENSE`（MIT）
- **A. 本地打包**：`build.py`（PyInstaller）→ `dist/feishu2md.exe`
- **B. CI 自动发布**：`.github/workflows/release.yml`，push tag（如 v1.0.0）→ Actions 用同一套 PyInstaller 命令打包 → 上传 Release 附件

## 9. 交付物

1. 可运行源码（`python app.py`）
2. 本地打包脚本 + CI workflow（A+B）
3. GitHub 仓库（源码 + Release 下载 exe）
4. `README.md` + `LICENSE`
5. 本设计文档
