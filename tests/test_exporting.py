"""图片解码与导出。

解扰用「按算法反向打乱再还原」的方式做像素级往返验证；
PDF 用真实的 Pillow 产物验证页数与逐页追加写入。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from jmcpy.enums import ExportFormat
from jmcpy.errors import ConfigurationError, ParseFailed
from jmcpy.exporting import download_chapter, download_chapter_async
from jmcpy.imaging import (
    block_count,
    descramble,
    detect_suffix,
    encode_image,
    load_image,
    write_pdf,
)
from jmcpy.models import Chapter, Picture
from jmcpy.models.artifacts import ChapterDownload

CDN = "cdn.example"


def count_pdf_pages(path: Path) -> int:
    """按最新一次 catalog 的 /Kids 数组统计页数（PDF 逐页追加会留下旧的页对象）。"""
    data = path.read_bytes()
    kids = re.findall(rb"/Kids\s*\[([^\]]*)\]", data)
    assert kids, "PDF 里应当有页面树"
    return len(re.findall(rb"\d+\s+\d+\s+R", kids[-1]))


def make_image(width: int = 40, height: int = 24, color: tuple[int, int, int] = (10, 20, 30)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def make_gradient(width: int = 40, height: int = 32) -> Image.Image:
    """逐行亮度递增的图，便于验证分块顺序。"""
    image = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), (y * 7 % 256, y * 3 % 256, x % 256))
    return image


def scramble_like(image: Image.Image, blocks: int) -> Image.Image:
    """按 :func:`descramble` 的逆过程打乱，用于构造测试输入。"""
    width, height = image.size
    scrambled = Image.new(image.mode, (width, height))
    move = height // blocks
    remain = height % blocks
    for index in range(blocks):
        source_y = height - move * (index + 1) - remain
        target_y = move * index
        piece_height = move
        if index == 0:
            piece_height += remain
        else:
            target_y += remain
        scrambled.paste(
            image.crop((0, target_y, width, target_y + piece_height)),
            (0, source_y, width, source_y + piece_height),
        )
    return scrambled


def image_bytes(image: Image.Image, suffix: str = ".png") -> bytes:
    return encode_image(image, suffix)


class FakeSource:
    """按序号返回图片字节的图片源。"""

    def __init__(self, chapter: Chapter, images: dict[int, bytes]) -> None:
        self.chapter = chapter
        self.images = images
        self.urls: list[str] = []

    def picture(self, chapter: Chapter, index: int) -> Picture:
        return chapter.picture(index, CDN)

    def fetch_picture(self, picture: Picture) -> bytes:
        self.urls.append(picture.url)
        payload = self.images[picture.index]
        if isinstance(payload, Exception):
            raise payload
        return payload


class AsyncFakeSource(FakeSource):
    async def fetch_picture(self, picture: Picture) -> bytes:  # type: ignore[override]
        return super().fetch_picture(picture)


def make_chapter(count: int = 3, *, scramble_id: int | None = None, title: str = "示例章节") -> Chapter:
    return Chapter(
        chapter_id=500000,
        book_id=400000,
        title=title,
        order=1,
        pictures=tuple(f"{index:05d}.png" for index in range(1, count + 1)),
        scramble_id=scramble_id,
    )


# --------------------------------------------------------------------------- 分块
@pytest.mark.parametrize(
    ("scramble_id", "chapter_id", "expected"),
    [
        (None, 500000, 0),
        (220980, 220979, 0),
        (220980, 220980, 10),
        (220980, 268849, 10),
    ],
)
def test_block_count_thresholds(scramble_id: int | None, chapter_id: int, expected: int) -> None:
    assert block_count(scramble_id, chapter_id, "00001.webp") == expected


def test_block_count_modern_range_is_even_between_2_and_20() -> None:
    for chapter_id in (268850, 421925, 421926, 900000):
        value = block_count(220980, chapter_id, "00001.webp")
        assert value % 2 == 0
        assert 2 <= value <= 20


def test_block_count_depends_on_filename() -> None:
    values = {block_count(220980, 900000, f"{index:05d}.webp") for index in range(1, 30)}

    assert len(values) > 1, "块数应当随文件名变化"


def test_descramble_round_trip_restores_pixels() -> None:
    original = make_gradient(40, 32)
    scrambled = scramble_like(original, 6)

    restored = descramble(scrambled, 6)

    assert ImageChops.difference(restored, original).getbbox() is None, "逐像素应当完全一致"


def test_descramble_is_noop_for_single_block() -> None:
    image = make_gradient(10, 10)

    assert descramble(image, 0) is image
    assert descramble(image, 1) is image


def test_descramble_skips_when_blocks_exceed_height() -> None:
    image = make_gradient(4, 3)

    assert descramble(image, 10) is image


def test_detect_suffix_from_magic_bytes() -> None:
    assert detect_suffix(image_bytes(make_image(), ".png")) == ".png"
    assert detect_suffix(image_bytes(make_image(), ".jpg")) == ".jpg"
    assert detect_suffix(image_bytes(make_image(), ".webp")) == ".webp"
    assert detect_suffix(b"not an image") is None


def test_load_image_rejects_garbage() -> None:
    with pytest.raises(ParseFailed, match="图片解码失败"):
        load_image(b"definitely not an image")


def test_encode_image_converts_alpha_for_jpeg() -> None:
    rgba = Image.new("RGBA", (8, 8), (1, 2, 3, 128))

    data = encode_image(rgba, ".jpg")

    assert detect_suffix(data) == ".jpg"


def test_encode_image_rejects_unknown_suffix() -> None:
    with pytest.raises(ConfigurationError, match="不支持的图片格式"):
        encode_image(make_image(), ".tiff")


# --------------------------------------------------------------------------- 导出
def test_bytes_output_returns_undecoded_payload(tmp_path: Path) -> None:
    chapter = make_chapter(count=2)
    source = FakeSource(chapter, {1: image_bytes(make_image()), 2: image_bytes(make_image())})

    result = download_chapter(source, chapter, output=ExportFormat.BYTES)

    assert len(result) == 2
    assert result.ok is True
    assert result.artifacts[0].data is not None
    assert detect_suffix(result.artifacts[0].data or b"") == ".png"
    assert result.artifacts[0].decoded is False
    assert result.pdf is None
    assert result[0].index == 1
    assert [item.index for item in result] == [1, 2]


def test_base64_output_matches_bytes(tmp_path: Path) -> None:
    chapter = make_chapter(count=1)
    payload = image_bytes(make_image())
    source = FakeSource(chapter, {1: payload})

    result = download_chapter(source, chapter, output=ExportFormat.BASE64)

    assert result.artifacts[0].text == base64.b64encode(payload).decode("ascii")
    assert result.artifacts[0].data is None


def test_path_output_writes_files(tmp_path: Path) -> None:
    chapter = make_chapter(count=3, title="示例 章节/标题")
    source = FakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 4)})

    result = download_chapter(source, chapter, output=ExportFormat.PATH, dest=tmp_path)

    assert result.ok is True
    names = sorted(path.name for path in result.paths)
    assert names == ["0001.png", "0002.png", "0003.png"]
    assert all(path.is_file() for path in result.paths)
    assert result.destination is not None
    assert result.destination.name == "示例 章节_标题", "目录名应当被净化"


def test_path_output_reuses_existing_file_without_overwrite(tmp_path: Path) -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})
    first = download_chapter(source, chapter, output=ExportFormat.PATH, dest=tmp_path)
    assert first.paths[0].read_bytes()

    first.paths[0].write_bytes(b"sentinel")
    download_chapter(source, chapter, output=ExportFormat.PATH, dest=tmp_path)

    assert first.paths[0].read_bytes() == b"sentinel", "默认不覆盖已存在的文件"

    download_chapter(source, chapter, output=ExportFormat.PATH, dest=tmp_path, overwrite=True)
    assert first.paths[0].read_bytes() != b"sentinel"


def test_pdf_output_has_one_page_per_image(tmp_path: Path) -> None:
    chapter = make_chapter(count=4)
    source = FakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 5)})

    result = download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path)

    assert result.pdf is not None
    assert result.pdf.is_file()
    assert count_pdf_pages(result.pdf) == 4
    assert len(result) == 4
    assert all(item.path is None and item.data is None for item in result)
    assert not list(tmp_path.glob(".jmcpy-pages-*")), "临时目录应当被清理"


def test_pdf_output_requires_dest() -> None:
    chapter = make_chapter(count=1)

    with pytest.raises(ConfigurationError, match="需要指定 dest"):
        download_chapter(FakeSource(chapter, {}), chapter, output=ExportFormat.PDF)


def test_path_output_requires_dest() -> None:
    chapter = make_chapter(count=1)

    with pytest.raises(ConfigurationError, match="需要指定 dest"):
        download_chapter(FakeSource(chapter, {}), chapter, output=ExportFormat.PATH)


def test_decode_restores_scrambled_picture(tmp_path: Path) -> None:
    original = make_gradient(40, 32)
    chapter = make_chapter(count=1, scramble_id=1)
    # 必须用实现真正采用的分块数，否则构造出的输入与还原算法不匹配
    blocks = block_count(1, chapter.chapter_id, chapter.pictures[0])
    scrambled = scramble_like(original, blocks)
    source = FakeSource(chapter, {1: image_bytes(scrambled)})

    result = download_chapter(source, chapter, output=ExportFormat.BYTES, decode=True)

    artifact = result.artifacts[0]
    assert artifact.decoded is True
    assert artifact.data is not None
    restored = load_image(artifact.data)
    assert restored.size == original.size
    # PNG 无损，还原后应当与原始像素完全一致
    assert ImageChops.difference(restored.convert("RGB"), original).getbbox() is None


def test_decode_false_keeps_server_bytes(tmp_path: Path) -> None:
    scrambled = scramble_like(make_gradient(40, 32), 6)
    payload = image_bytes(scrambled)
    chapter = make_chapter(count=1, scramble_id=1)
    source = FakeSource(chapter, {1: payload})

    result = download_chapter(source, chapter, output=ExportFormat.BYTES, decode=False)

    assert result.artifacts[0].decoded is False
    assert result.artifacts[0].data == payload, "关闭解码时应原样返回服务端字节"


def test_animated_gif_is_not_decoded() -> None:
    frames = [make_image(color=(255, 0, 0)), make_image(color=(0, 255, 0))]
    buffer = __import__("io").BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100)
    payload = buffer.getvalue()
    chapter = make_chapter(count=1, scramble_id=1)
    source = FakeSource(chapter, {1: payload})

    result = download_chapter(source, chapter, output=ExportFormat.BYTES)

    assert result.artifacts[0].suffix == ".gif"
    assert result.artifacts[0].decoded is False
    assert result.artifacts[0].data == payload


def test_failures_are_collected_not_raised(tmp_path: Path) -> None:
    chapter = make_chapter(count=3)
    source = FakeSource(
        chapter,
        {1: image_bytes(make_image()), 2: RuntimeError("这张挂了"), 3: image_bytes(make_image())},
    )

    result = download_chapter(source, chapter, output=ExportFormat.BYTES, concurrency=1)

    assert result.ok is False
    assert result.total == 3
    assert len(result.artifacts) == 2
    assert result.failures[0].index == 2
    assert "这张挂了" in str(result.failures[0])
    assert result.failures[0].url.endswith("/00002.png")


def test_strict_mode_raises_first_failure() -> None:
    chapter = make_chapter(count=2)
    source = FakeSource(chapter, {1: image_bytes(make_image()), 2: RuntimeError("失败")})

    with pytest.raises(RuntimeError, match="失败"):
        download_chapter(source, chapter, output=ExportFormat.BYTES, strict=True, concurrency=1)


def test_concurrency_keeps_order_and_reports_progress() -> None:
    chapter = make_chapter(count=6)
    source = FakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 7)})
    seen: list[tuple[int, int]] = []

    result = download_chapter(
        source,
        chapter,
        output=ExportFormat.BYTES,
        concurrency=3,
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert [item.index for item in result] == [1, 2, 3, 4, 5, 6], "并发也要保持顺序"
    assert len(seen) == 6
    assert seen[-1] == (6, 6)


def test_invalid_options() -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})

    with pytest.raises(ConfigurationError, match="concurrency"):
        download_chapter(source, chapter, output=ExportFormat.BYTES, concurrency=0)
    with pytest.raises(ConfigurationError, match="quality"):
        download_chapter(source, chapter, output=ExportFormat.BYTES, quality=0)


def test_empty_chapter_pdf_is_not_created(tmp_path: Path) -> None:
    chapter = make_chapter(count=0)

    result = download_chapter(FakeSource(chapter, {}), chapter, output=ExportFormat.PDF, dest=tmp_path)

    assert result.pdf is None
    assert len(result) == 0
    assert result.ok is True


def test_chapter_download_helpers() -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})
    result = download_chapter(source, chapter, output=ExportFormat.BYTES)

    assert "章节 500000" in str(result)
    assert result.raise_for_failures() is None
    assert isinstance(result, ChapterDownload)


async def test_async_download_matches_sync(tmp_path: Path) -> None:
    chapter = make_chapter(count=3)
    source = AsyncFakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 4)})

    result = await download_chapter_async(source, chapter, output=ExportFormat.PATH, dest=tmp_path, concurrency=2)

    assert sorted(path.name for path in result.paths) == ["0001.png", "0002.png", "0003.png"]


async def test_async_download_collects_failures() -> None:
    chapter = make_chapter(count=2)
    source = AsyncFakeSource(chapter, {1: image_bytes(make_image()), 2: RuntimeError("异步失败")})

    result = await download_chapter_async(source, chapter, output=ExportFormat.BYTES)

    assert len(result.artifacts) == 1
    assert "异步失败" in str(result.failures[0])


async def test_async_pdf_output(tmp_path: Path) -> None:
    chapter = make_chapter(count=2)
    source = AsyncFakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 3)})

    result = await download_chapter_async(source, chapter, output=ExportFormat.PDF, dest=tmp_path)

    assert result.pdf is not None
    assert count_pdf_pages(result.pdf) == 2


# --------------------------------------------------------------------------- PDF 工具
def test_write_pdf_appends_pages_from_files(tmp_path: Path) -> None:
    sources = []
    for index, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)], start=1):
        path = tmp_path / f"{index}.png"
        make_image(color=color).save(path, format="PNG")
        sources.append(path)

    output = write_pdf(sources, tmp_path / "out.pdf", dpi=150.0)

    assert count_pdf_pages(output) == 3


def test_write_pdf_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="没有可写入 PDF"):
        write_pdf([], tmp_path / "out.pdf")
