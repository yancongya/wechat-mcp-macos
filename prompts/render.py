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
    if hours is None:
        hours = 24
    if hours <= 0:
        now = datetime.now()
        since_ts = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    else:
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


def _extract_hour_label(ts: str) -> str:
    ts = (ts or "").strip()
    m = re.search(r"(?:\b|\s)(\d{2}):(\d{2})", ts)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d{2}", ts):
        return ts
    return "??"


def compute_stats(msgs: list) -> dict:
    """Compute basic stats from messages."""
    total = len(msgs)
    # Active hour
    hours_count = {}
    for m in msgs:
        ts = m.get("time_str", "")
        h = _extract_hour_label(ts)
        hours_count[h] = hours_count.get(h, 0) + 1
    peak_hour = max(hours_count, key=hours_count.get) if hours_count else "--"
    peak_hour_end = f"{int(peak_hour) + 1:02d}" if peak_hour != "--" else "--"
    return {
        "total": total,
        "peak_hour": f"{peak_hour}:00 - {peak_hour_end}:00" if peak_hour != "--" else "暂无数据",
        "sender_count": len(set(m.get("sender", "") for m in msgs if m.get("sender"))),
    }


def _clean_text_messages(msgs: list) -> list:
    clean = []
    for m in msgs:
        text = (m.get("text") or "").strip()
        if not text or text.startswith("["):
            continue
        if len(text) < 4:
            continue
        clean.append(m)
    return clean


def _extract_hot_words(msgs: list, top_n: int = 8) -> list:
    stopwords = {
        "这个", "那个", "就是", "可以", "应该", "感觉", "现在", "然后", "还是", "因为",
        "所以", "如果", "一下", "不是", "没有", "你们", "我们", "他们", "自己", "今天",
        "昨天", "时候", "东西", "问题", "老师", "哈哈", "真的", "已经", "一个", "怎么",
        "旺柴", "捂脸", "破涕为笑", "课程问题群里问", "表情", "图片", "视频", "链接", "文件",
        "回复", "试试", "可以的", "好的", "一下子", "这样", "那样", "其实", "然后呢"
    }
    sender_names = {m.get("sender", "").strip().lower() for m in msgs if (m.get("sender") or "").strip()}
    text = " ".join((m.get("text") or "") for m in _clean_text_messages(msgs))
    low_text = text.lower()

    canonical_tags = [
        ("Blender", ["blender"]),
        ("几何节点", ["几何节点"]),
        ("Codex", ["codex"]),
        ("Qwen", ["qwen"]),
        ("本地模型", ["本地模型", "omlx", "llama.cpp", "llama", "ollama"]),
        ("XP粒子", ["xp粒子", "xp ", " x p", "xp"]),
        ("OpenPBR", ["openpbr"]),
        ("C4D", ["c4d"]),
        ("Affinity", ["affinity"]),
        ("L2D", ["l2d"]),
        ("Spine", ["spine"]),
        ("豆包", ["豆包"]),
        ("DeepSeek", ["deepseek"]),
        ("MCP", ["mcp"]),
        ("API", ["api"]),
        ("技能沉淀", ["skill", "技能", "蒸馏"]),
        ("倒角节点", ["倒角"]),
        ("几何接近", ["几何接近"]),
        ("汉化", ["汉化"]),
    ]

    tag_scores = {}
    for tag, aliases in canonical_tags:
        score = 0
        for alias in aliases:
            score += low_text.count(alias.lower()) if any('a' <= ch.lower() <= 'z' for ch in alias) else text.count(alias)
        if score > 0:
            tag_scores[tag] = tag_scores.get(tag, 0) + score * 5

    words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9.+_-]{2,16}", text)
    freq = {}
    phrase_blacklist = ["现在", "觉得", "可以", "这样", "那个", "然后", "一下", "真的", "就是"]
    for w in words:
        wl = w.lower()
        if w in stopwords or wl in stopwords:
            continue
        if wl in sender_names:
            continue
        if len(w) < 2:
            continue
        if len(w) > 10 and any(b in w for b in phrase_blacklist):
            continue
        if re.fullmatch(r"[A-Za-z]{1,3}", w):
            continue
        if re.search(r"[🐶😂🤣😭🥲😅😆😄🙂🙃😉😎🤔🤡💩]", w):
            continue
        score = 1
        if _domain_relevance_score(w) > 0:
            score += 4
        freq[w] = freq.get(w, 0) + score

    merged = dict(tag_scores)
    for w, score in freq.items():
        if w in merged:
            merged[w] += score
        elif _domain_relevance_score(w) > 0 and len(w) <= 8:
            merged[w] = score

    ranked = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    result = []
    seen = set()
    for w, _c in ranked:
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(w)
        if len(result) >= top_n:
            break
    return result


