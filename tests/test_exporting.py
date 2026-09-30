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
    encrypt_pdf,
    image_stem,
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


def test_block_count_matches_values_verified_on_live_images() -> None:
    """这三个取值来自对真实图片的接缝分析：只有按它们还原，画面才会重新连续。

    其中参与哈希的是**不含扩展名**的文件名——带上扩展名会算出 4/10，还原后画面反而错位。
    """
    assert block_count(220980, 1114751, "00005.webp") == 6
    assert block_count(220980, 1114751, "00001.webp") == 8
    assert block_count(220980, 1472715, "00001.webp") == 4


def test_block_count_ignores_extension_and_query() -> None:
    assert block_count(220980, 1114751, "00005") == block_count(220980, 1114751, "00005.webp")
    assert block_count(220980, 1114751, "00005.jpg") == block_count(220980, 1114751, "00005.webp")
    assert block_count(220980, 1114751, "00005.webp?v=1") == block_count(220980, 1114751, "00005.webp")
    assert block_count(220980, 1114751, "photos/1114751/00005.webp") == 6


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


def test_image_stem_strips_extension() -> None:
    assert image_stem("00005.webp") == "00005"
    assert image_stem("media/photos/1/00005.webp?v=2") == "00005"
    assert image_stem("noextension") == "noextension"
    assert image_stem(".hidden") == ".hidden"


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


def test_pdf_output_name_includes_chapter_id(tmp_path: Path) -> None:
    first = make_chapter(count=1)
    second = Chapter(
        chapter_id=first.chapter_id + 1,
        book_id=first.book_id,
        title=first.title,
        order=2,
        pictures=("00001.png",),
    )

    result_a = download_chapter(
        FakeSource(first, {1: image_bytes(make_image(color=(1, 2, 3)))}),
        first,
        output=ExportFormat.PDF,
        dest=tmp_path,
    )
    result_b = download_chapter(
        FakeSource(second, {1: image_bytes(make_image(color=(9, 8, 7)))}),
        second,
        output=ExportFormat.PDF,
        dest=tmp_path,
    )

    assert result_a.pdf is not None and result_b.pdf is not None
    assert result_a.pdf.name == f"JM{first.chapter_id} {first.title}.pdf"
    assert result_a.pdf != result_b.pdf, "标题相同的章节不能互相覆盖"
    assert result_a.pdf.is_file() and result_b.pdf.is_file()


def test_pdf_output_reuses_existing_file_when_not_overwriting(tmp_path: Path) -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})

    first = download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path)
    assert first.pdf is not None
    first.pdf.write_bytes(b"sentinel")

    second = download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path)
    assert second.pdf == first.pdf
    assert second.pdf.read_bytes() == b"sentinel", "overwrite=False 时复用已有文件"

    download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path, overwrite=True)
    assert first.pdf.read_bytes() != b"sentinel"


def test_download_pdf_defaults_to_full_chroma(tmp_path: Path) -> None:
    chapter = make_chapter(count=2)
    source = FakeSource(chapter, {index: image_bytes(make_gradient()) for index in range(1, 3)})

    result = download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path)

    assert result.pdf is not None
    assert first_page_sampling(result.pdf) == (1, 1), "下载链路默认也是 4:4:4"


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


def test_write_pdf_keeps_same_page_scale_for_every_page(tmp_path: Path) -> None:
    """回归：追加页曾丢掉 resolution 而退回 72 DPI，导致第一页之后页面大了一倍。"""
    from pypdf import PdfReader

    sources = []
    for index in range(3):
        path = tmp_path / f"{index}.png"
        make_image(width=844, height=1200, color=(index * 40, 10, 10)).save(path, format="PNG")
        sources.append(path)

    output = write_pdf(sources, tmp_path / "out.pdf", dpi=150.0)

    boxes = {
        (round(float(page.mediabox.width), 1), round(float(page.mediabox.height), 1))
        for page in PdfReader(output).pages
    }
    assert boxes == {(405.1, 576.0)}, "每一页都要按同一个 DPI 换算物理尺寸"


def first_page_sampling(path: Path) -> tuple[int, int]:
    """取 PDF 第一页内嵌 JPEG 的色度采样（1,1=4:4:4；2,2=4:2:0）。"""
    import io

    from pypdf import PdfReader

    with PdfReader(path) as reader:
        data = reader.pages[0].images[0].data
    layer = Image.open(io.BytesIO(data)).layer[0]
    return int(layer[1]), int(layer[2])


def test_write_pdf_subsampling_defaults_to_full_chroma(tmp_path: Path) -> None:
    """默认写出 4:4:4；要更小体积可以显式指定，或传 None 交回编码器默认。"""
    source = tmp_path / "page.png"
    make_gradient(80, 60).save(source, format="PNG")

    default_pdf = write_pdf([source], tmp_path / "default.pdf", quality=95)
    encoder_pdf = write_pdf([source], tmp_path / "encoder.pdf", quality=95, subsampling=None)
    small_pdf = write_pdf([source], tmp_path / "small.pdf", quality=95, subsampling=2)

    assert first_page_sampling(default_pdf) == (1, 1), "默认 4:4:4"
    assert first_page_sampling(encoder_pdf) == (2, 2), "None 交给编码器（4:2:0）"
    assert first_page_sampling(small_pdf) == (2, 2)
    assert small_pdf.stat().st_size < default_pdf.stat().st_size


