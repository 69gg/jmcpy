# jmcpy

禁漫天堂的 Python SDK。

- **同步 / 异步双份 API**，接口逐字对应
- **两种客户端实现**：移动端接口（请求签名 + 响应解密，字段最全）与网页端接口
  （补齐移动端没有的分类副分类搜索）；门面客户端按能力自动路由
- **自动重试 + 多端点切换**：同端点退避重试、失败换线路，两种模式可配；端点池支持
  远端自动发现与本地缓存
- **绕过反爬**：默认用 curl-cffi 伪装浏览器指纹，可回退 httpx
- **可选登录**：凭据加密保存在用户配置目录，主密钥交给操作系统钥匙串；不登录也能用
- **图片下载与分块还原**，交付为 bytes / base64 / 文件路径 / PDF

> 本项目仅供学习网络协议与 Python 工程实践使用。请遵守所在地法律法规以及目标站点的服务条款，
> 不要用于商业用途或大规模抓取。

## 安装

```bash
uv add jmcpy              # 或 pip install jmcpy
uv add "jmcpy[keyring]"   # 额外启用操作系统钥匙串保存会话主密钥
uv add "jmcpy[httpx]"     # 额外启用 httpx 传输后端（默认用 curl-cffi）
```

要求 Python 3.11 及以上。

## 快速开始

```python
from jmcpy import Client, ExportFormat

with Client() as client:
    page = client.search("关键词", page=1)
    print(page.total, page.page_count)

    book = client.get_book("JM1114751")  # 车号也接受 int、"1114751"、本子 URL
    print(book.title, len(book.chapters))

    chapter = client.get_chapter(book.chapters[0].chapter_id)
    print(len(chapter), chapter.pictures[:3])  # 章节里的图片文件名

    result = client.download(chapter, output=ExportFormat.PDF, dest="./downloads")
    print(result.pdf, result.ok)
```

异步版本逐字对应，只是加 `await`：

```python
import asyncio

from jmcpy import AsyncClient, ExportFormat


async def main() -> None:
    async with AsyncClient() as client:
        page = await client.search("关键词")
        book = await client.get_book(page.items[0].book_id)
        chapter = await client.get_chapter(book.chapters[0].chapter_id)
        await client.download(chapter, output=ExportFormat.PATH, dest="./downloads")


asyncio.run(main())
```

## 搜索

```python
from jmcpy import Genre, RankingSpan, SearchTarget, SortBy, SubGenre, TimeRange

with Client() as client:
    client.search("MANA")  # 站内搜索
    client.search("MANA", target=SearchTarget.AUTHOR)  # 按作者
    client.search("无修正", sort=SortBy.VIEWS, time_range=TimeRange.WEEK)
    client.search("MANA", genre=Genre.DOUJIN)  # 限定大分类

    # 副分类只有网页端支持，门面会自动路由；若当前网络访问不了网页端会抛 ChallengeBlocked
    client.search("MANA", genre=Genre.DOUJIN, sub_genre=SubGenre.CG)

    client.browse(genre=Genre.HANMAN, sort=SortBy.LIKES, time_range=TimeRange.MONTH)
    client.ranking(RankingSpan.DAY)  # 日/周/月排行
```

## 评论

```python
with Client() as client:
    feed = client.get_comments(1114751, page=1)
    print(feed.total, feed.page_count)

    for comment in feed:
        print(comment.display_name, "剧透" if comment.is_spoiler else "", comment.content)
        for reply in comment.replies:  # 回评（可多层）
            print("  ", reply.display_name, reply.content)

    for feed in client.iter_comments(1114751, limit=3):  # 逐页遍历
        print(feed.page, len(feed))
```

## 登录（可选）

不登录也能使用除鉴权接口之外的全部功能。登录后凭据会保存在用户配置目录：

| 平台 | 路径 |
| --- | --- |
| Linux | `~/.config/jmcpy/session.json` |
| macOS | `~/Library/Application Support/jmcpy/session.json` |
| Windows | `%LOCALAPPDATA%\jmcpy\session.json` |

文件用 Fernet 加密，主密钥存放在操作系统钥匙串；钥匙串不可用时降级为 `0600` 权限的
明文文件并给出 warning。可用 `JMCPY_HOME` 或 `Settings(home=...)` 改到别处。

```python
with Client() as client:
    client.login("用户名", "密码")  # remember=False 则只留在内存里
    print(client.account().username)

# 下次构造客户端时会自动恢复登录态（可用 restore_session=False 关闭）
with Client() as client:
    print(client.is_logged_in())
```

## 下载与输出格式

```python
from jmcpy import ExportFormat

with Client() as client:
    chapter = client.get_chapter(1114751)

    images = client.download(chapter, output=ExportFormat.BYTES)  # list[bytes]
    texts = client.download(chapter, output=ExportFormat.BASE64)  # list[str]
    paths = client.download(chapter, output=ExportFormat.PATH, dest="./out")  # list[Path]
    pdf = client.download(chapter, output=ExportFormat.PDF, dest="./out")  # Path

    for artifact in pdf:
        print(artifact.index, artifact.suffix, artifact.decoded, artifact.size)
    print(pdf.pdf, len(pdf.failures))
```

细节见 [docs/downloading.md](docs/downloading.md)：并发、是否还原、覆盖策略、质量、
PDF 分辨率、失败收集与进度回调都在那里。

## 配置

```python
from jmcpy import Client, RetryMode, Settings

settings = Settings(
    timeout=20.0,  # 接口超时（秒）
    image_timeout=60.0,  # 图片超时（秒）
    retry_times=3,  # 每个端点的重试次数
    retry_mode=RetryMode.RETRY_FIRST,
    concurrency=8,  # 图片并发
    proxy="http://127.0.0.1:7890",
)
with Client(settings) as client:
    ...
```

也可以写配置文件（默认 `<配置目录>/config.toml`）或用环境变量覆盖：

```toml
# ~/.config/jmcpy/config.toml
timeout = 30
retry_mode = "rotate_first"
mobile_endpoints = ["www.cdngwc.cc"]
```

```bash
JMCPY_TIMEOUT=30 JMCPY_PROXY=http://127.0.0.1:7890 python your_script.py
```

优先级：显式参数 > 环境变量 > 配置文件 > 出厂默认。全部配置项见
[docs/configuration.md](docs/configuration.md)。

## 文档

- [快速开始](docs/quickstart.md)
- [配置项与环境变量](docs/configuration.md)
- [下载与输出格式](docs/downloading.md)
- [API 参考](docs/api-reference.md)
- [架构说明](docs/architecture.md)
- [疑难排查](docs/troubleshooting.md)
- [变更记录](CHANGELOG.md)

## 许可

MIT