def _extract_resources(msgs: list, limit: int = 8) -> list:
    resources = []
    seen = set()
    for m in msgs:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if "http://" in text or "https://" in text or text.startswith("[链接]") or text.startswith("[文件]"):
            key = text[:120]
            if key in seen:
                continue
            seen.add(key)
            resources.append(f"[{m.get('time_str','')}] {m.get('sender','未知')}: {text[:120]}")
        if len(resources) >= limit:
            break
    return resources


def _segment_messages(msgs: list, gap_seconds: int = 300) -> list:
    if not msgs:
        return []
    segments = [[msgs[0]]]
    for m in msgs[1:]:
        prev = segments[-1][-1]
        if (m.get("timestamp", 0) or 0) - (prev.get("timestamp", 0) or 0) > gap_seconds:
            segments.append([m])
        else:
            segments[-1].append(m)
    return segments


def _topic_keywords(segment: list, top_n: int = 3) -> list:
    return _extract_hot_words(segment, top_n=top_n)


def _domain_relevance_score(text: str) -> int:
    keywords = [
        "blender", "几何节点", "节点", "codex", "gpt", "qwen", "模型", "agent", "workflow",
        "mcp", "粒子", "倒角", "材质", "渲染", "xp", "openpbr", "纹理", "llama",
        "omlx", "豆包", "deepseek", "skill", "插件", "版本", "接入", "自动化", "本地模型",
        "api", "汉化", "几何接近", "刚体", "阻尼", "重力", "c4d", "affinity", "spine", "l2d"
    ]
    low = text.lower()
    score = sum(1 for kw in keywords if kw in low)
    if any(k in low for k in ["哈哈", "笑死", "牛逼", "爽", "卧槽", "表情"]):
        score -= 1
    return score


def _representative_quotes(segment: list, limit: int = 3) -> list:
    candidates = []
    seen = set()
    for m in segment:
        sender = m.get("sender") or "未知"
        text = (m.get("text") or "").strip()
        if not text or text.startswith("[") or len(text) < 8:
            continue
        if text in seen:
            continue
        seen.add(text)
        score = _domain_relevance_score(text) * 5 + min(len(text), 60) / 20
        candidates.append((score, sender, text))
    candidates.sort(key=lambda x: x[0], reverse=True)

    quotes = []
    used_senders = set()
    for _score, sender, text in candidates:
        if sender in used_senders and len(quotes) >= 1:
            continue
        used_senders.add(sender)
        quotes.append(f"- {sender}: {text[:60]}")
        if len(quotes) >= limit:
            break
    return quotes


