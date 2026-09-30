"""下载产物模型。

下载是「按格式取结果」的：调用方先声明要什么（bytes / base64 / 文件路径 / PDF），
拿到 :class:`ChapterDownload` 后按同样的约定去读对应字段。
失败项单独收集在 :attr:`ChapterDownload.failures` 里，不会被静默丢掉。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .detail import Picture

__all__ = ["ChapterDownload", "DownloadFailure", "PictureArtifact"]


@dataclass(frozen=True, slots=True)
class PictureArtifact:
    """一张图片的处理结果。

    只有调用方请求的那种载体字段会被填充：

    * ``ExportFormat.BYTES`` → :attr:`data`
    * ``ExportFormat.BASE64`` → :attr:`text`
    * ``ExportFormat.PATH`` → :attr:`path`
    * ``ExportFormat.PDF`` → 三者都为 ``None``，整章的 PDF 在
      :attr:`ChapterDownload.pdf`；:attr:`size` 仍然给出编码后的字节数
    """

    index: int
    picture: Picture
    #: 实际使用的扩展名（按响应内容识别，可能与该章节声明的文件名不同）
    suffix: str
    #: 是否做过分块还原
    decoded: bool
    #: 产物的字节数
    size: int | None = None
    data: bytes | None = None
    text: str | None = None
    path: Path | None = None

    def __str__(self) -> str:
        return f"#{self.index} {self.suffix} decoded={self.decoded} size={self.size}"


@dataclass(frozen=True, slots=True)
class DownloadFailure:
    """一张图片的失败记录。"""

    index: int
    url: str
    error: Exception
    picture: Picture | None = None

    def __str__(self) -> str:
        return f"#{self.index} {self.url} 失败: {type(self.error).__name__}: {self.error}"


@dataclass(frozen=True, slots=True)
class ChapterDownload:
    """一个章节的下载结果。"""

    chapter_id: int
    title: str
    artifacts: tuple[PictureArtifact, ...] = ()
    failures: tuple[DownloadFailure, ...] = ()
    pdf: Path | None = None
    destination: Path | None = field(default=None)
    #: PDF 是否加了打开密码（只记录状态，不保存密码本身）
    encrypted: bool = False

    def __len__(self) -> int:
        return len(self.artifacts)

    def __iter__(self) -> Iterator[PictureArtifact]:
        return iter(self.artifacts)

    def __getitem__(self, index: int) -> PictureArtifact:
        return self.artifacts[index]

    @property
    def ok(self) -> bool:
        """是否全部成功。"""
        return not self.failures

    @property
    def total(self) -> int:
        """应下载的图片总数（成功 + 失败）。"""
        return len(self.artifacts) + len(self.failures)

    @property
    def paths(self) -> tuple[Path, ...]:
        """已落盘的文件路径（``ExportFormat.PATH`` 下）。"""
        return tuple(item.path for item in self.artifacts if item.path is not None)

    def raise_for_failures(self) -> None:
        """有失败项时抛出第一个失败原因。"""
        if self.failures:
            raise self.failures[0].error

    def __str__(self) -> str:
        state = "全部成功" if self.ok else f"{len(self.failures)} 张失败"
        suffix = "，PDF 已加密" if self.encrypted else ""
        return f"章节 {self.chapter_id} 下载完成: {len(self.artifacts)}/{self.total} 张，{state}{suffix}"
