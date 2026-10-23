"""Decrypt a locally cached V1MMWX mini-program package and list its index.

This is an offline utility.  It does not access the network or start WeChat.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PREFIX = b"V1MMWX"
SALT = b"saltiest"
IV = b"the iv: 16 bytes"
AES_PREFIX_BYTES = 1024


def decrypt(source: Path, app_id: str) -> bytes:
    encrypted = source.read_bytes()
    if not encrypted.startswith(PREFIX):
        raise ValueError("不是 V1MMWX 加密包")
    if len(encrypted) <= len(PREFIX) + AES_PREFIX_BYTES:
        raise ValueError("包长度不足")

    key = hashlib.pbkdf2_hmac("sha1", app_id.encode(), SALT, 1000, dklen=32)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    encrypted_aes = encrypted[len(PREFIX) : len(PREFIX) + AES_PREFIX_BYTES]
    plain_aes = decryptor.update(encrypted_aes) + decryptor.finalize()
    xor_key = ord(app_id[-2])
    # V1MMWX puts one separator byte between the AES-CBC prefix and the XOR tail.
    plain_tail = bytes(byte ^ xor_key for byte in encrypted[len(PREFIX) + AES_PREFIX_BYTES + 1 :])
    plain = plain_aes + plain_tail
    if plain[0] != 0xBE:
        raise ValueError("解密后 wxapkg 魔术字不匹配；请检查 AppID")
    return plain


def index_entries(package: bytes) -> list[tuple[str, int, int]]:
    if package[13] != 0xED:
        raise ValueError("wxapkg 尾标记不匹配")
    count = int.from_bytes(package[14:18], "big")
    cursor = 18
    entries: list[tuple[str, int, int]] = []
    for _ in range(count):
        name_size = int.from_bytes(package[cursor : cursor + 4], "big")
        cursor += 4
        name = package[cursor : cursor + name_size].decode("utf-8")
        cursor += name_size
        offset = int.from_bytes(package[cursor : cursor + 4], "big")
        size = int.from_bytes(package[cursor + 4 : cursor + 8], "big")
        cursor += 8
        if offset + size > len(package):
            raise ValueError(f"索引越界：{name}")
        entries.append((name, offset, size))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    package = decrypt(args.package, args.app_id)
    entries = index_entries(package)
    print(f"decrypted_bytes={len(package)} files={len(entries)}")
    for name, offset, size in entries:
        print(f"{size:>9} {offset:>9} {name}")
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        for name, offset, size in entries:
            if any(ord(char) < 32 for char in name):
                print(f"skip_invalid_name={name!r}")
                continue
            target = (args.output / name.lstrip("/")).resolve()
            if args.output.resolve() not in target.parents:
                raise ValueError(f"非法包内路径：{name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package[offset : offset + size])


if __name__ == "__main__":
    main()
