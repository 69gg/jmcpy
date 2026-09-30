# API 参考

包内所有公开名字都可以从顶层导入：

```python
from jmcpy import Client, AsyncClient, Settings, ExportFormat
```

## 客户端

### `Client` / `AsyncClient`

门面客户端：默认走移动端接口，只有副分类搜索会按需路由到网页端。

| 成员 | 说明 |
| --- | --- |
| `Client(settings=None, *, mobile=None, web=None, store=None)` | `settings` 接受 `Settings`、字段映射或 `None` |
| `.mobile` / `.web` | 两种底层实现；`.web` 首次访问时才创建 |
| `.settings` | 生效的配置 |
| `.endpoints` | 当前移动端端点池 |
| `.search(query, *, page=1, target=..., sort=..., time_range=..., genre=..., sub_genre=None)` | 搜索；带 `sub_genre` 时自动路由到网页端 |
| `.browse(*, page=1, genre=..., sub_genre=None, sort=..., time_range=...)` | 按分类浏览 |
| `.ranking(span=RankingSpan.WEEK, *, page=1, genre=..., sub_genre=None)` | 日/周/月排行 |
| `.get_book(book_id)` | 本子详情 |
| `.get_chapter(chapter_id, *, with_scramble_id=True)` | 章节详情（含图片与解扰参数） |
| `.get_scramble_id(chapter_id)` | 单独取解扰参数（带进程内缓存） |
| `.get_comments(book_id, *, page=1)` | 评论分页 |
| `.iter_comments(book_id, *, start=1, limit=None)` | 逐页遍历评论 |
| `.download(chapter, *, output=..., dest=None, password=None, subsampling=0, ...)` | 下载章节并交付；`password` 给 PDF 加打开密码，`subsampling` 控制页内 JPEG 色度采样（默认 4:4:4） |
| `.picture(chapter, index)` | 构造单张图片定位（1 起算） |
| `.fetch_picture(picture)` | 取单张图片原始字节 |
| `.cover_url(book_id, *, size="")` | 封面地址 |
| `.login(username, password, *, remember=True)` | 登录 |
| `.logout()` | 退出并清除本地凭据 |
| `.account()` | 当前用户资料 |
| `.is_logged_in()` | 是否持有凭据 |
| `.session` | 本地保存的登录快照 |
| `.save_session()` / `.restore_session()` | 手动保存 / 恢复 |
| `.credential_store` | 会话文件读写器 |
| `.set_cookies(mapping)` / `.get_cookies()` | 注入 / 读取 Cookie（同时作用于两种实现） |
| `.refresh_endpoints(*, refresh=True)` | 刷新端点池 |
| `.close()` | 释放连接资源；也支持 `with` / `async with` |

异步版全部方法同名，加 `await`；`iter_comments` 返回异步迭代器。

### `MobileClient` / `AsyncMobileClient`

移动端接口实现，字段最全。方法集与门面相同，但**没有** `sub_genre` 参数
（服务端会忽略它）。

```python
from jmcpy import MobileClient

with MobileClient() as client:
    page = client.search("MANA", target=SearchTarget.AUTHOR)
```

### `WebClient` / `AsyncWebClient`

网页端实现：支持 `genre + sub_genre` 组合的搜索与浏览，以及排行榜。

```python
from jmcpy import Genre, SubGenre, WebClient

with WebClient() as client:
    page = client.search("MANA", genre=Genre.DOUJIN, sub_genre=SubGenre.CG)
    client.browse(page=2, genre=Genre.HANMAN, sub_genre=SubGenre.CHINESE)
    client.ranking(RankingSpan.WEEK)
```

列表项解析出**车号、标题、作者**；`Listing.total` 只有搜索页提供（分类页没有该元素）。

搜索**必须带关键词**（站点要求至少两个字），空查询会抛 `InvalidArgument` 并提示改用 `browse()`；
关键词过短时站点自己返回错误页，SDK 转成 `ParseFailed` 并带上原文。

网页端域名运行时从发布页发现；发现不到时可以显式配置：

```python
WebClient(Settings(web_endpoints=("可用域名",)))
```

被反爬验证页拦截只是「这条线路不可用」，SDK 会自动换下一条；所有线路都不可用才抛
`ChallengeBlocked`。

## 模型

全部是不可变 dataclass，并保留原始响应片段 `raw`。