def build_compressed_context(msgs: list, stats: dict, raw_message_chars: int = 0) -> dict:
    clean = _clean_text_messages(msgs)
    type_counts = {}
    hour_counts = {f"{h:02d}": 0 for h in range(24)}
    sender_counts = {}
    for m in msgs:
        t = str(m.get("type", ""))
        type_counts[t] = type_counts.get(t, 0) + 1
        ts = _extract_hour_label(m.get("time_str") or "")
        if ts in hour_counts:
            hour_counts[ts] += 1
        sender = (m.get("sender") or "").strip()
        if sender:
            sender_counts[sender] = sender_counts.get(sender, 0) + 1

    segments = []
    for seg in _segment_messages(clean):
        if len(seg) < 4:
            continue
        participants = sorted({m.get("sender") or "未知" for m in seg if m.get("sender")})
        keywords = _topic_keywords(seg, top_n=3)
        starter = next((m.get("text", "") for m in seg if len((m.get("text") or "").strip()) >= 10), "")
        title = starter[:28] if starter else " / ".join(keywords) or "群聊讨论"
        resources = sum(1 for m in seg if "http://" in (m.get("text") or "") or "https://" in (m.get("text") or ""))
        segment_text = " ".join((m.get("text") or "") for m in seg)
        relevance = _domain_relevance_score(segment_text + " " + title + " " + " ".join(keywords))
        chatter = sum(1 for m in seg if any(k in (m.get("text") or "") for k in ["哈哈", "笑死", "牛逼", "卧槽", "爽", "表情", "旺柴", "捂脸"]))
        score = len(seg) + len(participants) * 2 + resources * 3 + relevance * 10 - chatter
        if relevance <= 0 and len(seg) < 35:
            continue
        if chatter > len(seg) * 0.35 and relevance < 2:
            continue
        segments.append({
            "title": title,
            "time": f"{seg[0].get('time_str','')} - {seg[-1].get('time_str','')}",
            "count": len(seg),
            "participants": participants[:5],
            "keywords": keywords,
            "quotes": _representative_quotes(seg, limit=3),
            "score": score,
            "relevance": relevance,
        })

    segments.sort(key=lambda x: (x["relevance"], x["score"]), reverse=True)
    top_segments = segments[:5]

    tips = []
    for m in clean:
        text = (m.get("text") or "").strip()
        if _domain_relevance_score(text) <= 0:
            continue
        if any(k in text for k in ["可以", "建议", "试试", "改", "参数", "下载", "版本", "用", "重启", "接入", "汉化", "问下官方", "调", "完整版", "历史版本"]):
            tips.append(f"- {m.get('sender','未知')}: {text[:80]}")
        if len(tips) >= 8:
            break

    context_lines = [
        f"消息总数：{stats['total']}",
        f"最活跃时段：{stats['peak_hour']}",
        f"发言人数：{stats['sender_count']}",
        f"热词：{' / '.join(_extract_hot_words(msgs, top_n=6)) or '无'}",
        "",
        "【高信息话题】",
    ]
    for idx, seg in enumerate(top_segments, 1):
        context_lines.append(f"{idx}. {seg['title']}")
        context_lines.append(f"   时间：{seg['time']} · {seg['count']}条 · 参与：{', '.join(seg['participants']) or '未知'}")
        if seg['keywords']:
            context_lines.append(f"   关键词：{' / '.join(seg['keywords'])}")
        for q in seg['quotes']:
            context_lines.append(f"   {q}")
    if tips:
        context_lines.extend(["", "【可复用技巧/建议】", *tips[:6]])
    resources = _extract_resources(msgs, limit=6)
    if resources:
        context_lines.extend(["", "【链接/资源】", *[f"- {r}" for r in resources]])

    context_text = "\n".join(context_lines)
    compressed_chars = len(context_text)
    text_count = sum(1 for m in msgs if str(m.get("type", "")) == "1")
    chars_saved = max(raw_message_chars - compressed_chars, 0)
    estimated_tokens_saved = round(chars_saved / 1.1) if chars_saved else 0

    return {
        "type_counts": type_counts,
        "hot_words": _extract_hot_words(msgs, top_n=6),
        "top_segments": top_segments,
        "tips": tips[:6],
        "resources": resources,
        "activity_by_hour": [{"hour": hour, "count": count} for hour, count in hour_counts.items()],
        "top_speakers": [
            {"name": name, "avatar_name": name, "count": count}
            for name, count in sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        ],
        "metrics": {
            "message_count": stats["total"],
            "sender_count": stats["sender_count"],
            "text_count": text_count,
            "raw_chars": raw_message_chars,
            "compressed_chars": compressed_chars,
            "chars_saved": chars_saved,
            "estimated_tokens_saved": estimated_tokens_saved,
            "compression_ratio": round((compressed_chars / raw_message_chars) * 100, 1) if raw_message_chars else 0,
        },
        "context_text": context_text,
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
    parser.add_argument("--hours", type=int, default=None, help="时间范围（小时），0=今天自然日")
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
    pipeline_args = prompt_entry.get("input", {}).get("pipeline_args", {})
    hours = args.hours if args.hours is not None else pipeline_args.get("hours", defaults.get("hours", 24))
    limit = pipeline_args.get("limit", defaults.get("limit", 200))
    messages = get_messages(db, wxid, hours, limit)

    raw_message_text = format_messages_text(messages)
    raw_message_chars = len(raw_message_text)

    if not messages:
        window_text = "今天" if hours == 0 else f"最近 {hours}h"
        print(f"⚠️ {display_name} {window_text}内没有消息")
        # Still produce minimal output
        stats = {"total": 0, "peak_hour": "无", "sender_count": 0}
        compressed = {"context_text": "无有效消息", "top_segments": [], "tips": [], "resources": [], "hot_words": [], "activity_by_hour": [], "top_speakers": [], "metrics": {"message_count": 0, "sender_count": 0, "text_count": 0, "raw_chars": 0, "compressed_chars": 0, "chars_saved": 0, "estimated_tokens_saved": 0, "compression_ratio": 0}}
    else:
        stats = compute_stats(messages)
        compressed = build_compressed_context(messages, stats, raw_message_chars=raw_message_chars)

    # ── 变量 ──
    today = datetime.now().strftime("%Y-%m-%d")
    window_desc = "今天自然日" if hours == 0 else f"最近 {hours} 小时"
    vars_dict = {
        "GROUP_NAME": display_name,
        "CONTACT_NAME": display_name,
        "TARGET_DATE": today,
        "WXID": wxid,
        "TOTAL": str(stats["total"]),
        "PEAK_HOUR": stats["peak_hour"],
        "SENDER_COUNT": str(stats["sender_count"]),
        "HOURS": str(hours if hours is not None else 24),
        "WINDOW_DESC": window_desc,
        "MESSAGES": raw_message_text,
        "COMPRESSED_CONTEXT": compressed["context_text"],
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
            "compressed_context": compressed,
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
