"""签名与载荷加解密。

其中两条用例使用**真实录制的服务端响应**作为测试向量：
密文、时间戳与期望明文都取自一次真实请求，因此它们能证明密钥推导、
base64 解码与 PKCS#7 去填充这条链路与线上一致。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from jmcpy.constants import CHAPTER_TOKEN_SALT, ENDPOINT_FEED_SALT, MOBILE_PAYLOAD_SALT
from jmcpy.crypto import (
    derive_key,
    md5_hex,
    open_endpoint_feed,
    seal_payload,
    sign_request,
    strip_feed_noise,
    unseal_payload,
)
from jmcpy.errors import CryptoError

FIXTURES = Path(__file__).parent / "fixtures"


def test_md5_hex_matches_known_digest() -> None:
    assert md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72"


def test_sign_request_produces_expected_token_and_tokenparam() -> None:
    token, tokenparam = sign_request(1700000000, "2.1.9")

    # 期望值为 md5("1700000000" + "185Hcomic3PAPP7R") 的独立计算结果
    assert token == "880c64833265ad47a928afcf1b1220f5"
    assert tokenparam == "1700000000,2.1.9"


def test_sign_request_uses_chapter_salt_when_asked() -> None:
    token, _ = sign_request(1700000000, "2.1.9", CHAPTER_TOKEN_SALT)

    assert token == "8be524e958f97f014ddf2a570b011305"
    assert token != "880c64833265ad47a928afcf1b1220f5"


def test_sign_request_rejects_empty_version() -> None:
    with pytest.raises(CryptoError):
        sign_request(1700000000, "")


def test_derive_key_is_ascii_md5_of_timestamp_and_salt() -> None:
    key = derive_key(1700000000, MOBILE_PAYLOAD_SALT)
    assert key == md5_hex(f"1700000000{MOBILE_PAYLOAD_SALT}").encode("ascii")
    assert len(key) == 32


def test_unseal_real_settings_response() -> None:
    """真实响应向量：密文必须能解出服务端给出的同一份 JSON。"""
    fixture = json.loads((FIXTURES / "mobile_settings_response.json").read_text(encoding="utf-8"))

    plain = unseal_payload(fixture["payload"], fixture["ts"], fixture["salt"])

    assert plain == fixture["plaintext"]
    assert json.loads(plain)["jm3_version"]


def test_unseal_accepts_string_timestamp() -> None:
    fixture = json.loads((FIXTURES / "mobile_settings_response.json").read_text(encoding="utf-8"))

    assert unseal_payload(fixture["payload"], str(fixture["ts"])) == fixture["plaintext"]


def test_unseal_tolerates_missing_padding() -> None:
    fixture = json.loads((FIXTURES / "mobile_settings_response.json").read_text(encoding="utf-8"))
    payload = fixture["payload"].rstrip("=")

    assert unseal_payload(payload, fixture["ts"]) == fixture["plaintext"]


@pytest.mark.parametrize("text", ["", "a", "x" * 15, "x" * 16, "x" * 17, "中文内容", "🌊 emoji"])
def test_seal_and_unseal_round_trip(text: str) -> None:
    payload = seal_payload(text, 1700000000)
    assert unseal_payload(payload, 1700000000) == text


def test_seal_pads_aligned_plaintext_with_a_whole_block() -> None:
    # 明文恰好占满一个分组时，PKCS#7 必须再补一整块，否则服务端无法去填充
    assert len(base64.b64decode(seal_payload("x" * 16, 1700000000))) == 32
    assert len(base64.b64decode(seal_payload("x" * 15, 1700000000))) == 16
    assert unseal_payload(seal_payload("x" * 16, 1700000000), 1700000000) == "x" * 16


def test_unseal_rejects_wrong_timestamp() -> None:
    payload = seal_payload("内容", 1700000000)
    with pytest.raises(CryptoError, match=r"填充校验失败|UTF-8"):
        unseal_payload(payload, 1700000001)


def test_unseal_rejects_broken_base64() -> None:
    with pytest.raises(CryptoError, match="base64"):
        unseal_payload("not base64 %%%", 1700000000)


def test_unseal_rejects_empty_payload() -> None:
    with pytest.raises(CryptoError, match="密文为空"):
        unseal_payload("   ", 1700000000)


def test_unseal_rejects_unaligned_ciphertext() -> None:
    with pytest.raises(CryptoError, match="整数倍"):
        unseal_payload("YWJj", 1700000000)


def test_open_endpoint_feed_matches_recorded_payload() -> None:
    raw = (FIXTURES / "endpoint_feed.txt").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / "endpoint_feed_expected.json").read_text(encoding="utf-8"))

    payload = json.loads(open_endpoint_feed(raw))

    assert payload["Server"] == expected["Server"]
    assert payload["jm3_Server"]


def test_strip_feed_noise_skips_leading_junk() -> None:
    assert strip_feed_noise("\ufeff\n\tQUJD") == "QUJD"
    assert strip_feed_noise("QUJD") == "QUJD"
    assert strip_feed_noise("全部是噪声") == ""


def test_endpoint_feed_salt_differs_from_payload_salt() -> None:
    assert ENDPOINT_FEED_SALT != MOBILE_PAYLOAD_SALT
