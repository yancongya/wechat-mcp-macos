#!/usr/bin/env python3
"""WeChat 本地数据安全清理器。

按时间阈值和容量阈值管理：
- prompts/summaries 中的中间 JSON/TXT 与最终 PNG
- ~/.wechat-mcp/decrypted 解密缓存
- ~/.wechat-mcp/logs 日志

默认读取项目根目录 cleanup-policy.json。只删除生成物，不触碰密钥、配置和原始微信数据库。
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY = ROOT_DIR / "cleanup-policy.json"
MCP_DIR = Path.home() / ".wechat-mcp"
SUMMARY_DIR = ROOT_DIR / "prompts" / "summaries"
DECRYPTED_DIR = MCP_DIR / "decrypted"
LOGS_DIR = MCP_DIR / "logs"

SUMMARY_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SUMMARY_INTERMEDIATE_SUFFIXES = {".json", ".txt", ".log"}


@dataclass
class Candidate:
    path: Path
    size: int
    mtime: float
    category: str


def format_size(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


def load_policy(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"清理配置不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("categories"), dict):
        raise ValueError("cleanup-policy.json 缺少 categories")
    return data


def iter_files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            yield path


def collect_candidates() -> dict[str, list[Candidate]]:
    result = {name: [] for name in ("summary_intermediates", "summary_images", "decrypted", "logs")}
    for path in iter_files(SUMMARY_DIR) or []:
        suffix = path.suffix.lower()
        if suffix in SUMMARY_IMAGE_SUFFIXES:
            category = "summary_images"
        elif suffix in SUMMARY_INTERMEDIATE_SUFFIXES or path.name == ".DS_Store":
            category = "summary_intermediates"
        else:
            continue
        stat = path.stat()
        result[category].append(Candidate(path, stat.st_size, stat.st_mtime, category))
    for category, root in (("decrypted", DECRYPTED_DIR), ("logs", LOGS_DIR)):
        for path in iter_files(root) or []:
            stat = path.stat()
            result[category].append(Candidate(path, stat.st_size, stat.st_mtime, category))
    return result


def select_deletions(files: list[Candidate], config: dict, now: float) -> tuple[list[Candidate], list[Candidate]]:
    """先按时间删除，再按容量从最旧开始压缩；始终保护最新 keep_newest 个。"""
    files = sorted(files, key=lambda item: item.mtime, reverse=True)
    keep_newest = max(0, int(config.get("keep_newest", 0)))
    protected = set(item.path for item in files[:keep_newest])
    max_age_days = max(0, float(config.get("max_age_days", 0)))
    max_size_bytes = max(0, int(float(config.get("max_size_mb", 0)) * 1024 * 1024))
    cutoff = now - max_age_days * 86400 if max_age_days else None

    selected: dict[Path, Candidate] = {}
    if cutoff is not None:
        for item in files:
            if item.path not in protected and item.mtime < cutoff:
                selected[item.path] = item

    survivors = [item for item in files if item.path not in selected]
    survivor_size = sum(item.size for item in survivors)
    if max_size_bytes and survivor_size > max_size_bytes:
        for item in sorted(survivors, key=lambda entry: entry.mtime):
            if survivor_size <= max_size_bytes:
                break
            if item.path in protected:
                continue
            selected[item.path] = item
            survivor_size -= item.size

    return list(selected.values()), [item for item in files if item.path not in selected]


def remove_empty_dirs(root: Path, dry_run: bool) -> None:
    if not root.exists() or dry_run:
        return
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def run_cleanup(policy: dict, dry_run: bool = False, only: set[str] | None = None, now: float | None = None) -> dict:
    now = now or time.time()
    candidates = collect_candidates()
    summary = {"deleted_files": 0, "deleted_bytes": 0, "categories": {}}

    for category, files in candidates.items():
        config = policy["categories"].get(category, {})
        enabled = bool(config.get("enabled", False))
        if only and category not in only:
            enabled = False
        total_before = sum(item.size for item in files)
        deletions, survivors = select_deletions(files, config, now) if enabled else ([], files)
        deleted_bytes = sum(item.size for item in deletions)
        if not dry_run:
            for item in deletions:
                try:
                    item.path.unlink()
                except FileNotFoundError:
                    pass
        summary["deleted_files"] += len(deletions)
        summary["deleted_bytes"] += deleted_bytes
        summary["categories"][category] = {
            "enabled": enabled,
            "files_before": len(files),
            "size_before": total_before,
            "delete_files": len(deletions),
            "delete_bytes": deleted_bytes,
            "files_after": len(survivors),
            "size_after": sum(item.size for item in survivors),
        }

    for root in (SUMMARY_DIR, DECRYPTED_DIR, LOGS_DIR):
        remove_empty_dirs(root, dry_run)
    return summary


def _cleanup_legacy_category(category: str, days: int) -> int:
    """兼容旧 pipeline.py 的 cleanup_decrypted/cleanup_logs 调用。"""
    policy = {
        "categories": {
            name: {
                "enabled": name == category,
                "max_age_days": days,
                "max_size_mb": 0,
                "keep_newest": 0,
            }
            for name in ("summary_intermediates", "summary_images", "decrypted", "logs")
        }
    }
    return run_cleanup(policy, only={category})["deleted_files"]


def cleanup_decrypted(days: int = 7) -> int:
    return _cleanup_legacy_category("decrypted", days)


def cleanup_logs(days: int = 30) -> int:
    return _cleanup_legacy_category("logs", days)


def print_report(report: dict, dry_run: bool) -> None:
    print("=== WeChat 数据清理检查 ===")
    labels = {
        "summary_intermediates": "总结中间数据",
        "summary_images": "总结图片",
        "decrypted": "解密缓存",
        "logs": "日志",
    }
    for category, item in report["categories"].items():
        status = "启用" if item["enabled"] else "跳过"
        print(
            f"{labels[category]} [{status}]：{item['files_before']} 个 / {format_size(item['size_before'])}"
            f" → 清理 {item['delete_files']} 个 / {format_size(item['delete_bytes'])}"
            f" → 保留 {item['files_after']} 个 / {format_size(item['size_after'])}"
        )
    action = "预计释放" if dry_run else "已释放"
    print(f"{action}：{format_size(report['deleted_bytes'])}，文件 {report['deleted_files']} 个")


def main() -> None:
    parser = argparse.ArgumentParser(description="按时间和容量阈值清理微信总结中间数据")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="清理策略 JSON")
    parser.add_argument("--check", action="store_true", help="查看当前占用与预计清理量，不删除")
    parser.add_argument("--dry-run", action="store_true", help="同 --check")
    parser.add_argument(
        "--only",
        action="append",
        choices=["summary_intermediates", "summary_images", "decrypted", "logs"],
        help="只处理指定类别，可重复",
    )
    args = parser.parse_args()
    dry_run = args.check or args.dry_run
    policy = load_policy(Path(args.policy).expanduser().resolve())
    report = run_cleanup(policy, dry_run=dry_run, only=set(args.only or []))
    print_report(report, dry_run)


if __name__ == "__main__":
    main()
