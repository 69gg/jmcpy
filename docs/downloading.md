# 下载与输出格式

## 四种交付形式

```python
from jmcpy import Client, ExportFormat

with Client() as client:
    chapter = client.get_chapter(1114751)
    client.download(chapter, output=ExportFormat.BYTES)  # list[bytes]
    client.download(chapter, output=ExportFormat.BASE64)  # list[str]
    client.download(chapter, output=ExportFormat.PATH, dest="./out")  # 落盘图片
    client.download(chapter, output=ExportFormat.PDF, dest="./out")  # 一个 PDF
```

返回值统一是 `ChapterDownload`：

| 属性 | 含义 |
| --- | --- |
| `artifacts` | 成功项，按图片序号排序 |
| `failures` | 失败项（含序号、地址、异常对象） |
| `pdf` | `PDF` 模式下的 PDF 路径，其余为 `None` |
| `destination` | 实际使用的目录 |
| `ok` / `total` / `paths` | 是否全部成功 / 应下载总数 / 已落盘路径 |
| `raise_for_failures()` | 有失败项时抛出第一个失败原因 |

`artifacts` 里每一项是 `PictureArtifact`，只有你请求的那种载体字段会被填充：

| 输出格式 | 填充的字段 |
| --- | --- |
| `BYTES` | `data: bytes` |
| `BASE64` | `text: str` |
| `PATH` | `path: Path` |
| `PDF` | 都为 `None`，成果在 `ChapterDownload.pdf`；`size` 仍是该页编码后的字节数 |

因此：

```python
result = client.download(chapter, output=ExportFormat.PDF, dest="./out")
assert result.pdf is not None
for item in result:
    print(item.index, item.suffix, item.decoded, item.size)  # decoded 表示是否做过分块还原
```

## 分块还原

服务端会把图片竖直切成若干块并打乱顺序。块数由章节号与文件名共同决定：

- 章节号小于服务端下发的阈值时：不切块；
- 小于 268850：固定 10 块；
- 否则按 `md5(章节号 + 不含扩展名的文件名)` 末位字符取模（早期为 10、新版为 8），再 `* 2 + 2`；
  参与哈希的是 `00005.webp` 里的 `00005`，扩展名不参与——这点用真实图片的接缝连续性验证过。

SDK 会自动取到阈值（`/chapter_view_template`，带缓存）并还原。需要注意：

- **不需要还原时原样输出**，不做二次有损压缩；
- 动图（按响应内容识别，而不是按文件名）不还原，原样输出；
- `decode=False` 时保存服务端原图，便于自行处理。

## 参数

```python
client.download(
    chapter,  # Chapter 对象，或直接给章节车号
    output=ExportFormat.PATH,
    dest="./out",  # PATH/PDF 必填，其它格式填了也不写文件
    decode=True,  # 是否分块还原
    concurrency=8,  # 并发下载数，默认取 Settings.concurrency
    overwrite=False,  # False 时复用已存在的同名文件
    quality=95,  # 需要重新编码时的质量（JPEG/WebP）
    dpi=150.0,  # PDF 页面分辨率
    strict=False,  # True 时只要有失败就抛异常
    on_progress=lambda done, total: print(done, total),
)
```

并发生效时**结果顺序仍然与图片顺序一致**（并发只影响下载顺序，不影响产物顺序）。

`PATH` 与 `PDF` 必须显式给 `dest`，否则抛 `ConfigurationError`——避免在意外位置写文件。

## 落盘结构与命名

```
dest/
└── 章节标题/                 # 标题为空时退化为 JM<章节号>
    ├── 0001.webp            # 按响应内容识别的真实扩展名
    ├── 0002.jpg
    └── ...
PDF 模式：
dest/
└── 章节标题.pdf
```

标题会做跨平台净化：非法字符（`<>:"/\|?*` 与控制字符）替换为下划线、折叠空白、
去掉尾随点号、超长截断。

PDF 合成是**逐页追加**写入的，同一时刻内存里只有一张图片，因此章节有几百张图也
不会把内存吃满；中间页文件放在 `dest/.jmcpy-pages-*` 临时目录里，结束后自动清理。

## 失败处理

单张失败不会打断整章，也不会被静默丢弃：

```python
result = client.download(chapter, output=ExportFormat.PATH, dest="./out")
if not result.ok:
    for failure in result.failures:
        print(failure.index, failure.url, failure.error)
    result.raise_for_failures()  # 需要的话手动抛出
```

图片请求本身已经带重试：失败时会在 CDN 端点之间切换，服务端偶发返回空响应时
会带上时间戳再试一次。

## 单张图片

```python
picture = client.picture(chapter, 3)  # 1 起算
raw: bytes = client.fetch_picture(picture)

# 换一个 CDN 端点重取
raw = client.fetch_picture(picture.with_endpoint("cdn-msp2.jmapiproxy2.cc"))
```

也可以直接遍历章节声明的地址：

```python
chapter.picture_urls("cdn-msp2.jmapiproxy2.cc")
```

## 封面

```python
client.cover_url(1114751)  # 原图
client.cover_url(1114751, size="_3x4")  # 列表页那种竖版缩略图
```
