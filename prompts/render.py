#!/usr/bin/env python3
"""
Prompt render engine for wechat-mcp-macos.
Matches chat name/wxid against registry.json → fetches data → fills template → outputs text/image.

Usage:
    python3 render.py "群名或联系人" [--hours 24] [--type group|contact|auto]
    python3 render.py "群名" --image                     # 额外生成图片
    python3 render.py --list                              # 列出所有已注册 prompt
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 路径 ──
PROJECT_DIR = Path(__file__).resolve().parent.parent
REGISTRY_FILE = PROJECT_DIR / "prompts" / "registry.json"
TEMPLATES_DIR = PROJECT_DIR / "prompts" / "templates"
VENV_PYTHON = PROJECT_DIR / "backend" / ".venv" / "bin" / "python"


# ── registry 加载 ──

def load_registry() -> dict:
    with open(REGISTRY_FILE, encoding="utf-8") as f:
        return json.load(f)


def resolve_template(template_file: str) -> str:
    """Resolve template_file relative to TEMPLATES_DIR."""
    path = TEMPLATES_DIR / template_file
    if not path.exists():
        # fallback: relative to registry
        path = Path(template_file)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ── DB 查询 ──

def get_db_handle():
    """Get a WeChatDB instance via the installed wechat_mcp_macos package."""
    sys.path.insert(0, str(PROJECT_DIR))
    # Use the installed pip package
    from wechat_mcp_macos.config import load_config
    from wechat_mcp_macos.db import WeChatDB
    from wechat_mcp_macos.key_extractor import get_cached_keys

    cfg = load_config()
    keys = get_cached_keys()
    if not keys:
        print("❌ 密钥未提取/加载", file=sys.stderr)
        return None
    return WeChatDB(cfg["db_dir"], keys)


def resolve_chat_info(db, name: str):
    """Resolve a chat name or wxid to (wxid, display_name, is_group).
    Returns (wxid, name, is_group) or (None, None, None)."""
    # Try as group
    groups = db.get_groups()
    for g in groups:
        if g["name"] == name or g["username"] == name:
            return g["username"], g["name"], True

    # Try as contact (from recent sessions)
    sessions = db.get_recent_sessions(limit=50)
    for s in sessions:
        if s["name"] == name or s["username"] == name:
            return s["username"], s["name"], s.get("is_group", False)

    # Try resolve_username (direct lookup)
    wxid = db.resolve_username(name)
    if wxid:
        # Determine if group by checking group list again
        for g in groups:
            if g["username"] == wxid:
                return wxid, g["name"], True
        return wxid, name, False

    return None, None, None


def get_messages(db, wxid: str, hours: int, limit: int = 200):
    """Fetch recent messages from a chat."""
    since_ts = time.time() - hours * 3600
    return db.get_messages(wxid, since_ts=since_ts, limit=limit)


# ── trigger 匹配 ──

def match_prompt(registry: dict, wxid: str, name: str, is_group: bool) -> dict | None:
    """Find the best matching prompt entry.

    Priority:
        1. Exact match (chats/contacts list contains wxid)
        2. Fallback by type (empty chats list with matching type)
        3. Catchall
    """
    prompts = registry.get("prompts", [])
    matched = None
    fallback = None

    for p in prompts:
        t = p.get("trigger", {})
        trigger_type = t.get("type", "")

        if trigger_type == "catchall":
            matched = p  # keep going, higher priority may override
            continue

        if is_group and trigger_type == "group":
            chats = t.get("chats", [])
            if wxid in chats:
                return p  # exact match → immediate return
            if not chats:
                fallback = p  # fallback candidate

        if not is_group and trigger_type == "contact":
            contacts = t.get("contacts", [])
            if wxid in contacts:
                return p
            if not contacts:
                fallback = p

        if trigger_type == "catchall":
            matched = p

    return fallback if fallback is not None else matched


# ── 模板填充 ──

def format_messages_text(msgs: list) -> str:
    """Format messages as [time] sender: text."""
    lines = []
    for m in msgs:
        sender = m.get("sender", "未知")
        text = m.get("text", "")
        ts = m.get("time_str", "")
        lines.append(f"[{ts}] {sender}: {text}")
    return "\n".join(lines)


def compute_stats(msgs: list) -> dict:
    """Compute basic stats from messages."""
    total = len(msgs)
    # Active hour
    hours_count = {}
    for m in msgs:
        ts = m.get("time_str", "")
        h = ts[:2] if len(ts) >= 2 else "??"
        hours_count[h] = hours_count.get(h, 0) + 1
    peak_hour = max(hours_count, key=hours_count.get) if hours_count else "--"
    peak_hour_end = f"{int(peak_hour) + 1:02d}" if peak_hour != "--" else "--"
    return {
        "total": total,
        "peak_hour": f"{peak_hour}:00 - {peak_hour_end}:00" if peak_hour != "--" else "暂无数据",
        "sender_count": len(set(m.get("sender", "") for m in msgs)),
    }


def fill_template(template: str, variables: dict) -> str:
    """Replace {{VAR}} placeholders with values."""
    result = template
    for key, val in variables.items():
        result = result.replace("{{" + key + "}}", str(val))
    return result


# ── 图片生成 ──

def render_image(summary_text: str, output_path: str, style: dict | None = None):
    """Call summary_img.py to render a text summary as a long image."""
    import tempfile

    # Write summary to temp JSON
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=str(PROJECT_DIR / "prompts"))
    json.dump({"header": {"title": "群聊总结"}, "topics": [{"content": summary_text}]}, tmp, ensure_ascii=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(PROJECT_DIR / "summary_img.py"),
             "--input", tmp_path,
             "--output", output_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"⚠️  图片生成失败: {result.stderr[:200]}", file=sys.stderr)
            return False
        print(f"✅ 图片已生成: {output_path}")
        return True
    except Exception as e:
        print(f"⚠️  图片生成异常: {e}", file=sys.stderr)
        return False
    finally:
        os.unlink(tmp_path)


# ── 主入口 ──

def main():
    parser = argparse.ArgumentParser(description="Prompt render engine for wechat-mcp-macos")
    parser.add_argument("chat", nargs="?", default="", help="群聊/联系人名称或 wxid")
    parser.add_argument("--hours", type=int, default=24, help="时间范围（小时）")
    parser.add_argument("--type", choices=["group", "contact", "auto"], default="auto",
                        help="强制指定类型（auto 自动检测）")
    parser.add_argument("--image", action="store_true", help="同时生成图片")
    parser.add_argument("--list", action="store_true", help="列出所有已注册 prompt")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式（供调用方解析）")
    args = parser.parse_args()

    registry = load_registry()
    defaults = registry.get("defaults", {})

    # --list 模式
    if args.list:
        print("=== 已注册 Prompt ===")
        for p in registry.get("prompts", []):
            trigger = p.get("trigger", {})
            ttype = trigger.get("type", "?")
            targets = trigger.get("chats") or trigger.get("contacts") or []
            target_str = ", ".join(targets[:3])
            if len(targets) > 3:
                target_str += "..."
            print(f"  [{p['id']}] {p['name']}")
            print(f"    trigger: {ttype} ({target_str or 'fallback'})")
            print(f"    mode: {p.get('process', {}).get('mode', '?')}")
            print(f"    output: {', '.join(k for k,v in p.get('output', {}).items() if v)}")
            print()
        return

    if not args.chat:
        parser.print_help()
        return

    # ── 连接 DB ──
    db = get_db_handle()
    if not db:
        print("❌ 无法连接微信数据库", file=sys.stderr)
        sys.exit(1)

    # ── 解析 chat ——
    wxid, display_name, is_group = resolve_chat_info(db, args.chat)
    if not wxid:
        print(f"❌ 未找到匹配的聊天: {args.chat}", file=sys.stderr)
        sys.exit(1)

    if args.type == "group":
        is_group = True
    elif args.type == "contact":
        is_group = False

    # ── 匹配 prompt ──
    prompt_entry = match_prompt(registry, wxid, display_name, is_group)
    if not prompt_entry:
        print(f"⚠️ 未匹配到 prompt，使用兜底", file=sys.stderr)
        # Build minimal inline prompt
        prompt_entry = {
            "process": {"mode": "rule"},
            "output": {"text": True, "image": False},
        }

    process = prompt_entry.get("process", {})
    output_cfg = prompt_entry.get("output", {})

    # ── 取数 ──
    hours = args.hours or prompt_entry.get("input", {}).get("pipeline_args", {}).get("hours", defaults.get("hours", 24))
    limit = defaults.get("limit", 200)
    messages = get_messages(db, wxid, hours, limit)

    if not messages:
        print(f"⚠️ {display_name} 最近 {hours}h 内没有消息")
        # Still produce minimal output
        stats = {"total": 0, "peak_hour": "无", "sender_count": 0}
    else:
        stats = compute_stats(messages)

    # ── 变量 ──
    today = datetime.now().strftime("%Y-%m-%d")
    vars_dict = {
        "GROUP_NAME": display_name,
        "CONTACT_NAME": display_name,
        "TARGET_DATE": today,
        "WXID": wxid,
        "TOTAL": str(stats["total"]),
        "PEAK_HOUR": stats["peak_hour"],
        "SENDER_COUNT": str(stats["sender_count"]),
        "HOURS": str(hours),
        "MESSAGES": format_messages_text(messages),
    }

    # ── 渲染 ──
    mode = process.get("mode", "rule")

    if mode == "llm":
        # Load template from file
        template_file = process.get("template_file", "")
        template = resolve_template(template_file) if template_file else ""
        if not template:
            print(f"⚠️ 模板文件未找到: {template_file}，回退 rule 模式", file=sys.stderr)
            mode = "rule"

    if mode == "llm":
        result_text = fill_template(template, vars_dict)
    else:
        # rule mode: output structured summary
        result_text = (
            f"=== {display_name} 消息统计 ===\n"
            f"日期: {today} (最近 {hours}h)\n"
            f"消息总数: {stats['total']} 条\n"
            f"发言人数: {stats['sender_count']} 人\n"
            f"最活跃时段: {stats['peak_hour']}\n"
        )

    # ── 输出 ──
    if args.json:
        output = {
            "chat": display_name,
            "wxid": wxid,
            "is_group": is_group,
            "hours": hours,
            "stats": stats,
            "mode": mode,
            "prompt_id": prompt_entry.get("id", "default"),
            "prompt": result_text if mode == "llm" else None,
            "summary": result_text if mode == "rule" else None,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(result_text)

    # ── 图片 ──
    if args.image and output_cfg.get("image", False):
        img_style = output_cfg.get("image_style", defaults.get("output", {}).get("image_style", {}))
        out_path = str(PROJECT_DIR / "prompts" / "summaries" / f"{wxid.split('@')[0]}-{today}.png")
        os.makedirs(str(PROJECT_DIR / "prompts" / "summaries"), exist_ok=True)
        render_image(result_text, out_path, img_style)

    # 返回结构化数据供调用方（Agent / 脚本链式调用）消费
    if args.json:
        return output
    return result_text


if __name__ == "__main__":
    main()
