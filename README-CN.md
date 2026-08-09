# wx-crawl

[English](README.md) | [简体中文](README-CN.md)

`wx-crawl` 用于爬取微信公众号的文章正文和历史列表。项目使用 [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools) 作为历史列表和正文抓取的主要工具；当主要工具抓取失败或内容不完整时，使用 [we-mp-rss](https://github.com/rachelos/we-mp-rss) 回退抓取。感谢这两个项目提供的核心能力。

## 1. 项目作用

向 `wx-crawl` 提供一个或多个微信公众号文章链接，程序会识别链接所属的公众号并下载其历史文章。输入的文章链接本身也一定会被抓取。

程序会维护本地公众号登记表，从各公众号的历史列表中发现新文章，按 URL 避免重复下载，并以结构化方式保存文章正文、HTML、元数据和图片。

## 2. 项目结构

```text
wx-crawl/
├── src/                         爬虫、认证和工具适配代码
├── install/
│   ├── requirements.txt         wx-crawl 使用的依赖
│   └── third_party_versions.yaml
│                                第三方仓库地址和固定提交版本
├── third_party/                 第三方源码和虚拟环境
├── config.yaml                  用户爬取配置
├── input_template.csv           最小输入 CSV 示例
├── account_sources.csv          已发现公众号的持久化登记表
├── results/                     下载的文章和运行记录
└── .python-version              推荐的 Python 版本
```

`third_party/`、`results/` 和 `src/auth/config/` 是本地运行数据，不会提交到 Git。认证 Token 保存在 `src/auth/config/`，不得提交到代码仓库。

## 3. 快速开始

1. 最简单的方式是在 `input_template.csv` 中添加一个或多个微信公众号文章链接，每个链接单独放在一个单元格中。
2. 也可以使用任意其他 CSV，并在 `config.yaml` 中将 `crawl.input_csv` 设置为该文件的路径。CSV 不限制列、表头或布局，**只要求每个微信文章链接单独出现在一个单元格中**。
3. 选择爬取模式：
   - `incremental`：增量模式。对于已有本地文章的公众号，下载该公众号在“本地已保存的最新文章”之后发布的全部新文章；对于首次发现、尚无本地断点的公众号，下载其最新的 `articles_per_account` 篇文章。
   - `window`：窗口模式。所有公众号都以最新的 `articles_per_account` 篇文章作为下载范围；本地已保存的文章会按 URL 跳过。
4. 检查安装和配置：

   ```bash
   python3 src/crawl.py --check
   ```

5. 开始爬取：

   ```bash
   python3 src/crawl.py
   ```

### 注意事项

- 首次认证运行时，终端会显示当前运行记录中二维码 PNG 的准确路径。使用微信扫码并确认登录后，认证信息会保存在本地供后续运行复用。
- 每个输入文章链接都会被明确下载，即使它不在所选历史窗口内。
- 新发现的公众号会加入 `account_sources.csv`，所有已登记公众号的名称都会在每次运行时重新校验和更新。
- 已有文章按 URL 去重。
- 任务正常完成或被中断后，工具服务都会自动关闭。

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

只有成功下载的文章才会加入文章集合和统计记录。

### `results/summary.csv`

每个已登记公众号占一行：

| 字段 | 含义 |
| --- | --- |
| `编号` | 稳定的本地公众号编号 |
| `公众号唯一ID` | 上游服务返回的公众号唯一 ID |
| `公众号名称` | 当前公众号名称 |
| `文章总数` | 本地保存的文章总数 |
| `文章总存储（M）` | 文章占用的总存储空间，单位为 MiB |
| `最新文章时间` | 本地最新文章的发布时间 |
| `最新文章标题` | 本地最新文章的标题 |

### 每次运行的记录

每次运行都会在 `results/record/` 下创建一个以启动时间命名的目录。

- `account_summary.csv`：公众号名称、本次新增文章数、爬取耗时（分钟）和新增存储空间（MiB）。
- `article_details.csv`：本次新增文章的公众号名称、文章标题和发布时间。
- `tools-log/`：爬虫和第三方工具的诊断日志。临时登录二维码也可能出现在这里，并在认证完成后删除。

### 每篇文章的文件

- `content.txt`：清洗后的文章正文。
- `metadata.json` 或 `data.json`：标题、原始 URL、发布时间和资源信息。
- `*.html`：保存的文章 HTML，也可能包含一份原始 HTML。
- `media/`：下载的封面和正文图片。
- `fallback.html` 和 `fallback_metadata.json`：使用 `we-mp-rss` 回退抓取时生成。

## 5. 使用 Codex 安装

推荐让 Codex 安装并验证本项目。请在仓库顶层向 Codex 提交以下任务：

```text
为本地运行配置 wx-crawl 仓库。

使用 .python-version 指定的 Python 3.12。读取
install/third_party_versions.yaml，将两个仓库克隆到 third_party/ 下，
使用文件中记录的准确目录名和提交版本，并且不要修改它们受 Git 跟踪的源码。

在每个第三方仓库中分别创建独立的 .venv。将 wechat-mp-tools 自身依赖和
install/requirements.txt 安装到 wechat-mp-tools 环境中。将 we-mp-rss 自身依赖
安装到它的独立环境中，然后安装对应的 Playwright Chromium 浏览器。

最后运行 `python3 src/crawl.py --check`，确认两个第三方 Git 工作区均为干净状态，
并确保没有工具服务残留运行。
```

第三方工具的预期目录结构为：

```text
third_party/
├── wechat-mp-tools/
│   └── .venv/
└── we-mp-rss/
    └── .venv/
```

使用 Python 3.12 是有意的选择：固定版本的 `wechat-mp-tools` 依赖要求 `mitmproxy` 12，并且本项目已经使用 Python 3.12 完整验证。

## 致谢

特别感谢以下项目的维护者和贡献者：

- [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools)：提供微信公众号识别、历史列表获取、认证和主要正文下载能力。
- [we-mp-rss](https://github.com/rachelos/we-mp-rss)：提供基于浏览器的文章提取能力，在主要抓取结果不完整时作为回退方案。

`wx-crawl` 通过适配层集成这两个项目，不修改它们受 Git 跟踪的源代码。
