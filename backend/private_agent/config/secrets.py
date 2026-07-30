"""蓝图 §2.7 API Key AES-256-GCM 加密存储。

B5.3:加密/解密 API Key,密文存入 config_runtime 表。
蓝图 §2.7:UI 录入 → AES-256-GCM 加密 → config_runtime.api_keys。
蓝图 §9.13:"API Key 加密存储(2.7):UI 录入 → AES-256-GCM 加密 → config_runtime.api_keys"。
"""
from __future__ import annotations

import base64
import os
import secrets as _stdlib_secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# GCM 推荐 nonce 长度(12 字节)
_NONCE_BYTES = 12

# master key 来源:环境变量 PA_MASTER_KEY(32 字节,hex 编码 64 字符)
_MASTER_KEY_ENV = "PA_MASTER_KEY"


def encrypt_api_key(plaintext: str, master_key: bytes) -> dict[str, str]:
    """AES-256-GCM 加密 API Key。

    Args:
        plaintext: API Key 明文。
        master_key: 32 字节主密钥(AES-256)。

    Returns:
        {"ciphertext": base64, "nonce": base64}
    """
    _validate_key(master_key)
    nonce = _stdlib_secrets.token_bytes(_NONCE_BYTES)
    aesgcm = AESGCM(master_key)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return {
        "ciphertext": base64.b64encode(ct).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
    }


def decrypt_api_key(encrypted: dict[str, str], master_key: bytes) -> str:
    """AES-256-GCM 解密 API Key。

    Args:
        encrypted: {"ciphertext": base64, "nonce": base64}
        master_key: 32 字节主密钥(必须与加密时一致)。

    Returns:
        API Key 明文。

    Raises:
        cryptography.exceptions.InvalidTag: master_key 不匹配或密文被篡改。
    """
    _validate_key(master_key)
    nonce = base64.b64decode(encrypted["nonce"])
    ct = base64.b64decode(encrypted["ciphertext"])
    aesgcm = AESGCM(master_key)
    pt = aesgcm.decrypt(nonce, ct, associated_data=None)
    return pt.decode("utf-8")


def get_master_key() -> bytes:
    """从环境变量 PA_MASTER_KEY 读取主密钥(蓝图 §2.7)。

    环境变量应为 64 字符 hex(32 字节)。

    Returns:
        32 字节主密钥。

    Raises:
        ValueError: 环境变量未设置或长度不正确。
    """
    hex_str = os.environ.get(_MASTER_KEY_ENV)
    if not hex_str:
        raise ValueError(
            f"环境变量 {_MASTER_KEY_ENV} 未设置(蓝图 §2.7 需要 64 字符 hex 主密钥)"
        )
    try:
        key = bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError(f"{_MASTER_KEY_ENV} 不是合法 hex: {e}") from e
    _validate_key(key)
    return key


def _validate_key(key: bytes) -> None:
    """校验主密钥长度为 32 字节(AES-256)。"""
    if len(key) != 32:
        raise ValueError(
            f"master_key 必须为 32 字节(AES-256),实际 {len(key)} 字节"
        )
