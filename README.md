# AstrBot 禁漫下载插件

[![AGPL-3.0 License](https://img.shields.io/github/license/yourname/astrbot_plugin_jmcomic)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E6%8F%92%E4%BB%B6-orange)](https://github.com/Soulter/AstrBot)

本插件为 [AstrBot](https://github.com/Soulter/AstrBot) 提供了禁漫天堂（JMComic）的搜索、下载、自动生成 PDF 或 ZIP 打包功能，支持**范围下载**、**排行榜**、**图文详情**，并内置两种磁盘清理模式，有效管理存储空间。

---

## ✨ 功能亮点

- 📥 **下载本子/章节**  
  - 默认下载后自动转换为 PDF 文件并发送。  
  - 支持 `/jmz` 命令，打包为 ZIP 压缩包发送。  
  - 支持**范围下载**，例如 `/jm download 123 1-5` 仅下载第 1~5 章。

- 🔍 **搜索本子**  
  - 支持关键词搜索，分页显示结果。  
  - 命令 `/jms <关键词> [页码]`，默认第 1 页。

- 📊 **排行榜**  
  - 获取月榜、周榜、日榜。  
  - 命令 `/jmr [week|day] [页码]`，默认月榜第 1 页。

- 📄 **图文详情**  
  - 查看本子标题、作者、收藏数、标签、章节列表。  
  - **附带封面图片**，以合并转发消息发送。  
  - 命令 `/jm detail <本子号>`。

- 🧹 **智能清理**  
  - **count 模式**：每个用户最多保留 N 个本子（默认 10 个），超过自动删除最旧。  
  - **after_send 模式**：每次发送后立即删除本次下载的所有文件（原图 + PDF/ZIP）。  
  - 可在 AstrBot 管理面板自由切换。

- 📦 **自动依赖安装**  
  - 首次运行时自动检测并安装 `jmcomic`、`Pillow`、`img2pdf` 等依赖，无需手动操作。

- 🌐 **跨平台兼容**  
  - 所有路径基于插件目录动态生成，无论部署在何处均可正常运行。

- 🧪 **异常日志**  
  - 下载失败时会记录详细错误日志，便于排查问题。

---

## 📦 安装方法

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

如果使用 AstrBot 后台的“上传插件”功能遇到临时目录问题，建议直接使用文件夹安装：

1. 下载本仓库的 ZIP 文件并解压。
2. 将解压后的文件夹（例如 `astrbot_plugin_jmcomic`）复制到 AstrBot 的 `data/plugins/` 目录下。
3. 重启 AstrBot。

---

## 🚀 使用说明

所有命令以 `/jm` 开头，支持在群聊或私聊中使用。

### 下载本子（PDF）

```
/jm download <本子号> [范围]
```

- 默认（不加范围）：下载本子全部章节 → 合并为 PDF → 发送 PDF 文件。
- 加范围：`1-10` 或 `5`，下载指定章节。

**示例**：
```
/jm download 123
/jm download 123 1-5
/jm download 123 3
```

### 下载本子（ZIP 打包）

```
/jmz <本子号> [范围]
```

- 下载本子 → 打包为 ZIP → 发送 ZIP 文件。

**示例**：
```
/jmz 123
/jmz 123 1-5
```

### 搜索本子

```
/jms <关键词> [页码]
```

默认页码为 1，最多显示前 10 条结果。

**示例**：
```
/jms 火影
/jms 海贼王 2
```

### 排行榜

```
/jmr [week|day] [页码]
```

- 不加参数：月榜第 1 页。
- 加 `week`：周榜。
- 加 `day`：日榜。
- 可选页码。

**示例**：
```
/jmr
/jmr week 2
/jmr day
```

### 查看本子详情

```
/jm detail <本子号>
```

返回一条合并转发消息，包含标题、作者、收藏数、标签、前 10 个章节列表，并附带封面图片。

**示例**：
```
/jm detail 123
```

### 帮助

```
/jm help
```

显示所有命令及使用示例。

---

## ⚙️ 配置选项

在 AstrBot 管理面板的插件配置页，可以为该插件添加以下配置（`_conf_schema.json` 已内置）：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `download_dir` | string | `./data/jm_downloads` | 下载根目录，所有下载的文件将保存在此目录下。 |
| `cleanup_mode` | string | `count` | 清理模式：`count`（按数量保留）或 `after_send`（发送后立即删除本次下载的所有文件）。 |
| `max_albums` | int | `10` | 当 `cleanup_mode` 为 `count` 时，每个用户最多保留的本子数量（0 表示不限制）。 |
| `delete_temp_cover` | bool | `true` | 详情指令中，发送封面图片后是否删除临时封面文件。 |
| `enable_jm_log` | bool | `false` | 是否显示 jmcomic 库的内部调试日志（用于排查问题）。 |
| `option_file` | string | `""` | 自定义 jmcomic 选项配置文件路径（YAML 格式），留空则使用内置默认配置。 |

修改配置后，**需要重启 AstrBot 才能生效**。

---

## 📁 文件结构

```
astrbot_plugin_jmcomic/
├── _conf_schema.json      # 插件配置定义
├── main.py                 # 插件主代码
├── jmcomic/                # jmcomic 库（已集成，无需额外安装）
│   └── ...                 # 库文件
├── assets/
│   └── option/
│       └── option_workflow_download.yml  # 默认 jmcomic 配置文件（可选）
└── README.md               # 本文件
```

---

## ❓ 常见问题

### Q: 上传 ZIP 安装时提示 `FileNotFoundError: [Errno 2] No such file or directory`
A: 这是 AstrBot 临时目录不存在导致的。请使用**方法一（git clone）** 或**方法二（手动解压复制文件夹）** 安装，无需上传 ZIP。

### Q: 下载后没有生成 PDF？
A: 请检查是否成功安装了 `img2pdf`。插件会自动尝试安装，若失败请手动执行 `pip install img2pdf`。也可查看插件日志（位于 `下载目录/logs/`）获取详细错误。

### Q: 如何修改默认保留的本子数量？
A: 在插件配置中设置 `max_albums` 项，并将 `cleanup_mode` 设为 `count`。

### Q: 是否支持多用户隔离？
A: 支持。每个用户（QQ 号）拥有独立的下载子目录，清理操作也仅针对当前用户。

### Q: 详情命令不显示封面？
A: 可能是封面下载失败，请检查网络和 jmcomic 配置。如果不需要封面，可以忽略该错误，插件仍会发送文本详情。

---

## 📄 许可证

本项目采用 **AGPL-3.0** 许可证。详情请参阅 [LICENSE](LICENSE) 文件。

---

## 🙏 致谢

- [hect0x7/JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python) – 强大的禁漫爬虫库。
- [Soulter/AstrBot](https://github.com/Soulter/AstrBot) – AstrBOt插件模板

---

如有任何问题或建议，欢迎提交 [Issue](https://github.com/yourusername/astrbot_plugin_jmcomic/issues) 或 Pull Request。