def test_write_pdf_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="没有可写入 PDF"):
        write_pdf([], tmp_path / "out.pdf")


# --------------------------------------------------------------------------- PDF 密码
def read_pdf_password_state(path: Path) -> tuple[bool, int | None, bool]:
    """返回 (是否加密, 页数, 无密码能否读取)。"""
    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError

    reader = PdfReader(path)
    encrypted = reader.is_encrypted
    if not encrypted:
        return False, len(reader.pages), True
    try:
        pages = len(reader.pages)
    except FileNotDecryptedError:
        return True, None, False
    return True, pages, True


def test_write_pdf_with_password_requires_it_to_open(tmp_path: Path) -> None:
    from pypdf import PdfReader

    pages = []
    for index in range(3):
        path = tmp_path / f"{index}.png"
        make_image(color=(index * 40, 10, 10)).save(path, format="PNG")
        pages.append(path)

    output = write_pdf(pages, tmp_path / "locked.pdf", password="s3cret")

    encrypted, pages_without_password, readable_without_password = read_pdf_password_state(output)
    assert encrypted is True
    assert pages_without_password is None
    assert readable_without_password is False

    reader = PdfReader(output)
    assert reader.decrypt("wrong") == 0, "错误密码不应解开"
    assert reader.decrypt("s3cret") > 0
    assert len(reader.pages) == 3


def test_write_pdf_without_password_is_not_encrypted(tmp_path: Path) -> None:
    source = tmp_path / "1.png"
    make_image().save(source, format="PNG")

    output = write_pdf([source], tmp_path / "open.pdf")

    encrypted, pages, readable = read_pdf_password_state(output)
    assert (encrypted, pages, readable) == (False, 1, True)


def test_encrypt_pdf_rejects_empty_password(tmp_path: Path) -> None:
    source = tmp_path / "1.png"
    make_image().save(source, format="PNG")
    output = write_pdf([source], tmp_path / "p.pdf")

    with pytest.raises(ConfigurationError, match="密码不能为空"):
        encrypt_pdf(output, "")


def test_encrypt_pdf_accepts_unicode_password(tmp_path: Path) -> None:
    from pypdf import PdfReader

    source = tmp_path / "1.png"
    make_image().save(source, format="PNG")
    output = write_pdf([source], tmp_path / "u.pdf")

    encrypt_pdf(output, "中文密码 🔒")

    reader = PdfReader(output)
    assert reader.decrypt("中文密码 🔒") > 0


def test_download_pdf_with_password(tmp_path: Path) -> None:
    chapter = make_chapter(count=2)
    source = FakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 3)})

    result = download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path, password="let-me-in")

    assert result.pdf is not None
    assert result.encrypted is True
    encrypted, pages_without_password, readable = read_pdf_password_state(result.pdf)
    assert (encrypted, pages_without_password, readable) == (True, None, False)

    from pypdf import PdfReader

    reader = PdfReader(result.pdf)
    assert reader.decrypt("let-me-in") > 0
    assert len(reader.pages) == 2


def test_download_without_password_reports_unencrypted(tmp_path: Path) -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})

    result = download_chapter(source, chapter, output=ExportFormat.PDF, dest=tmp_path)

    assert result.encrypted is False
    assert "加密" not in str(result)


def test_password_only_makes_sense_for_pdf() -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})

    with pytest.raises(ConfigurationError, match="只对 PDF 输出有意义"):
        download_chapter(source, chapter, output=ExportFormat.BYTES, password="x")


def test_subsampling_is_validated_and_ignored_outside_pdf() -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})

    with pytest.raises(ConfigurationError, match="subsampling 只能是"):
        download_chapter(source, chapter, output=ExportFormat.PDF, dest="/tmp", subsampling=3)

    # 与 dpi 一样：非 PDF 输出忽略这个参数，不报错
    result = download_chapter(source, chapter, output=ExportFormat.BYTES, subsampling=0)
    assert len(result) == 1


def test_empty_password_is_rejected() -> None:
    chapter = make_chapter(count=1)
    source = FakeSource(chapter, {1: image_bytes(make_image())})

    with pytest.raises(ConfigurationError, match="不能为空字符串"):
        download_chapter(source, chapter, output=ExportFormat.PDF, dest="/tmp", password="")


async def test_async_download_pdf_with_password(tmp_path: Path) -> None:
    chapter = make_chapter(count=2)
    source = AsyncFakeSource(chapter, {index: image_bytes(make_image()) for index in range(1, 3)})

    result = await download_chapter_async(
        source, chapter, output=ExportFormat.PDF, dest=tmp_path, password="async-pass"
    )

    assert result.encrypted is True
    from pypdf import PdfReader

    reader = PdfReader(result.pdf)
    assert reader.decrypt("async-pass") > 0
    assert len(reader.pages) == 2
