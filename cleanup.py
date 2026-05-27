#!/usr/bin/env python3
"""
WeChat MCP 缓存清理
定期清理解密数据库临时文件、过期日志等。

用法:
    python3 cleanup.py                # 清理所有缓存
    python3 cleanup.py --check        # 只检查大小，不清理
    python3 cleanup.py --decrypted    # 只清理解密数据库
    python3 cleanup.py --logs         # 只清理日志
    python3 cleanup.py --days 7       # 清理 7 天前的文件
"""

import argparse
import os
import shutil
import time
from pathlib import Path

MCP_DIR = Path.home() / ".wechat-mcp"
DECRYPTED_DIR = MCP_DIR / "decrypted"
LOGS_DIR = MCP_DIR / "logs"

def get_dir_size(path):
    """获取目录大小（字节）"""
    total = 0
    if path.is_file():
        return path.stat().st_size
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total

def format_size(bytes):
    """格式化文件大小"""
    if bytes < 1024:
        return f"{bytes} B"
    elif bytes < 1024 * 1024:
        return f"{bytes / 1024:.1f} KB"
    elif bytes < 1024 * 1024 * 1024:
        return f"{bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{bytes / 1024 / 1024 / 1024:.1f} GB"

def cleanup_decrypted(days=7):
    """清理过期的解密数据库"""
    if not DECRYPTED_DIR.exists():
        return 0

    cleaned = 0
    cutoff = time.time() - days * 86400

    for f in DECRYPTED_DIR.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            size = f.stat().st_size
            f.unlink()
            cleaned += 1

    # 清理空目录
    for d in sorted(DECRYPTED_DIR.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()

    return cleaned

def cleanup_logs(days=30):
    """清理过期日志"""
    if not LOGS_DIR.exists():
        return 0

    cleaned = 0
    cutoff = time.time() - days * 86400

    for f in LOGS_DIR.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            cleaned += 1

    return cleaned

def cleanup_all_keys_cache():
    """清理旧格式的密钥缓存（如果有）"""
    # 保留 all_keys.json，清理其他临时密钥文件
    cleaned = 0
    for f in MCP_DIR.glob("*.json.bak"):
        f.unlink()
        cleaned += 1
    return cleaned

def main():
    parser = argparse.ArgumentParser(description="WeChat MCP 缓存清理")
    parser.add_argument("--check", action="store_true", help="只检查大小")
    parser.add_argument("--decrypted", action="store_true", help="只清理解密数据库")
    parser.add_argument("--logs", action="store_true", help="只清理日志")
    parser.add_argument("--days", type=int, default=7, help="清理 N 天前的文件")
    args = parser.parse_args()

    print("=== WeChat MCP 缓存清理 ===")
    print()

    # 检查当前大小
    total_before = 0
    for name, path in [("解密数据库", DECRYPTED_DIR), ("日志", LOGS_DIR)]:
        if path.exists():
            size = get_dir_size(path)
            total_before += size
            print(f"📁 {name}: {format_size(size)}")
        else:
            print(f"📁 {name}: 不存在")

    # MCP 配置文件
    config_size = 0
    for f in MCP_DIR.glob("*.json"):
        config_size += f.stat().st_size
    print(f"📁 配置文件: {format_size(config_size)}")
    total_before += config_size

    print(f"\n📊 总计: {format_size(total_before)}")
    print()

    if args.check:
        print("仅检查模式，未清理任何文件")
        return

    # 执行清理
    cleaned_files = 0
    cleaned_bytes = 0

    do_all = not args.decrypted and not args.logs

    if do_all or args.decrypted:
        n = cleanup_decrypted(args.days)
        cleaned_files += n
        print(f"🧹 解密数据库: 清理 {n} 个过期文件 (>{args.days}天)")

    if do_all or args.logs:
        n = cleanup_logs(args.days)
        cleaned_files += n
        print(f"🧹 日志: 清理 {n} 个过期文件 (>{args.days}天)")

    if do_all:
        n = cleanup_all_keys_cache()
        cleaned_files += n
        print(f"🧹 备份文件: 清理 {n} 个")

    # 检查清理后大小
    total_after = 0
    for path in [DECRYPTED_DIR, LOGS_DIR]:
        if path.exists():
            total_after += get_dir_size(path)
    total_after += config_size

    saved = total_before - total_after
    print(f"\n✅ 清理完成: {cleaned_files} 个文件, 释放 {format_size(saved)}")
    print(f"📊 清理后: {format_size(total_after)}")

if __name__ == "__main__":
    main()
