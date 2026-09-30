# jmcpy

禁漫天堂（JMComic）的 Python SDK。只做库，不带 GUI、不带命令行。

- 同步 / 异步双份 API
- 两种客户端实现：移动端接口（加密请求与响应）与网页端接口（用于补齐分类副分类搜索）
- 自动重试 + 多端点（域名）切换，超时、退避、并发全部可配
- 浏览器指纹伪装（curl-cffi），可回退到 httpx
- 可选登录，凭据加密后落在用户配置目录，密钥交给操作系统钥匙串
- 图片下载与分块解扰，输出 bytes / base64 / 文件路径 / PDF

> 本项目仅供学习网络协议与 Python 工程实践使用。请遵守所在地法律法规以及目标站点的服务条款，
> 不要用于商业用途或大规模抓取。

## 安装

```bash
uv add jmcpy            # 或 pip install jmcpy
uv add "jmcpy[keyring]" # 额外启用操作系统钥匙串保存会话密钥
```

要求 Python 3.11 及以上。

## 快速开始

```python
from jmcpy import Client, ExportFormat

with Client() as client:
    page = client.search("关键词", page=1)
    print(page.total, page.page_count)
    for item in page.items:
        print(item.book_id, item.title)

    book = client.get_book("JM1472715")
    print(book.title, len(book.chapters))

    chapter = client.get_chapter(book.chapters[0].chapter_id)
    result = client.download(chapter, output=ExportFormat.PDF, dest="./downloads")
    print(result.pdf)
```

异步版本逐字对应，只需改成 `AsyncClient` 并 `await`：

```python
import asyncio
from jmcpy import AsyncClient, ExportFormat


async def main() -> None:
    async with AsyncClient() as client:
        page = await client.search("关键词")
        print(page.total)


asyncio.run(main())
```

## 登录（可选）

不登录也能使用除「需要鉴权」之外的全部功能。登录后会话会保存到用户配置目录：

- Linux：`~/.config/jmcpy/session.json`
- macOS：`~/Library/Application Support/jmcpy/session.json`
- Windows：`%LOCALAPPDATA%\jmcpy\session.json`

文件使用 Fernet 加密，主密钥存放在操作系统钥匙串；钥匙串不可用时降级为 `0600` 权限的明文文件并给出告警。

```python
with Client() as client:
    client.login("用户名", "密码")
    print(client.account().username)
```

## 配置

```python
from jmcpy import Client, RetryMode, Settings

settings = Settings(
    timeout=20.0,
    image_timeout=60.0,
    retry_times=3,
    retry_mode=RetryMode.RETRY_FIRST,
    concurrency=8,
)
with Client(settings) as client:
    ...
```

所有配置项都可以用 `JMCPY_` 前缀的环境变量覆盖，详见 [docs/configuration.md](docs/configuration.md)。

## 文档

- [快速开始](docs/quickstart.md)
- [配置项与环境变量](docs/configuration.md)
- [下载与输出格式](docs/downloading.md)
- [API 参考](docs/api-reference.md)
- [架构说明](docs/architecture.md)
- [疑难排查](docs/troubleshooting.md)

## 许可

MIT
