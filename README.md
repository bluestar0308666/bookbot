# BookBot：大型 PDF 私人流式书库

BookBot 是一个面向大型 PDF 的轻量私人阅读系统。它把本地 Windows 阅读器和可选的私人云书库结合起来，让几百 MB 甚至更大的书籍也能按页加载，并可在电脑、手机和平板之间继续阅读。

> BookBot 不包含 AI，也不会自动上传原始 PDF。只有你主动执行“发布”时，处理后的书籍页面才会传到自己的服务器。

## 主要功能

- 使用 PDF.js 直接打开本机 PDF，支持搜索、缩放和页面导航
- 将大型 PDF 转成可断点续传、按页加载的网页书籍
- 连续滚动、双栏分屏、目录导航和多书同时打开
- 保存阅读进度、阅读时长、书签、高亮和关联笔记
- 将处理后的书籍分块发布到私人服务器
- 支持 PWA，可在兼容浏览器中安装到桌面或手机主屏幕

## Windows 本地使用

需要 Python 3.11 或更高版本。

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

安装完成后，双击 `启动BookBot桌面版.cmd`，或运行：

```powershell
.\.venv\Scripts\python server.py --desktop
```

默认访问地址为 `http://127.0.0.1:4173/`。本地路径、阅读记录和发布配置保存在 `data/`，不会被 Git 提交。

## 处理大型 PDF

把 PDF 放入 `待处理书籍/`，然后运行：

```powershell
.\.venv\Scripts\python tools\book_bot.py
```

生成内容位于 `dist/books/`。处理过程支持中断后继续。大型书籍转换时会额外占用磁盘空间，请在确认生成结果正常前保留原始 PDF。

## 部署私人云书库

自带的 Linux 服务默认只监听 `127.0.0.1:13011`。建议在它前面使用带身份认证和 HTTPS 的 Caddy 或 Nginx，避免直接暴露服务端口。

升级已有安装：

```bash
sudo env BOOKBOT_USER="$USER" bash app/install.sh
```

安装脚本会保留：

- `library/books/` 中的书籍页面
- `library/assets/library.json` 书架索引
- `data/reader.db` 阅读数据库

重启服务前会自动备份数据库。

首次部署时，请先创建内容为 `[]` 的 `/opt/bookbot/library/assets/library.json`，从 `deploy/bookbot.env.example` 创建 `/etc/bookbot.env`，再运行安装脚本。反向代理示例见 `deploy/Caddyfile.example`。

## 从 Windows 发布到服务器

复制 `data.example/publish-config.example.json` 为 `data/publish-config.json`，填写自己的服务器地址。发布令牌属于敏感信息，不要提交到 GitHub。

也可以交互式设置令牌：

```powershell
$env:BOOKBOT_SERVER_BASE = "https://books.example.com"
.\tools\set_publish_token_interactive.ps1
```

## 数据目录

- `data/`：阅读记录、同步记录和发布配置
- `library/books/`：服务器上的书籍与页面
- `library/assets/library.json`：服务器书架索引
- `dist/books/`：本地转换后的书籍

这些目录可能包含个人数据或受版权保护的内容，已在 `.gitignore` 中排除。

## 测试

```powershell
.\.venv\Scripts\python tools\test_legacy_removal.py
.\.venv\Scripts\python tools\test_remove_endpoint.py
.\.venv\Scripts\python tools\test_local_only_removal.py
.\.venv\Scripts\python tools\test_local_plus_stream_removal.py
.\.venv\Scripts\python tools\test_remote_version.py
.\.venv\Scripts\python tools\test_publish_e2e.py
.\.venv\Scripts\python tools\test_publish_client.py
node --check library\app.js
```

## 开源协议与第三方组件

BookBot 源码采用 [MIT License](LICENSE)。项目内置的 PDF.js 和字体保留各自的许可证，详见 `library/vendor/pdfjs/`。仓库不包含任何书籍或演示 PDF。

