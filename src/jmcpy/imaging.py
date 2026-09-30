"""图片处理：分块还原（解扰）、格式识别、编码与 PDF 合成。

服务端会把图片按竖直方向切成若干块并打乱顺序，块数由 ``scramble_id`` 与章节号、
文件名共同决定（见 :func:`block_count`）。还原就是把块顺序反过来拼回原位。
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from .constants import SCRAMBLE_LEGACY_LIMIT, SCRAMBLE_MODERN_LIMIT
from .crypto import md5_hex
from .errors import ConfigurationError, ParseFailed

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_PDF_DPI",
    "block_count",
    "descramble",
    "detect_suffix",
    "encode_image",
    "image_stem",
    "load_image",
    "write_pdf",
]

#: 解扰后重新编码时的质量
DEFAULT_JPEG_QUALITY = 95
#: PDF 页面分辨率
DEFAULT_PDF_DPI = 150.0
#: PDF 加密算法（AES-256 需要 PDF 1.7 扩展级别 3，现代阅读器都支持）
DEFAULT_PDF_ALGORITHM = "AES-256"

_IMAGE_SAVE_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".gif": "GIF",
}

_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def image_stem(filename: str) -> str:
    """取出图片名（**不含扩展名**），分块数按它计算。"""
    name = filename.split("?", 1)[0].rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def block_count(scramble_id: int | None, chapter_id: int, filename: str) -> int:
    """返回该图片被切成的块数；``0`` 表示无需还原。

    规则（与线上行为一致）：

    * ``chapter_id < scramble_id``：未启用切割；
    * ``chapter_id < 268850``：固定切 10 块；
    * 否则按 ``md5(f"{chapter_id}{不含扩展名的文件名}")`` 末位字符取模：
      2023-02-08 之前（``< 421926``）取模 10，之后取模 8，再 ``* 2 + 2``。

    注意参与哈希的是**不含扩展名**的文件名（``00005.webp`` → ``00005``）。
    这一点用真实图片验证过：带上扩展名时算出的块数无法还原出连续的画面。
    """
    if not scramble_id:
        return 0
    if chapter_id < int(scramble_id):
        return 0
    if chapter_id < SCRAMBLE_LEGACY_LIMIT:
        return 10

    modulus = 10 if chapter_id < SCRAMBLE_MODERN_LIMIT else 8
    digest = md5_hex(f"{chapter_id}{image_stem(filename)}")
    return (ord(digest[-1]) % modulus) * 2 + 2


def descramble(image: Image.Image, blocks: int) -> Image.Image:
    """把竖直分块的图片还原成正常顺序。

    ``blocks <= 1`` 时原样返回。
    """
    if blocks <= 1:
        return image

    width, height = image.size
    if blocks > height:
        # 块数大于像素高度时无法还原（服务端不会这样发），保持原图更安全
        logger.warning("分块数 %s 超过图片高度 %s，跳过还原", blocks, height)
        return image

    restored = Image.new(image.mode, (width, height))
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

        restored.paste(
            image.crop((0, source_y, width, source_y + piece_height)),
            (0, target_y, width, target_y + piece_height),
        )

    return restored


def detect_suffix(data: bytes) -> str | None:
    """按文件头判断图片格式，返回小写扩展名（含点）。"""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for magic, suffix in _MAGIC:
        if data.startswith(magic):
            return suffix
    return None


def load_image(data: bytes) -> Image.Image:
    """把字节解码成图片。"""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # Pillow 的解码异常类型不统一
        raise ParseFailed(f"图片解码失败: {type(exc).__name__}: {exc}") from exc
    return image


def encode_image(image: Image.Image, suffix: str, *, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """把图片编码成指定格式的字节。"""
    if suffix == ".gif" and getattr(image, "n_frames", 1) > 1:
        # 动图逐帧重编码成本高且无收益，交给调用方保留原字节
        raise ConfigurationError("动图不支持重新编码，请直接保留原始字节")

    fmt = _IMAGE_SAVE_FORMATS.get(suffix)
    if fmt is None:
        raise ConfigurationError(f"不支持的图片格式: {suffix}")

    target = image
    if fmt == "JPEG" and image.mode not in {"RGB", "L"}:
        target = image.convert("RGB")

    buffer = io.BytesIO()
    if fmt in {"JPEG", "WEBP"}:
        target.save(buffer, format=fmt, quality=quality)
    else:
        target.save(buffer, format=fmt)
    return buffer.getvalue()


def _normalize_for_pdf(image: Image.Image) -> Image.Image:
    """PDF 只稳定支持这几种颜色模式，其余统一转 RGB。"""
    if image.mode in {"1", "L", "RGB", "CMYK"}:
        return image
    return image.convert("RGB")


def write_pdf(
    sources: Sequence[Path],
    output: Path,
    *,
    dpi: float = DEFAULT_PDF_DPI,
    quality: int = DEFAULT_JPEG_QUALITY,
    password: str | None = None,
) -> Path:
    """把若干图片按顺序合成一个 PDF，可选加打开密码。

    逐页追加写入，因此合成阶段同一时刻内存里只有一张图片——章节图片动辄上百张，
    一次性全部载入容易把内存打满。加密是合成之后的单独一遍（见 :func:`encrypt_pdf`）。
    """
    if not sources:
        raise ConfigurationError("没有可写入 PDF 的图片")

    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w+b") as handle, Image.open(sources[0]) as image:
        _normalize_for_pdf(image).save(handle, "PDF", resolution=dpi, quality=quality, save_all=True)

    for source in sources[1:]:
        with open(output, "r+b") as handle, Image.open(source) as image:
            _normalize_for_pdf(image).save(handle, "PDF", append=True, quality=quality)

    if password is not None:
        encrypt_pdf(output, password)

    return output


def encrypt_pdf(path: Path, password: str, *, algorithm: str = DEFAULT_PDF_ALGORITHM) -> Path:
    """就地给 PDF 加上打开密码（默认 AES-256），返回同一个路径。

    加密后打开文件必须提供密码；权限位放开，打开后可以正常阅读/打印/复制。
    密码为空会直接报错——想不加密就不要传 password，避免「以为加了其实没加」。
    """
    if not password:
        raise ConfigurationError("PDF 密码不能为空；不需要加密就不要传 password")

    # pypdf 的导入成本约 70ms，只有真的要加密时才付这个代价
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=password, algorithm=algorithm)

    temporary = path.with_name(path.name + ".encrypting")
    try:
        with open(temporary, "wb") as handle:
            writer.write(handle)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def single_frame(image: Image.Image) -> Image.Image:
    """取出首帧（动图合成 PDF 时只保留第一帧）。"""
    if getattr(image, "n_frames", 1) <= 1:
        return image
    image.seek(0)
    return image
