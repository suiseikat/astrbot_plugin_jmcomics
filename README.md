# AstrBot 禁漫下载插件

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/yourusername/astrbot_plugin_jmcomic)](LICENSE)

本插件为 [AstrBot] 提供了禁漫天堂（JMComic）的搜索、下载、自动生成 PDF 或 ZIP 打包功能，并内置自动清理旧本子机制，有效管理磁盘空间。

## 功能亮点

- 🔍 **搜索本子**：支持关键词搜索，分页显示结果。
- 📥 **下载本子/章节**：
  - 默认下载后自动转换为 PDF 文件并发送。
  - 支持 `--zip` 参数，打包为 ZIP 压缩包发送。
- 🧹 **自动清理**：每个用户最多保留 10 个本子（可配置），超过时自动删除最旧的文件夹及对应的 PDF 文件。
- 📦 **依赖自动安装**：首次运行时自动检测并安装 `jmcomic`、`Pillow`、`img2pdf` 等依赖。
- 🌐 **跨平台兼容**：所有路径基于插件目录动态生成，无论部署在何处均可正常运行。
- 🧪 **异常日志**：下载失败时会记录详细错误日志，便于排查问题。

## 安装方法

### 方法一：通过 GitHub 克隆（推荐）

1. 进入 AstrBot 的插件目录：
   ```bash
   cd /path/to/AstrBot/data/plugins/
   ```
2. 克隆本仓库：
   ```bash
   git clone https://github.com/yourusername/astrbot_plugin_jmcomic.git
   ```
3. 重启 AstrBot。插件首次加载时会自动安装所需依赖。

### 方法二：手动安装（ZIP 上传）

如果使用 AstrBot 后台的“上传插件”功能，可能会遇到临时目录权限问题，建议直接使用文件夹安装：

1. 下载本仓库的 ZIP 文件并解压。
2. 将解压后的文件夹（例如 `astrbot_plugin_jmcomic`）复制到 AstrBot 的 `data/plugins/` 目录下。
3. 重启 AstrBot。

## 使用说明

所有命令以 `/jm` 开头，支持在群聊或私聊中使用。

### 下载本子

```
/jm download <本子ID> [--zip]
```

- 默认（不加 `--zip`）：下载本子所有章节 → 合并为 PDF → 发送 PDF 文件。
- 加 `--zip`：下载本子 → 打包为 ZIP → 发送 ZIP 文件。

**示例**：
```
/jm download 123456
/jm download 123456 --zip
```

### 下载章节

```
/jm photo <章节ID> [--zip]
```

- 默认：下载该章节 → 转为 PDF → 发送。
- 加 `--zip`：打包为 ZIP → 发送。

**示例**：
```
/jm photo 789012
/jm photo 789012 --zip
```

### 搜索本子

```
/jm search <关键词> [页码]
```

默认页码为 1，最多显示前 10 条结果。

**示例**：
```
/jm search ba
/jm search 终末地 2
```

### 查看本子详情

```
/jm detail <本子ID>
```

显示标题、作者、收藏数、章节数及前 10 个章节列表。

**示例**：
```
/jm detail 123456
```

## 配置选项

在 AstrBot 的插件管理页面，可以为该插件添加以下配置（JSON 格式）：

```json
{
  "download_dir": "./data/jm_downloads",   // 下载根目录（默认）
  "max_albums": 10,                         // 每个用户最多保留的本子数量
  "enable_jm_log": false,                   // 是否显示 jmcomic 内部调试日志
  "option_file": ""                          // 自定义 jmcomic option 配置文件路径（留空则使用内置默认）
}
```

## 依赖自动安装

插件首次启动时会自动检查并尝试安装以下 Python 库：

- `jmcomic`（禁漫 API 核心库）
- `Pillow`（图片处理）
- `img2pdf`（图片转 PDF）

如果自动安装失败（例如网络问题或权限不足），请手动执行以下命令安装：

```bash
pip install jmcomic Pillow img2pdf
```

## 文件结构

```
astrbot_plugin_jmcomic/
├── main.py                 # 插件主代码
├── assets/
│   └── option/
│       └── option_workflow_download.yml  # 默认 jmcomic 配置文件（可选）
└── README.md               # 本文件
```

## 常见问题

### Q: 上传 ZIP 安装时提示 `FileNotFoundError: [Errno 2] No such file or directory`
A: 这是 AstrBot 临时目录不存在导致的。请使用**方法一（git clone）** 或**方法二（手动解压复制文件夹）** 安装，无需上传 ZIP。

### Q: 下载后没有生成 PDF？
A: 请检查是否成功安装了 `img2pdf`。可查看插件日志（位于 `下载目录/logs/`）获取详细错误。如果依赖缺失，插件会自动尝试安装，也可手动执行 `pip install img2pdf`。

### Q: 如何修改默认保留的本子数量？
A: 在插件配置中设置 `max_albums` 项。

### Q: 是否支持多用户隔离？
A: 支持。每个用户（QQ 号）拥有独立的下载子目录，清理操作也仅针对当前用户。

## 许可证

[AGPL-3.0 license](LICENSE)

---

如有任何问题或建议，欢迎提交 [Issue](https://github.com/yourusername/astrbot_plugin_jmcomic/issues) 或 Pull Request。