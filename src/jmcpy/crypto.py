"""移动端接口的请求签名与载荷加解密。

协议要点：

* 请求头 ``token = md5(f"{ts}{salt}")``，``tokenparam = f"{ts},{接口版本}"``；
* 响应体 ``data`` 是 ``base64(AES-256-ECB(PKCS#7 填充的 JSON))``，
  密钥为 ``md5(f"{ts}{salt}")`` 的 ASCII 字节（32 字节）；
* 端点源 JSON 用同一个算法，但时间戳传空串、盐值不同。

时间戳必须与本次请求头里发送的一致，否则密钥会对不上。
"""

from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .constants import ENDPOINT_FEED_SALT, MOBILE_PAYLOAD_SALT, MOBILE_TOKEN_SALT
from .errors import CryptoError

__all__ = [
    "derive_key",
    "md5_hex",
    "open_endpoint_feed",
    "seal_payload",
    "sign_request",
    "unseal_payload",
]

_BLOCK_SIZE = 16


def md5_hex(text: str) -> str:
    """返回 ``text`` 的 md5 十六进制摘要。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def derive_key(timestamp: int | str, salt: str) -> bytes:
    """推导 AES-256 密钥（32 字节 ASCII）。"""
    return md5_hex(f"{timestamp}{salt}").encode("ascii")


def sign_request(timestamp: int, version: str, salt: str = MOBILE_TOKEN_SALT) -> tuple[str, str]:
    """生成请求头 ``token`` 与 ``tokenparam``。"""
    if not version:
        raise CryptoError("接口版本不能为空，tokenparam 需要它")
    return md5_hex(f"{timestamp}{salt}"), f"{timestamp},{version}"


def _pad(data: bytes) -> bytes:
    padding = _BLOCK_SIZE - len(data) % _BLOCK_SIZE
    return data + bytes([padding]) * padding


def _unpad(data: bytes) -> bytes:
    if not data or len(data) % _BLOCK_SIZE != 0:
        raise CryptoError(f"密文长度不是 {_BLOCK_SIZE} 的整数倍: {len(data)}")
    padding = data[-1]
    if not 1 <= padding <= _BLOCK_SIZE or data[-padding:] != bytes([padding]) * padding:
        raise CryptoError("PKCS#7 填充校验失败，通常是时间戳或盐值与服务端不一致")
    return data[:-padding]


def _aes_ecb(key: bytes, data: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    if len(data) % _BLOCK_SIZE != 0:
        raise CryptoError(f"密文长度不是 {_BLOCK_SIZE} 的整数倍: {len(data)}")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _b64decode(payload: str) -> bytes:
    text = "".join(payload.split())
    if not text:
        raise CryptoError("密文为空")
    remainder = len(text) % 4
    if remainder:
        text += "=" * (4 - remainder)
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CryptoError(f"base64 解码失败: {exc}") from exc


def unseal_payload(payload: str, timestamp: int | str, salt: str = MOBILE_PAYLOAD_SALT) -> str:
    """解密接口返回的 ``data`` 字段，返回 JSON 文本。"""
    raw = _b64decode(payload)
    plain = _unpad(_aes_ecb_decrypt(derive_key(timestamp, salt), raw))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CryptoError(f"解密结果不是 UTF-8 文本: {exc}") from exc


def seal_payload(text: str, timestamp: int | str, salt: str = MOBILE_PAYLOAD_SALT) -> str:
    """加密一段文本（:func:`unseal_payload` 的逆运算）。"""
    key = derive_key(timestamp, salt)
    return base64.b64encode(_aes_ecb(key, _pad(text.encode("utf-8")))).decode("ascii")


def strip_feed_noise(text: str) -> str:
    """去掉端点源响应开头的非 base64 噪声字符。"""
    start = 0
    while start < len(text) and text[start] not in _BASE64_CHARS:
        start += 1
    return text[start:]


_BASE64_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def open_endpoint_feed(text: str) -> str:
    """解密端点源响应（时间戳为空串，盐值独立）。"""
    return unseal_payload(strip_feed_noise(text), "", ENDPOINT_FEED_SALT)