| 类型 | 关键字段 |
| --- | --- |
| `BookBrief` | `book_id` `title` `author` `description` `cover_url` `category` `sub_category` `liked` `is_favorite` `updated_at` |
| `Book` | 上述字段 + `authors` `tags` `works` `cast` `likes` `views` `comment_count` `chapter_count` `chapters` `related` `added_at`；派生 `author`、`is_single_chapter` |
| `ChapterBrief` | `chapter_id` `title` `order` |
| `Chapter` | `chapter_id` `book_id` `title` `order` `tags` `pictures` `scramble_id` `liked` `is_favorite` `added_at`；`picture(index, endpoint)`、`picture_urls(endpoint)`、`len()` |
| `Picture` | `chapter_id` `index` `filename` `url` `scramble_id`；`suffix` `stem` `is_animated` `is_supported` `with_endpoint()` |
| `Comment` | `comment_id` `content`（已去 HTML） `parent_id` `book_id` `author_id` `username` `nickname` `is_spoiler` `likes` `posted_at` `replies`；`display_name` `walk()` `reply_count` |
| `CommentFeed` | `items` `total` `page` `page_size`；`page_count` `comment_count` `has_next`，可迭代 |
| `Listing[T]` | `items` `total` `page` `page_size`；`page_count` `has_next`，可迭代、可索引 |
| `Taxonomy` | `taxonomy_id` `title` |
| `Account` | `uid` `username` `email` `avatar_url` `coins` `favorites` `level` `level_name` `experience` … |
| `PictureArtifact` | `index` `picture` `suffix` `decoded` `size` `data` / `text` / `path` |
| `DownloadFailure` | `index` `url` `error` `picture` |
| `ChapterDownload` | `chapter_id` `title` `artifacts` `failures` `pdf` `destination`；`ok` `total` `paths` |

## 枚举

| 枚举 | 取值 |
| --- | --- |
| `SearchTarget` | `SITE` `WORK` `AUTHOR` `TAG` `ACTOR` |
| `SortBy` | `LATEST` `VIEWS` `PICTURES` `LIKES` `SCORE` `COMMENTS` |
| `TimeRange` | `ALL` `DAY` `WEEK` `MONTH` |
| `Genre` | `ALL` `DOUJIN` `SINGLE` `SHORT` `ANOTHER` `HANMAN` `MEIMAN` `DOUJIN_COSPLAY` `THREE_D` `ENGLISH_SITE` |
| `SubGenre` | `CHINESE` `JAPANESE` `CG` `YOUTH` `OTHER` `THREE_D` `COSPLAY` |
| `RankingSpan` | `DAY` `WEEK` `MONTH` |
| `RetryMode` | `RETRY_FIRST` `ROTATE_FIRST` |
| `Backend` | `CURL_CFFI` `HTTPX` |
| `ExportFormat` | `BYTES` `BASE64` `PATH` `PDF` |

`sub_genre` 必须与 `genre` 一起用；只给副分类会抛 `ConfigurationError`。

## 异常

```
JmcpyError
├── ConfigurationError      配置或参数不合法
├── InvalidArgument         车号等入参非法（同时是 ValueError）
├── NetworkIssue            连接被重置、超时、TLS 失败等链路异常
├── ResponseInvalid         响应结构不对（可重试）
├── BadStatus               不可重试的 HTTP 状态码
├── RequestFailed           重试与换端点全部失败（failures 里有每次尝试）
├── ApiRejected             接口明确返回业务错误（带 code）
│   └── NotFound            本子/章节不存在
├── AuthRequired            该操作需要登录
├── AuthRejected            登录失败或会话失效
├── ChallengeBlocked        被反爬验证页拦截
├── RegionBlocked           出口 IP 被地区限制
├── CryptoError             签名/加解密失败
├── ParseFailed             响应拿到了但解析不出结构
└── CredentialError         会话凭据读写失败
```

## 辅助入口

```python
from jmcpy import download_chapter, download_chapter_async, Settings

# 直接拿底层函数（自定义图片源时有用）
from jmcpy.exporting import download_chapter as export

# 配置
Settings.from_env()
Settings.from_file("config.toml")
Settings.load(overrides={"timeout": 30})
```

任何实现了下面两个方法的对象都可以当作图片源传给 `download_chapter`：

```python
class MySource:
    def picture(self, chapter, index): ...  # -> Picture
    def fetch_picture(self, picture): ...  # -> bytes
```
