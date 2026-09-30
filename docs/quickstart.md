# 快速开始

## 安装

```bash
uv add jmcpy
# 或
pip install jmcpy
```

可选依赖：

| extra | 作用 |
| --- | --- |
| `jmcpy[keyring]` | 把会话主密钥交给操作系统钥匙串（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service） |
| `jmcpy[httpx]` | 使用 httpx 作为传输后端；默认是 curl-cffi（浏览器指纹伪装） |
| `jmcpy[all]` | 以上全部 |

## 第一个脚本

```python
from jmcpy import Client

with Client() as client:
    page = client.search("关键词")
    print(f"共 {page.total} 条，第 {page.page} / {page.page_count} 页")
    for item in page:
        print(item.book_id, item.title)
```

`Client()` 在构造时只做两件事：读取配置、尝试恢复上次登录态，**不会**立刻发网络请求。
第一次真正调用接口时才会：

1. 解析可用端点（有缓存就用缓存，否则向端点源/发布页拉一次）；
2. 用一次 `/setting` 请求播种服务端下发的 Cookie，并同步最新接口版本号。

## 取详情与图片

```python
from jmcpy import Client, ExportFormat

with Client() as client:
    book = client.get_book("JM1114751")
    print(book.title, book.author, len(book.chapters), book.tags)

    chapter = client.get_chapter(book.chapters[0].chapter_id)
    print(chapter.title, len(chapter))

    # 单张图片：原始字节
    picture = client.picture(chapter, 1)
    raw = client.fetch_picture(picture)

    # 整章：合成 PDF
    result = client.download(chapter, output=ExportFormat.PDF, dest="./downloads")
    print(result.pdf)
```

## 异步

```python
import asyncio

from jmcpy import AsyncClient


async def main() -> None:
    async with AsyncClient() as client:
        page = await client.search("关键词")
        book = await client.get_book(page.items[0].book_id)
        async for feed in client.iter_comments(book.book_id, limit=2):
            print(feed.page, len(feed))


asyncio.run(main())
```

同步与异步的类名、方法名、参数完全一致：

| 同步 | 异步 |
| --- | --- |
| `Client` | `AsyncClient` |
| `MobileClient` | `AsyncMobileClient` |
| `WebClient` | `AsyncWebClient` |

## 选择客户端实现

大多数场景直接用门面 `Client` 即可，它默认走移动端接口，只在需要副分类搜索时
自动切到网页端。也可以直接用某一种实现：

```python
from jmcpy import MobileClient, WebClient

with MobileClient() as mobile:
    book = mobile.get_book(1114751)

with WebClient() as web:  # 需要能访问网页端的网络环境
    page = web.search("关键词")
```

## 车号写法

`get_book` / `get_chapter` / `get_comments` 等接受这些写法：

```python
client.get_book(1114751)
client.get_book("1114751")
client.get_book("JM1114751")
client.get_book("https://example/album/1114751/some-title")
```

写成别的形式会抛 `InvalidArgument`（它同时是 `ValueError`）。

## 出错时

```python
from jmcpy import ChallengeBlocked, JmcpyError, NotFound, RegionBlocked, RequestFailed

try:
    client.get_book(1)
except NotFound:
    ...  # 本子不存在
except RequestFailed as exc:
    ...  # 重试与换端点都失败；exc.failures 是每一次尝试的记录
except (ChallengeBlocked, RegionBlocked):
    ...  # 反爬验证页 / 地区限制，重试无用，需要换网络环境或代理
except JmcpyError:
    ...  # 其余本包异常的基类
```
