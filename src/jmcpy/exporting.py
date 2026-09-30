"""下载与导出：把章节图片取回来、按需分块还原，再按要求的格式交付。

四种交付形式（:class:`~jmcpy.enums.ExportFormat`）：

``BYTES``
    内存里的图片字节（需要还原时才重新编码，否则原样返回）。
``BASE64``
    上述字节的 base64 文本，便于塞进 JSON 或网页。
``PATH``
    落盘的图片文件，文件名形如 ``0001.webp``，放在 ``dest/章节名/`` 下。
``PDF``
    整章合成一个 PDF；逐页追加写入，内存占用与图片数量无关。

并发、是否还原、是否覆盖、编码质量、PDF 分辨率都可配。单张失败只记入
:attr:`~jmcpy.models.ChapterDownload.failures`，不影响其余图片。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .constants import DEFAULT_CONCURRENCY, UNDECODED_SUFFIXES
from .enums import ExportFormat
from .errors import ConfigurationError
from .imaging import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_PDF_DPI,
    block_count,
    descramble,
    detect_suffix,
    encode_image,
    load_image,
    single_frame,
    write_pdf,
)
from .models import Chapter, Picture
from .models.artifacts import ChapterDownload, DownloadFailure, PictureArtifact
from .texts import sanitize_filename

logger = logging.getLogger(__name__)

__all__ = [
    "AsyncPictureSource",
    "PictureSource",
    "ProgressHook",
    "download_chapter",
    "download_chapter_async",
]

ProgressHook = Callable[[int, int], None]


class PictureSource(Protocol):
    """图片获取来源（同步）。:class:`~jmcpy.clients.mobile.MobileClient` 天然满足。"""

    def picture(self, chapter: Chapter, index: int) -> Picture:
        """按章节与序号构造图片定位（含当前 CDN 端点）。"""

    def fetch_picture(self, picture: Picture) -> bytes:
        """取回图片原始字节。"""


class AsyncPictureSource(Protocol):
    """图片获取来源（异步）。"""

    def picture(self, chapter: Chapter, index: int) -> Picture:
        """按章节与序号构造图片定位。"""

    async def fetch_picture(self, picture: Picture) -> bytes:
        """取回图片原始字节。"""


@dataclass(frozen=True, slots=True)
class _Plan:
    """一次下载的全部决策结果。"""

    output: ExportFormat
    #: PATH 下是章节目录；PDF 下是 PDF 所在目录；其余为 None
    work_dir: Path | None
    decode: bool
    concurrency: int
    overwrite: bool
    quality: int
    dpi: float
    strict: bool
    #: PDF 打开密码；``None`` 表示不加密
    password: str | None = None
    #: PDF 页内 JPEG 的色度采样；``None`` 交给编码器
    subsampling: int | None = None

    @property
    def page_suffix(self) -> str:
        return ".png"


def _make_plan(
    output: ExportFormat,
    dest: str | os.PathLike[str] | None,
    decode: bool,
    concurrency: int | None,
    overwrite: bool,
    quality: int,
    dpi: float,
    strict: bool,
    chapter: Chapter,
    password: str | None = None,
    subsampling: int | None = None,
) -> _Plan:
    if concurrency is not None and concurrency < 1:
        raise ConfigurationError("concurrency 至少为 1")
    if quality < 1 or quality > 100:
        raise ConfigurationError("quality 需落在 1..100")
    if password is not None:
        if output is not ExportFormat.PDF:
            raise ConfigurationError(f"password 只对 PDF 输出有意义，当前输出格式是 {output.value}")
        if not password:
            raise ConfigurationError("PDF 密码不能为空字符串；不需要加密就不要传 password")
    if subsampling is not None:
        if output is not ExportFormat.PDF:
            raise ConfigurationError(f"subsampling 只对 PDF 输出有意义，当前输出格式是 {output.value}")
        if subsampling not in {0, 1, 2}:
            raise ConfigurationError("subsampling 只能是 0（4:4:4）、1（4:2:2）或 2（4:2:0）")

    target = Path(dest) if dest is not None else None
    if output in {ExportFormat.PATH, ExportFormat.PDF}:
        if target is None:
            raise ConfigurationError(f"{output.value} 输出需要指定 dest（保存目录）")
        target = target / chapter_dir_name(chapter) if output is ExportFormat.PATH else target
        target.mkdir(parents=True, exist_ok=True)

    return _Plan(
        output=output,
        work_dir=target,
        decode=decode,
        concurrency=concurrency if concurrency is not None else DEFAULT_CONCURRENCY,
        overwrite=overwrite,
        quality=quality,
        dpi=dpi,
        strict=strict,
        password=password,
        subsampling=subsampling,
    )


def chapter_dir_name(chapter: Chapter) -> str:
    """章节在磁盘上的目录名（标题净化后退化为车号）。"""
    return sanitize_filename(chapter.title) or f"JM{chapter.chapter_id}"


def chapter_pdf_name(chapter: Chapter) -> str:
    """章节 PDF 的文件名。

    带上章节号：标题相同的两个章节不会再互相覆盖，同一目录下也能看出是哪一章。
    """
    title = sanitize_filename(chapter.title)
    stem = f"JM{chapter.chapter_id} {title}".strip() if title else f"JM{chapter.chapter_id}"
    return f"{stem}.pdf"


@contextmanager
def _page_workspace(plan: _Plan) -> Iterator[Path | None]:
    """PDF 模式下提供一个存放中间页图片的临时目录，退出时自动清理。"""
    if plan.output is not ExportFormat.PDF or plan.work_dir is None:
        yield None
        return

    with tempfile.TemporaryDirectory(prefix=".jmcpy-pages-", dir=plan.work_dir) as directory:
        yield Path(directory)


def _decide_decode(data: bytes, picture: Picture, *, decode: bool) -> tuple[str, int]:
    """决定实际的扩展名与要还原的块数。

    以**响应内容**识别出的格式为准：章节声明的文件名偶尔与实际内容不符，
    动图（按内容判定）不做分块还原。
    """
    suffix = detect_suffix(data) or picture.suffix or ".jpg"
    if not decode or suffix in UNDECODED_SUFFIXES:
        return suffix, 0
    return suffix, block_count(picture.scramble_id, picture.chapter_id, picture.filename)


def _prepare_bytes(data: bytes, picture: Picture, *, decode: bool, quality: int) -> tuple[bytes, str, bool]:
    """按需还原并编码，返回 ``(字节, 扩展名, 是否还原过)``。"""
    suffix, blocks = _decide_decode(data, picture, decode=decode)
    if blocks <= 1:
        # 无需还原时保持原始字节，避免二次有损压缩
        return data, suffix, False

    image = descramble(load_image(data), blocks)
    return encode_image(image, suffix, quality=quality), suffix, True


def _to_artifact(
    picture: Picture,
    index: int,
    data: bytes,
    plan: _Plan,
    page_dir: Path | None,
) -> PictureArtifact:
    """把取回的字节按输出格式变成产物（同步/异步共用）。"""
    if plan.output is ExportFormat.PDF:
        if page_dir is None:  # pragma: no cover - 由 _page_workspace 保证
            raise ConfigurationError("PDF 输出缺少临时目录")
        suffix, blocks = _decide_decode(data, picture, decode=plan.decode)
        image = single_frame(load_image(data))
        decoded = blocks > 1
        if decoded:
            image = descramble(image, blocks)
        page_path = page_dir / f"{index:05d}{plan.page_suffix}"
        image.save(page_path, format="PNG")
        return PictureArtifact(
            index=index,
            picture=picture,
            suffix=suffix,
            decoded=decoded,
            size=page_path.stat().st_size,
        )

    processed, suffix, decoded = _prepare_bytes(data, picture, decode=plan.decode, quality=plan.quality)
    if plan.output is ExportFormat.BYTES:
        return PictureArtifact(
            index=index, picture=picture, suffix=suffix, decoded=decoded, size=len(processed), data=processed
        )
    if plan.output is ExportFormat.BASE64:
        return PictureArtifact(
            index=index,
            picture=picture,
            suffix=suffix,
            decoded=decoded,
            size=len(processed),
            text=base64.b64encode(processed).decode("ascii"),
        )

    if plan.work_dir is None:  # pragma: no cover - 由 _make_plan 保证
        raise ConfigurationError("PATH 输出缺少目标目录")
    target = plan.work_dir / f"{index:04d}{suffix}"
    if plan.overwrite or not target.exists():
        target.write_bytes(processed)
    return PictureArtifact(
        index=index, picture=picture, suffix=suffix, decoded=decoded, size=len(processed), path=target
    )


def _failure(
    source: PictureSource | AsyncPictureSource, chapter: Chapter, index: int, error: Exception
) -> DownloadFailure:
    """记录失败；尽力带上图片地址。"""
    try:
        picture: Picture | None = source.picture(chapter, index)
    except Exception:  # pragma: no cover - 构造失败时只记序号
        picture = None
    return DownloadFailure(index=index, url=picture.url if picture else "", error=error, picture=picture)


def _finalize(
    chapter: Chapter,
    plan: _Plan,
    artifacts: list[PictureArtifact],
    failures: list[DownloadFailure],
    page_dir: Path | None,
) -> ChapterDownload:
    pdf_path: Path | None = None
    if plan.output is ExportFormat.PDF and artifacts and page_dir is not None and plan.work_dir is not None:
        pdf_path = plan.work_dir / chapter_pdf_name(chapter)
        if plan.overwrite or not pdf_path.exists():
            write_pdf(
                [page_dir / f"{item.index:05d}{plan.page_suffix}" for item in artifacts],
                pdf_path,
                dpi=plan.dpi,
                quality=plan.quality,
                password=plan.password,
                subsampling=plan.subsampling,
            )

    result = ChapterDownload(
        chapter_id=chapter.chapter_id,
        title=chapter.title or f"JM{chapter.chapter_id}",
        artifacts=tuple(artifacts),
        failures=tuple(failures),
        pdf=pdf_path,
        destination=plan.work_dir,
        encrypted=plan.password is not None,
    )
    if plan.strict:
        result.raise_for_failures()
    return result


def download_chapter(
    source: PictureSource,
    chapter: Chapter,
    *,
    output: ExportFormat = ExportFormat.PATH,
    dest: str | os.PathLike[str] | None = None,
    decode: bool = True,
    concurrency: int | None = None,
    overwrite: bool = False,
    quality: int = DEFAULT_JPEG_QUALITY,
    dpi: float = DEFAULT_PDF_DPI,
    strict: bool = False,
    password: str | None = None,
    subsampling: int | None = None,
    on_progress: ProgressHook | None = None,
) -> ChapterDownload:
    """下载一个章节并按 ``output`` 交付。

    :param output: 交付形式（bytes / base64 / 路径 / PDF）
    :param dest: 保存目录；``PATH`` 与 ``PDF`` 必填
    :param decode: 是否做分块还原（``False`` 时保存服务端原图）
    :param concurrency: 并发下载数，默认取配置里的 ``concurrency``
    :param overwrite: 已存在的文件是否覆盖（``False`` 时复用已有文件）
    :param strict: 有失败项时是否直接抛异常
    :param password: 给 PDF 加打开密码（仅 ``PDF`` 输出可用；默认 AES-256）
    :param subsampling: PDF 页内 JPEG 的色度采样（仅 ``PDF`` 输出可用）
    :param on_progress: 进度回调 ``(已完成, 总数)``
    """
    plan = _make_plan(
        output, dest, decode, concurrency, overwrite, quality, dpi, strict, chapter, password, subsampling
    )
    indices = range(1, len(chapter.pictures) + 1)
    artifacts: list[PictureArtifact] = []
    failures: list[DownloadFailure] = []
    completed = 0

    with _page_workspace(plan) as page_dir:

        def fetch(index: int) -> PictureArtifact:
            picture = source.picture(chapter, index)
            return _to_artifact(picture, index, source.fetch_picture(picture), plan, page_dir)

        if plan.concurrency > 1 and len(chapter.pictures) > 1:
            with ThreadPoolExecutor(max_workers=plan.concurrency) as pool:
                futures = {index: pool.submit(fetch, index) for index in indices}
                for index in indices:
                    try:
                        artifacts.append(futures[index].result())
                    except Exception as exc:
                        failures.append(_failure(source, chapter, index, exc))
                    completed += 1
                    if on_progress is not None:
                        on_progress(completed, len(chapter.pictures))
        else:
            for index in indices:
                try:
                    artifacts.append(fetch(index))
                except Exception as exc:
                    failures.append(_failure(source, chapter, index, exc))
                completed += 1
                if on_progress is not None:
                    on_progress(completed, len(chapter.pictures))

        return _finalize(chapter, plan, artifacts, failures, page_dir)


async def download_chapter_async(
    source: AsyncPictureSource,
    chapter: Chapter,
    *,
    output: ExportFormat = ExportFormat.PATH,
    dest: str | os.PathLike[str] | None = None,
    decode: bool = True,
    concurrency: int | None = None,
    overwrite: bool = False,
    quality: int = DEFAULT_JPEG_QUALITY,
    dpi: float = DEFAULT_PDF_DPI,
    strict: bool = False,
    password: str | None = None,
    subsampling: int | None = None,
    on_progress: ProgressHook | None = None,
) -> ChapterDownload:
    """异步版的 :func:`download_chapter`（并发用信号量控制）。"""
    plan = _make_plan(
        output, dest, decode, concurrency, overwrite, quality, dpi, strict, chapter, password, subsampling
    )
    indices = list(range(1, len(chapter.pictures) + 1))
    artifacts: list[PictureArtifact] = []
    failures: list[DownloadFailure] = []
    completed = 0

    with _page_workspace(plan) as page_dir:
        semaphore = asyncio.Semaphore(plan.concurrency)

        async def guarded(index: int) -> tuple[int, PictureArtifact | Exception]:
            async with semaphore:
                try:
                    picture = source.picture(chapter, index)
                    data = await source.fetch_picture(picture)
                    return index, _to_artifact(picture, index, data, plan, page_dir)
                except Exception as exc:  # 单张失败不打断整章
                    return index, exc

        results = await asyncio.gather(*(guarded(index) for index in indices))
        for index, outcome in sorted(results, key=lambda item: item[0]):
            if isinstance(outcome, Exception):
                failures.append(_failure(source, chapter, index, outcome))
            else:
                artifacts.append(outcome)
            completed += 1
            if on_progress is not None:
                on_progress(completed, len(chapter.pictures))

        return _finalize(chapter, plan, artifacts, failures, page_dir)
