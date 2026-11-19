"""
SQLCipher 4 database decryption.

Decrypts WeChat's encrypted SQLite databases using AES-256-CBC with
HMAC-SHA512 verification. Based on the decrypt_db.py from the
wechat-decrypt project by ylytdeng.

Credits:
  - ylytdeng/wechat-decrypt: Original C memory scanner and decryption logic
  - Zhan (盏): WeChatDB query interface, message parsing, FTS search
"""

import hashlib
import hmac as hmac_mod
import logging
import os
import struct

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80  # IV(16) + HMAC(64)
SQLITE_HDR = b"SQLite format 3\x00"
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24


def derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    """Derive HMAC key from encryption key and salt."""
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def decrypt_page(enc_key: bytes, page_data: bytes, pgno: int) -> bytes:
    """Decrypt a single database page."""
    iv = page_data[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]

    if pgno == 1:
        encrypted = page_data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytes(bytearray(SQLITE_HDR + decrypted + b"\x00" * RESERVE_SZ))
    else:
        encrypted = page_data[: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b"\x00" * RESERVE_SZ


def verify_page1(enc_key: bytes, page1_data: bytes) -> bool:
    """Verify first page HMAC to confirm the key is correct."""
    salt = page1_data[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    hmac_data = page1_data[SALT_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    stored_hmac = page1_data[PAGE_SZ - HMAC_SZ : PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def decrypt_database(db_path: str, out_path: str, enc_key_hex: str) -> int:
    """Decrypt an entire database file.

    Args:
        db_path: Path to the encrypted database file.
        out_path: Output path for the decrypted database.
        enc_key_hex: Hex-encoded encryption key (64 hex chars = 32 bytes).

    Returns:
        Number of decrypted pages, or 0 on failure.
    """
    enc_key = bytes.fromhex(enc_key_hex)
    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ

    if file_size < PAGE_SZ:
        return 0

    with open(db_path, "rb") as f:
        page1 = f.read(PAGE_SZ)

    if not verify_page1(enc_key, page1):
        return 0

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(db_path, "rb") as fin, open(out_path, "wb") as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if page:
                    page = page + b"\x00" * (PAGE_SZ - len(page))
                else:
                    break
            fout.write(decrypt_page(enc_key, page, pgno))

    return total_pages


def decrypt_wal(wal_path: str, out_path: str, enc_key_hex: str) -> int:
    """Decrypt a WAL file and patch it into the already-decrypted database."""
    if not os.path.exists(wal_path):
        return 0
    wal_size = os.path.getsize(wal_path)
    if wal_size <= WAL_HEADER_SZ:
        return 0

    enc_key = bytes.fromhex(enc_key_hex)
    patched = 0

    with open(wal_path, "rb") as wf, open(out_path, "r+b") as df:
        wal_hdr = wf.read(WAL_HEADER_SZ)
        wal_salt1 = struct.unpack(">I", wal_hdr[16:20])[0]
        wal_salt2 = struct.unpack(">I", wal_hdr[20:24])[0]
        frame_size = WAL_FRAME_HEADER_SZ + PAGE_SZ

        while wf.tell() + frame_size <= wal_size:
            fh = wf.read(WAL_FRAME_HEADER_SZ)
            if len(fh) < WAL_FRAME_HEADER_SZ:
                break
            pgno = struct.unpack(">I", fh[0:4])[0]
            frame_salt1 = struct.unpack(">I", fh[8:12])[0]
            frame_salt2 = struct.unpack(">I", fh[12:16])[0]
            ep = wf.read(PAGE_SZ)
            if len(ep) < PAGE_SZ:
                break
            if pgno == 0 or pgno > 1_000_000:
                continue
            if frame_salt1 != wal_salt1 or frame_salt2 != wal_salt2:
                continue
            dec = decrypt_page(enc_key, ep, pgno)
            df.seek((pgno - 1) * PAGE_SZ)
            df.write(dec)
            patched += 1

    return patched
