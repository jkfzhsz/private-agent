"""B5.3 - API Key AES-256-GCM 加密存储。

Source: plan/m0-implementation step 5 (蓝图 §2.7 + §9.13)

蓝图 §2.7:UI 录入 → AES-256-GCM 加密 → config_runtime.api_keys。
蓝图 §9.13:"API Key 加密存储(2.7):UI 录入 → AES-256-GCM 加密 → config_runtime.api_keys"。
"""
import base64

import pytest

from private_agent.config import secrets


# 32 字节 master key(AES-256),测试用固定值
MASTER_KEY = b"0" * 32


def test_encrypt_decrypt_roundtrip():
    """加密后解密还原明文。"""
    plaintext = "sk-test-api-key-12345"
    encrypted = secrets.encrypt_api_key(plaintext, MASTER_KEY)
    decrypted = secrets.decrypt_api_key(encrypted, MASTER_KEY)
    assert decrypted == plaintext


def test_ciphertext_differs_from_plaintext():
    """密文(base64)不等于明文。"""
    plaintext = "sk-test-api-key-12345"
    encrypted = secrets.encrypt_api_key(plaintext, MASTER_KEY)
    # ciphertext 是 base64 编码,不应包含明文
    decoded = base64.b64decode(encrypted["ciphertext"])
    assert plaintext.encode() not in decoded
    assert plaintext != encrypted["ciphertext"]


def test_encrypted_payload_has_nonce_and_ciphertext():
    """加密结果包含 nonce 和 ciphertext 两个 base64 字段。"""
    encrypted = secrets.encrypt_api_key("sk-test", MASTER_KEY)
    assert "nonce" in encrypted
    assert "ciphertext" in encrypted
    # nonce 解码后应为 12 字节(GCM 标准)
    nonce = base64.b64decode(encrypted["nonce"])
    assert len(nonce) == 12


def test_decrypt_with_wrong_key_raises():
    """错误 master key 解密抛异常(认证失败)。"""
    plaintext = "sk-test-api-key-12345"
    encrypted = secrets.encrypt_api_key(plaintext, MASTER_KEY)
    wrong_key = b"1" * 32
    with pytest.raises(Exception):
        secrets.decrypt_api_key(encrypted, wrong_key)
