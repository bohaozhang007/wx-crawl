# wx-crawl

[English](README.md) | [简体中文](README-CN.md)

`wx-crawl` 用于在本地归档微信公众号的历史文章。项目以 [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools) 作为主要工具，负责识别公众号、获取历史列表、完成认证并下载文章；当主要工具得到的正文缺失或不完整时，再使用 [we-mp-rss](https://github.com/rachelos/we-mp-rss) 回退抓取。感谢这两个项目提供的核心能力。

## 目录

- [1. 项目作用](#1-项目作用)
- [2. 项目结构](#2-项目结构)
- [3. 快速开始](#3-快速开始)
- [4. 爬取结果](#4-爬取结果)
- [5. 筛选结果 SQLite](#5-筛选结果-sqlite)
- [6. 使用 Codex 安装](#6-使用-codex-安装)
- [7. 爬取流程与 Git 同步机制](#7-爬取流程与-git-同步机制)
- [8. 常见问题](#8-常见问题)
- [致谢](#致谢)

## 1. 项目作用

只需提供一个或多个微信公众号文章链接，`wx-crawl` 就会识别这些文章所属的公众号、读取公众号历史列表，并把文章正文下载到本地。用户直接提供的文章链接一定会单独处理，不受所选历史范围限制。

一次完整运行大致分为以下步骤：

1. 扫描输入 CSV，提取其中的微信公众号文章链接。
2. 根据链接识别公众号，并把新公众号合并到 `account_sources.csv`。
3. 重新核对登记表中所有公众号的 ID 和名称。
4. 根据配置的爬取模式生成历史文章候选列表。
5. 按 URL 跳过本地已经存在的文章。
6. 先用 `wechat-mp-tools` 下载候选文章。
7. 如果主要工具的结果未通过内容校验，再用 `we-mp-rss` 回退抓取。
8. 保存成功获取的正文、HTML、元数据和图片，并更新本次运行记录及全局汇总。

### 内容范围与成功标准

本项目重点保存文章文本和图片，不保证能够下载视频、音频、小程序内容、交互组件及其他嵌入资源。

一篇文章被判定为成功，需要同时具备标题和可用 HTML，并满足以下任一条件：

- 去除空白后，正文至少包含 50 个字符；
- 至少包含一张有效图片。

如果页面命中了已知的微信异常页面特征，即使满足上述数量条件也不会视为成功。这个标准有意保持宽松，避免漏掉有价值的短文章或以图片为主的文章。如果两个工具都无法得到合格结果，程序会跳过该文章，并把失败原因写入工具日志。

## 2. 项目结构

```text
wx-crawl/
├── src/
│   ├── auth/                    认证存储与登录适配
│   ├── crawler/                 爬取编排、内容校验与结果统计
│   ├── integrations/            第三方工具的运行时适配
│   ├── labeling/                模型 API 打标、判断规则与标签校验
│   └── crawl.py                 命令行入口
├── install/
│   ├── requirements.txt         wx-crawl 直接使用的依赖
│   └── third_party_versions.yaml
│                                第三方仓库地址与固定提交版本
├── third_party/                 第三方源码及各自的虚拟环境
├── config.yaml                  用户可修改的爬取配置
├── input_template.csv           默认输入 CSV
├── account_sources.csv          持久化的公众号登记表
├── results/                     已下载文章与每次运行记录
└── .python-version              推荐使用的 Python 版本
```

### 输入文件和公众号登记表分别负责什么

- `input_template.csv`，或者 `config.yaml` 中指定的其他 CSV，用于给本次运行提供新线索。程序会根据其中的文章链接发现新公众号，也会刷新已有公众号的信息。
- `account_sources.csv` 是长期保留的公众号登记表。每行包含稳定的本地编号、上游公众号 ID、当前公众号名称和一篇示例文章链接。
- 登记表中的示例文章也属于明确指定的文章；如果本地还没有保存，程序会主动下载。

**输入 CSV 只负责发现和更新公众号，并不限定本次只爬取其中出现的公众号。程序合并完新输入后，会继续处理 `account_sources.csv` 中登记的全部公众号。开始大规模爬取前，建议先检查该文件。**

### 本地数据和敏感信息

以下目录不会提交到 Git：

- `third_party/`：第三方源码克隆以及体积较大的虚拟环境；
- `results/`：本地文章档案，长期运行后可能占用数 GB 甚至更多空间；
- `src/auth/config/`：可复用的认证凭据，禁止提交或分享。

`account_sources.csv` 会随 Git 仓库保存，但文章结果和认证状态不会。因此，新机器拉取仓库后可以获得相同的公众号信息源清单，但需要在本机重新建立认证和结果数据。

## 3. 快速开始

第一次运行前，请先按照[第 5 节](#5-使用-codex-安装)完成安装。

### 3.1 准备输入

最简单的方式是替换 `input_template.csv` 中的示例链接，或者继续在文件中添加微信公众号文章链接。每个单元格放一个链接即可。

也可以使用任意其他 CSV，并在 `config.yaml` 中把 `crawl.input_csv` 改为该文件的路径。相对路径以仓库顶层目录为基准。CSV 的表头、列数和布局都没有要求，**只要求每个微信文章链接单独出现在一个单元格中**。

程序只把形如 `https://mp.weixin.qq.com/s/...` 的微信文章链接用作公众号发现入口。重复 URL 会先做规范化处理，同一个链接只处理一次。

如果输入 CSV 中没有识别到链接，但 `account_sources.csv` 已经登记了公众号，程序会给出警告，然后继续爬取登记表中的已有公众号。只有当输入和登记表都为空时，任务才会报错停止。

### 3.2 配置爬取方式

```yaml
crawl:
  input_csv: input_template.csv
  mode: incremental
  articles_per_account: 30
  incremental_max_days: 1
```

| 配置项 | 说明 |
| --- | --- |
| `input_csv` | 输入 CSV 的路径；不是绝对路径时，相对于仓库顶层目录 |
| `mode` | 可选 `incremental` 或 `window` |
| `articles_per_account` | 必须大于 0；用于 `window` 模式，以及 `incremental` 模式下首次发现的公众号 |
| `incremental_max_days` | 增量模式最多回溯的天数，默认 1；必须大于 0，`window` 模式忽略 |

两种模式的行为如下：

- `incremental`（增量模式）：程序最多回溯 `incremental_max_days` 天，并从最新文章开始向前翻阅历史列表；遇到第一个本地已存在的 URL，或文章早于回溯时间边界时停止。边界内且本地不存在的文章作为本次下载候选。已有公众号不受 `articles_per_account` 限制。对于首次发现、尚无本地断点的公众号，仍只处理最新的 `articles_per_account` 条历史记录，但不会超过最大回溯天数。
- `window`（窗口模式）：每个公众号都只把最新的 `articles_per_account` 条历史记录作为本次下载范围。

无论使用哪种模式，本地已经保存的 URL 都会跳过。输入 CSV 中直接提供的文章链接，以及 `account_sources.csv` 中保存的示例文章链接，不受历史窗口大小限制。

`articles_per_account` 表示纳入处理范围的历史记录数量，并不保证最终一定成功保存同样数量的文章。如果其中某篇文章通过两个工具都无法获取，本次实际新增数可能小于该值。

### 3.3 运行前检查

```bash
python3 src/crawl.py --check
```

`--check` 只做本地预检，主要检查：

- 两个 Python 虚拟环境和必要文件是否存在；
- `config.yaml` 的配置是否合法；
- 输入文件是否存在、可读且扩展名为 `.csv`；
- 公众号登记表能否读取；
- 认证数据目录和运行时链接是否准备妥当；
- 输入文件中识别到多少文章链接，登记表中已有多少公众号。

它不会启动正式爬取，不会请求登录二维码，也不会通过线上服务验证当前凭据，更不能证明此刻一定可以真实下载文章。

### 3.4 开始爬取

```bash
python3 src/crawl.py
```

如果需要更详细的终端输出和文件日志，可以加上 `--verbose`：

```bash
python3 src/crawl.py --verbose
```

任务运行时间较长时，可以按一次 `Ctrl+C` 安全中止。已经完成的文章会保留，程序会写入“已中断”的运行记录、清理临时文件，并关闭由本次任务启动的工具服务。

### 3.5 通过模型 API 为已下载文章打标

这个命令只负责打标，不会启动爬取，也不会执行筛选、生成摘要或写入 SQLite。

默认情况下，打标程序会从 `~/.hermes/config.yaml` 和 `~/.hermes/.env` 继承
Hermes Agent 当前使用的 provider、模型、API 地址和对应 API Key。因此，通过
钉钉/Hermes 启动打标时，会直接复用 Agent 的模型账号，无须在本仓库中重复配置
密钥。保持以下覆盖项为空即可使用默认行为：

```yaml
labeling:
  provider: ""
  model: ""
  base_url: ""
  concurrency: 2
  timeout_seconds: 180
  max_retries: 2
```

如果确实希望打标使用不同于 Hermes Agent 的模型，可以填写上述 YAML 配置，
或者使用 `LABEL_PROVIDER`、`LABEL_MODEL`、`LABEL_BASE_URL` 和
`LABEL_API_KEY` 临时覆盖。并发数、超时时间和重试次数也可以分别通过
`LABEL_CONCURRENCY`、`LABEL_TIMEOUT_SECONDS` 和 `LABEL_MAX_RETRIES`
覆盖。API Key 不得写入受 Git 跟踪的 YAML。继承 provider 时，程序会自动查找
对应的密钥变量，例如 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`。

先检查配置和决策树，不发送任何 API 请求：

```bash
.venv/bin/python -m src.labeling.cli --check
```

建议先从指定爬取批次中选一篇待处理文章测试：

```bash
.venv/bin/python -m src.labeling.cli \
  --run-dir results/record/<timestamp> --limit 1
```

确认无误后，处理该批次中全部缺少标签或标签无效的文章：

```bash
.venv/bin/python -m src.labeling.cli \
  --run-dir results/record/<timestamp>
```

程序会把每篇文章的完整 `content.txt`、元数据、带版本的决策树和研究方向配置
发送给指定模型。收到结构化结果后，程序还会在本地校验标签和原文证据，全部通过
才会原子写入 `label.json`。已经具有有效 v2 标签的文章会自动跳过；无效或旧版标签
只有在新结果通过校验后才会被替换。如果确实需要重新判断已经具有有效 v2 标签的
文章，再使用 `--replace`。

### 注意事项

- 第一次需要认证时，终端会打印二维码 PNG 的准确路径，该文件位于本次运行目录的 `tools-log` 中。请在五分钟内用微信扫码并确认登录。认证完成后二维码会被删除，凭据则保存在本地，供后续运行复用。
- 历史列表工具只需认证一次，不需要为每个公众号分别扫码。
- 凭据过期或失效后，后续运行可能会再次要求扫码。
- 新发现的公众号会写入 `account_sources.csv`。每次运行还会重新核对所有已登记公众号的名称；如果本次解析出的 ID 与登记 ID 不一致，程序会保留原 ID 并记录警告，不会静默改变公众号身份。
- 文章按规范化后的 URL 去重。
- 公众号和文章均按顺序处理，请求之间还会加入短暂的随机间隔。公众号较多时，一次完整运行可能需要较长时间。
- 同一个仓库同一时间只能运行一个爬虫实例。
- 如果 `wechat-mp-tools` 服务由本次任务启动，任务结束或中断时会自动关闭；如果运行前已经存在兼容服务，程序会复用它，并在任务结束后保留该服务。

## 4. 爬取结果

```text
results/
├── summary.csv
├── articles/
│   └── <编号>_<公众号ID>_<公众号名称>/
│       └── <YYYY_MM_DD_HH_MM_SS>_<文章标题>/
│           ├── content.txt
│           ├── metadata.json
│           ├── *.html
│           └── media/
└── record/
    └── <YYYY_MM_DD_HH_MM_SS>/
        ├── account_summary.csv
        ├── article_details.csv
        └── tools-log/
```

目录中的时间使用 Asia/Shanghai 时区，格式统一为 `YYYY_MM_DD_HH_MM_SS`。公众号名称和文章标题会做文件名安全处理，过长时会自动截短。

只有成功下载的文章才会进入 `results/articles/` 和结果 CSV。失败文章不会单独列在结果 CSV 中，相关原因只保留在 `tools-log/`。

### 4.1 `results/summary.csv`

该文件根据本地文章档案重新生成。每个已登记公众号占一行，即使该公众号目前还没有成功保存的文章，也会出现在汇总中。

| 字段 | 说明 |
| --- | --- |
| `编号` | 稳定的本地公众号编号 |
| `公众号唯一ID` | 上游服务返回的公众号唯一 ID |
| `公众号名称` | 当前核对后的公众号名称 |
| `文章总数` | 本地已保存的文章总数 |
| `文章总存储（M）` | 文章占用空间，单位为 MiB |
| `最新文章时间` | 本地最新一篇文章的发布时间 |
| `最新文章标题` | 本地最新一篇文章的标题 |

### 4.2 每次运行的记录

每次正式启动爬取后，程序都会在 `results/record/` 下创建一个以启动时间命名的目录。即使任务失败或被中断，也会尽量写入本次记录。

- `account_summary.csv`：每个公众号本次新增的文章数、爬取耗时（分钟）和新增存储空间（MiB）；最后一行是本次任务的总计。
- `article_details.csv`：本次新增文章的公众号名称、文章标题和发布时间。
- `tools-log/crawler.log`：爬虫的详细决策、警告、重试和错误。
- `tools-log/wechat-mp-tools.log`：主要工具服务的运行输出。
- `tools-log/we-mp-rss.log`：启用回退抓取时产生的输出。
- `tools-log/wechat-login-qr.png`：临时登录二维码，会在认证完成或任务清理时删除。

### 4.3 单篇文章目录

最终文件会因实际成功的下载工具而略有不同：

- `content.txt`：清洗后的文章正文；
- `metadata.json` 或 `data.json`：文章标题、原始 URL、发布时间和资源信息；
- `*.html`：保存的文章 HTML，部分结果还会包含原始 HTML；
- `media/`：可用时保存封面和正文图片；
- `fallback.html` 和 `fallback_metadata.json`：采用 `we-mp-rss` 回退结果时生成。

## 5. 筛选结果 SQLite

组合筛选 skill 会把同时命中申请类型和技术领域的文章写入
`results/articles.sqlite3`。数据库保存标题、公众号、发布时间、原文链接、
两层标签、Agent 摘要和正文文本；未入选文章仍会先保留，确认数据库导入成功
后才可以清理。

初始化数据库：

```bash
./wx-crawl-db init
```

导入某次运行生成的 `filtered_articles.json`（不删除文章目录）：

```bash
./wx-crawl-db ingest --run-dir results/record/<启动时间>
```

预览并确认删除该次运行中未入选的文章：

```bash
./wx-crawl-db prune --run-dir results/record/<启动时间>
./wx-crawl-db prune --run-dir results/record/<启动时间> --confirm-delete
# 清理历史上所有未入库文章（需显式确认）
./wx-crawl-db prune --all-unselected --confirm-delete
```

查询和钉钉接入可使用 JSON 输出：

```bash
./wx-crawl-db list --domain 具身智能 --json
./wx-crawl-db pending-delivery --channel dingtalk --json
./wx-crawl-db mark-delivered --channel dingtalk --id 123
```

CLI 只负责数据库查询和推送状态记录，不直接耦合钉钉 Webhook。钉钉发送成功
后调用 `mark-delivered`，即可避免重复推送。

不要只依赖目录名判断文章是否相同。程序以元数据中的 URL 作为去重依据。

## 6. 使用 Codex 安装

### 环境要求

- Linux 环境。当前代码使用 Linux 风格的进程组、文件锁和虚拟环境路径。
- Python 3.12，与 `.python-version` 保持一致。
- 能够访问 GitHub、Python 包索引、微信公众号文章页面和上游认证服务。
- 第一次扫码认证时需要使用微信手机客户端。
- 磁盘空间应能容纳两个虚拟环境、Playwright Chromium 和后续增长的文章档案。

推荐直接让 Codex 完成安装和验证。在仓库顶层向 Codex 提交以下任务即可：

```text
请在 Linux 环境中安装并配置当前 wx-crawl 仓库。

使用 .python-version 指定的 Python 3.12。读取
install/third_party_versions.yaml，把其中两个仓库克隆到 third_party/，
目录名和提交版本必须与文件记录一致，不要修改第三方仓库中受 Git 跟踪的源码。

分别在两个第三方仓库中创建独立的 .venv。把 wechat-mp-tools 自身依赖和
install/requirements.txt 安装到 wechat-mp-tools 的环境中；把 we-mp-rss 自身依赖
安装到它的环境中，然后安装与其 Playwright 版本匹配的 Chromium，以及 Chromium
在当前 Linux 系统中需要的动态库。

最后运行 `python3 src/crawl.py --check`。确认两个第三方 Git 工作区都没有
tracked source 修改，两个环境都能通过 `python -m pip check`，Chromium 可以
无头启动，认证目录已被 Git 忽略，并确保没有工具服务残留运行。
```

安装完成后的目录应为：

```text
third_party/
├── wechat-mp-tools/
│   └── .venv/
└── we-mp-rss/
    └── .venv/
```

项目固定使用 Python 3.12：当前锁定版本的 `wechat-mp-tools` 依赖 `mitmproxy` 12，本项目也已经在 Python 3.12 环境中完整验证。

项目固定第三方仓库版本，是因为 `wx-crawl` 会通过本地适配层调用其中部分 Python 模块和内部类。更新任一上游仓库后，可能需要同时调整并重新测试 `src/auth/` 或 `src/integrations/`。第三方 Git 工作区应保持无修改，以便在其他机器上准确复现相同环境。

## 7. 爬取流程与 Git 同步机制

项目把“可以随仓库同步的公众号信息源”和“只保存在当前机器上的爬取状态”分开管理。一次正常运行分为以下三个阶段。

### 7.1 根据输入 CSV 更新公众号登记表

1. 读取配置指定的 CSV，扫描其中每一个单元格，提取可识别的微信文章链接。
2. 对 URL 做规范化和去重，避免同一个输入链接重复请求公众号解析。
3. 根据每个有效文章链接获取上游公众号 ID 和当前公众号名称。
4. 按公众号 ID 将解析结果合并到 `account_sources.csv`：
   - 如果 ID 尚未登记，使用下一个本地编号新增一行，保存编号、公众号 ID、当前名称和本次输入文章链接；
   - 如果 ID 已经存在，保留原来的本地编号，必要时更新公众号名称，并把示例文章链接更新为本次输入链接；
   - 同一公众号的多个输入链接只会生成一条登记记录，但这些不同的输入文章仍会作为本次明确下载的候选项。
5. 使用每条登记记录中的示例文章重新核对已有公众号。名称发生变化时会更新；如果解析出的公众号 ID 与登记 ID 不一致，程序会保留登记 ID 并写入警告，避免静默改变公众号身份。
6. 在开始下载文章前，先重写 `account_sources.csv`，并重新生成 `results/summary.csv`。

因此，输入 CSV 没有识别到链接，只代表“本次没有新增公众号”；只要登记表非空，程序仍会继续爬取已有公众号。

### 7.2 按完整登记表执行爬取

登记表更新完成后，程序会按照稳定的本地编号顺序，依次处理 `account_sources.csv` 中的每个公众号：

1. 读取该公众号本地已有文章的元数据，建立 URL 去重集合。
2. 按照 `incremental` 或 `window` 模式获取历史候选文章。
3. 额外加入尚未下载的登记表示例文章，以及本次输入 CSV 中直接指定的文章；这些文章不受历史窗口限制。
4. 先尝试主要下载工具，校验结果；只有主要结果不可用时才启动回退抓取。
5. 将每篇成功文章从临时工作目录移动到对应公众号的正式目录。
6. 清理临时候选数据，继续处理下一篇文章和下一个公众号，最后更新运行记录与全局汇总。

先更新登记表、再遍历完整登记表，可以确保本次刚发现的公众号立即进入同一轮完整爬取。

### 7.3 Git 会同步哪些内容

`account_sources.csv` 会提交到 Git。其他机器拉取仓库后，可以同步以下公众号信息：

- 稳定的本地编号；
- 上游公众号 ID；
- 当前公众号名称；
- 示例文章链接。

这样可以方便地审阅、维护和同步“需要爬取哪些公众号”。

以下内容不会通过 Git 同步：

- `results/articles/`：已下载正文、URL 去重数据和增量断点；
- `results/record/` 与 `results/summary.csv`：本机运行记录和派生统计；
- `src/auth/config/`：私密认证凭据；
- `third_party/`：可按固定版本重新安装的第三方源码与虚拟环境。

因此，新机器虽然能继承相同的公众号登记表，但没有原机器的本地文章断点。在 `incremental` 模式下，这些公众号会被视为“没有本地历史”，第一次只处理最新的 `articles_per_account` 条记录。如果希望另一台机器从完全相同的增量位置继续，需要单独复制 `results/articles/`，不要把它提交到 Git。

## 8. 常见问题

### 没有识别到文章链接

- 检查 `crawl.input_csv` 是否指向一个真实存在、扩展名为 `.csv` 的文件。
- 确保每个完整的 `https://mp.weixin.qq.com/s/...` 链接单独位于一个单元格中。
- 运行 `python3 src/crawl.py --check`，查看程序识别到的链接数和登记表中的公众号数。
- 只要登记表非空，即使当前输入没有新链接，程序仍会继续处理已有公众号。

### 二维码没有出现或登录失败

- 根据终端打印的准确路径打开二维码 PNG，并在五分钟超时前扫码。
- 扫码后，记得在微信手机客户端中确认登录。
- 查看本次运行目录中的 `tools-log/wechat-mp-tools.log` 和 `tools-log/crawler.log`。
- 如果凭据已经过期，或真实请求返回认证错误，请重新启动任务，通过新的扫码认证替换失效凭据。

### 回退浏览器启动失败

- 检查 `we-mp-rss` 虚拟环境中是否安装了固定版本的 Playwright，以及与之匹配的 Chromium。
- 补充 Chromium 在当前 Linux 系统中缺少的动态库。
- 查看 `tools-log/we-mp-rss.log`。上游工具中的可选数据库警告不一定代表文章提取失败，最终应以文章内容校验结果为准。

### 运行速度较慢

程序会顺序处理公众号、历史页面和文章，并在请求之间保留间隔。实际耗时取决于登记表规模、爬取模式、文章和图片大小、重试次数，以及是否需要启动回退浏览器。需要停止时，请使用 `Ctrl+C` 受控中止，不要从外部强制终止整个进程组。

### 5200 端口已被占用

`wechat-mp-tools` 默认使用 `127.0.0.1:5200`。请停止占用该端口的无关程序；如果端口上已经运行服务，请先确认它确实是兼容版本的 `wechat-mp-tools`，再启动爬虫。

## 致谢

特别感谢以下项目的维护者和贡献者：

- [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools)：提供微信公众号识别、历史列表获取、认证和主要正文下载能力；
- [we-mp-rss](https://github.com/rachelos/we-mp-rss)：提供基于浏览器的文章提取能力，在主要抓取结果不完整时作为回退方案。

`wx-crawl` 通过适配层集成这两个项目，不修改它们受 Git 跟踪的源代码。
