#!/usr/bin/env python3
"""
WeChat 群聊每日总结 Pipeline
整合 wechat-digest 解密模块 + 纯规则总结 + UI 自动化发送。
零 LLM 消耗。

用法:
    python3 pipeline.py                          # 总结监控群
    python3 pipeline.py --chat "58299288465"     # 总结指定群
    python3 pipeline.py --hours 24               # 最近 24 小时
    python3 pipeline.py --dry-run                # 只生成不发送
    python3 pipeline.py --send                   # 生成并发送
    python3 pipeline.py --voice-engine whisper   # 语音转文字引擎
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import zstandard

# ── 配置 ──
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from crypto.decrypt import full_decrypt, decrypt_wal
from crypto.config import load_config

# 监控的群聊 wxid
WATCHED_GROUPS = [
    "58299288465@chatroom",  # 琅泽-老K】几何节点全能班0群
    "48672694909@chatroom",  # 琅泽-老K】BL几何节点入门
]

CONTACTS_FILE = PROJECT_DIR / "contacts.json"
GROUP_NICKNAMES_FILE = PROJECT_DIR / "group_nicknames.json"
VENV_PYTHON = PROJECT_DIR / "backend" / ".venv" / "bin" / "python"

# ── 数据加载 ──

def load_keys():
    """加载解密密钥"""
    _, keys_file = load_config()
    with open(keys_file) as f:
        return json.load(f)

def load_contacts():
    if CONTACTS_FILE.exists():
        with open(CONTACTS_FILE) as f:
            return json.load(f)
    return {}

def _load_group_nicknames():
    """加载群成员补充昵称映射。"""
    if GROUP_NICKNAMES_FILE.exists():
        with open(GROUP_NICKNAMES_FILE) as f:
            data = json.load(f)
        # 过滤掉 _comment 等元数据
        return {k: v for k, v in data.items() if not k.startswith("_")}
    return {}


def resolve_name(wxid, contacts, group_nicknames=None):
    """解析发送者昵称，三层 fallback：contacts → group_nicknames → 截断 wxid。"""
    if not wxid:
        return None

    # 第一层：contacts.json（通讯录，6000+ 条）
    if wxid in contacts:
        info = contacts[wxid]
        name = info.get("remark") or info.get("nickname")
        if name:
            return name

    # 第二层：group_nicknames.json（群成员补充映射）
    if group_nicknames and wxid in group_nicknames:
        return group_nicknames[wxid]

    # 第三层：截断 wxid，至少比完整 wxid 可读
    if wxid.startswith("wxid_") and len(wxid) > 12:
        return wxid[5:12]  # 取中间部分，如 "psli7do"
    return wxid

# ── 消息提取（基于 wechat-digest 的 extract-messages） ──

def extract_messages(group_username, hours=24, voice_engine="auto"):
    """从数据库提取消息，返回结构化消息列表"""
    cfg, keys_file = load_config()
    with open(keys_file) as f:
        keys = json.load(f)

    db_dir = cfg["db_dir"]
    table_name = "Msg_" + hashlib.md5(group_username.encode()).hexdigest()
    dctx = zstandard.ZstdDecompressor()

    # 时间窗口
    if hours <= 0:
        # 自然日模式：今天 0:00 ~ 现在
        now = datetime.datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ts_start = int(today_start.timestamp())
        ts_end = int(time.time())
    else:
        # 滑动窗口模式：过去 N 小时
        ts_end = int(time.time())
        ts_start = ts_end - hours * 3600

    # 收集所有可用的消息数据库
    msg_dbs = []
    for key_name, key_info in keys.items():
        if re.match(r"^message/message_\d+\.db$", key_name) and "enc_key" in key_info:
            db_path = os.path.join(db_dir, key_name)
            if os.path.exists(db_path):
                msg_dbs.append((key_name, db_path, bytes.fromhex(key_info["enc_key"])))

    if not msg_dbs:
        return []

    # 解密并查询
    all_rows = []
    for key_name, db_path, enc_key in msg_dbs:
        cache_dir = tempfile.mkdtemp(prefix="wechat-pipeline-")
        out_path = os.path.join(cache_dir, "dec.db")
        try:
            full_decrypt(db_path, out_path, enc_key)
            wal_path = db_path + "-wal"
            if os.path.exists(wal_path):
                decrypt_wal(wal_path, out_path, enc_key)

            conn = sqlite3.connect(out_path)
            try:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                ).fetchone()
                if not exists:
                    continue

                rows = conn.execute(f"""
                    SELECT create_time, message_content, WCDB_CT_message_content, local_type
                    FROM "{table_name}"
                    WHERE create_time >= ? AND create_time < ?
                    ORDER BY create_time
                """, (ts_start, ts_end)).fetchall()
                all_rows.extend(rows)
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

    all_rows.sort(key=lambda r: r[0])

    # 加载联系人映射
    contacts = load_contacts()

    # 加载 Name2Id 映射（从已解密的数据库）
    name2id = {}
    for key_name, db_path, enc_key in msg_dbs[:1]:
        cache_dir = tempfile.mkdtemp(prefix="wechat-n2i-")
        out_path = os.path.join(cache_dir, "dec.db")
        try:
            full_decrypt(db_path, out_path, enc_key)
            conn = sqlite3.connect(out_path)
            try:
                rows = conn.execute("SELECT rowid, user_name FROM Name2Id;").fetchall()
                name2id = {r[0]: r[1] for r in rows}
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

    # 检测 "我" 的 sender_id
    my_id = _detect_my_sender_id(msg_dbs)

    # 检测语音
    has_voice = any((lt & 0xFFFFFFFF) == 34 for _, _, _, lt in all_rows)
    voice_entries = []
    transcriber = None
    if has_voice:
        try:
            from voice_to_text import VoiceTranscriber
            import zstandard as _zstd
            dctx = _zstd.ZstdDecompressor()
            
            # 直接从 media_0.db 加载语音数据
            if "message/media_0.db" in keys:
                media_key = bytes.fromhex(keys["message/media_0.db"]["enc_key"])
                media_db = os.path.join(db_dir, "message/media_0.db")
                if os.path.exists(media_db):
                    _cache = tempfile.mkdtemp(prefix="wechat-voice-load-")
                    _out = os.path.join(_cache, "dec.db")
                    full_decrypt(media_db, _out, media_key)
                    _wal = media_db + "-wal"
                    if os.path.exists(_wal):
                        decrypt_wal(_wal, _out, media_key)
                    _conn = sqlite3.connect(_out)
                    voice_rows = _conn.execute("""
                        SELECT create_time, voice_data FROM VoiceInfo
                        WHERE create_time >= ? AND create_time < ?
                        AND voice_data IS NOT NULL
                    """, (ts_start, ts_end)).fetchall()
                    _conn.close()
                    os.remove(_out)
                    shutil.rmtree(_cache, ignore_errors=True)
                    
                    if voice_rows:
                        transcriber = VoiceTranscriber(engine=voice_engine)
                        voice_entries = {ts: data for ts, data in voice_rows}
                        print(f"   🎤 找到 {len(voice_entries)} 条语音消息")
        except Exception as e:
            print(f"   ⚠️ 语音模块加载失败: {e}", file=sys.stderr)

    # 解析消息
    messages = []
    for ts, content, ct, lt in all_rows:
        real_type = lt & 0xFFFFFFFF
        if real_type not in (1, 3, 34, 43, 47, 49, 10000):
            continue
        if not content:
            continue

        # 解压 zstd
        try:
            if ct == 4:
                text = dctx.decompress(content).decode("utf-8", errors="replace")
            else:
                text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
        except Exception:
            continue

        # 解析发送者
        sender_wxid = ""
        is_me = False
        if real_type == 1:
            parts = text.split(":\n", 1)
            if len(parts) == 2:
                sender_wxid = parts[0].strip()
                text = parts[1].strip()
            # 检查是否是自己
            for sid, wxid in name2id.items():
                if wxid == sender_wxid:
                    if str(sid) == str(my_id):
                        is_me = True
                    break
        elif real_type == 34:
            # 语音消息 - 从 voice_entries 按时间戳匹配
            text = "[语音]"
            if voice_entries and ts in voice_entries and transcriber:
                voice_text = transcriber.transcribe(voice_entries[ts])
                if voice_text:
                    text = f"[语音] {voice_text}"
        elif real_type == 49:
            # 链接/文件/合并转发
            parsed = _parse_type49(text)
            text = parsed.get("text", "[链接/文件]")
            if parsed.get("sender"):
                sender_wxid = parsed["sender"]
        elif real_type == 3:
            text = "[图片]"
        elif real_type == 43:
            text = "[视频]"
        elif real_type == 47:
            text = "[表情]"
        elif real_type == 10000:
            text = "[系统] " + text[:50]

        dt_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")

        messages.append({
            "time": ts,
            "time_str": dt_str,
            "type": real_type,
            "sender_wxid": sender_wxid,
            "is_me": is_me,
            "content": text,
            "raw": content if isinstance(content, str) else "",
        })

    return messages

def _detect_my_sender_id(msg_dbs):
    """检测自己的 sender_id"""
    for key_name, db_path, enc_key in msg_dbs[:1]:
        cache_dir = tempfile.mkdtemp(prefix="wechat-myid-")
        out_path = os.path.join(cache_dir, "dec.db")
        try:
            full_decrypt(db_path, out_path, enc_key)
            conn = sqlite3.connect(out_path)
            try:
                # 获取前 5 个 Msg_ 表
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                ).fetchall()[:5]

                sender_sets = []
                for (tname,) in tables:
                    rows = conn.execute(f"""
                        SELECT DISTINCT real_sender_id FROM "{tname}"
                        WHERE local_type NOT IN (10000, 10002) LIMIT 20
                    """).fetchall()
                    ids = {r[0] for r in rows if r[0]}
                    if ids:
                        sender_sets.append(ids)

                if sender_sets:
                    common = sender_sets[0]
                    for s in sender_sets[1:]:
                        common = common & s
                    common.discard(0)
                    return min(common) if common else None
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
    return None

def _load_voice_data(db_dir, keys, ts_start, ts_end):
    """加载语音数据"""
    if "message/media_0.db" not in keys:
        return {}
    enc_key = bytes.fromhex(keys["message/media_0.db"]["enc_key"])
    db_path = os.path.join(db_dir, "message/media_0.db")
    if not os.path.exists(db_path):
        return {}

    cache_dir = tempfile.mkdtemp(prefix="wechat-voice-")
    out_path = os.path.join(cache_dir, "dec.db")
    try:
        full_decrypt(db_path, out_path, enc_key)
        wal_path = db_path + "-wal"
        if os.path.exists(wal_path):
            decrypt_wal(wal_path, out_path, enc_key)

        conn = sqlite3.connect(out_path)
        rows = conn.execute("""
            SELECT create_time, voice_data FROM VoiceInfo
            WHERE create_time >= ? AND create_time < ?
        """, (ts_start, ts_end)).fetchall()
        conn.close()
        return {ts: data for ts, data in rows}
    except Exception:
        return {}
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass

def _extract_sender_from_silk(text, name2id, my_id):
    """从语音消息中提取发送者"""
    # 语音消息的 content 也可能包含 sender 信息
    for sid, wxid in name2id.items():
        if str(sid) == str(my_id):
            return wxid
    return ""

def _parse_type49(content):
    """解析 type=49 消息（链接/文件/合并转发）"""
    result = {"text": "", "sender": "", "type": "link"}

    if not content or not content.startswith("<"):
        return result

    try:
        root = ET.fromstring(content)

        # 提取标题
        title_node = root.find(".//title")
        if title_node is not None and title_node.text:
            result["text"] = title_node.text.strip()

        # 提取 URL
        url_node = root.find(".//url")
        if url_node is not None and url_node.text:
            result["url"] = url_node.text.strip()

        # 检测合并转发
        record_node = root.find(".//recorditem")
        if record_node is not None:
            result["type"] = "merged_forward"
            # 解析子消息
            sub_msgs = []
            for item in root.iter("recorditem"):
                sender = ""
                msg_text = ""
                for child in item:
                    if child.tag == "sourcename":
                        sender = child.text or ""
                    elif child.tag == "msg":
                        msg_text = "".join(child.itertext()).strip()
                if sender or msg_text:
                    sub_msgs.append(f"{sender}: {msg_text[:50]}")
            if sub_msgs:
                result["text"] = f"[合并转发 {len(sub_msgs)} 条] " + " | ".join(sub_msgs[:3])

        # 提取文件名
        if not result["text"]:
            file_node = root.find(".//file_name") or root.find(".//filename")
            if file_node is not None and file_node.text:
                result["text"] = f"[文件] {file_node.text}"

        # 检测小程序
        app_node = root.find(".//appmsg")
        if app_node is not None:
            result["type"] = "miniapp"

    except Exception:
        pass

    return result

# ── 纯规则总结引擎 ──

def generate_summary(messages, contacts, group_name="", group_nicknames=None):
    """纯规则生成群聊总结（优化版）"""
    if not messages:
        return "今日无消息"

    lines = []
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    lines.append(f"📋 今日群聊总结")
    lines.append(f"📅 {today}")
    lines.append("")

    # ── 活跃度 Top 8 ──
    sender_counts = Counter()
    for m in messages:
        if not m["is_me"]:
            name = resolve_name(m["sender_wxid"], contacts, group_nicknames)
            if name:  # 过滤空名
                sender_counts[name] += 1

    lines.append("🏆 活跃排行")
    for name, count in sender_counts.most_common(8):
        lines.append(f"  {name}: {count}条")
    lines.append("")

    # ── 消息类型（只显示有内容的） ──
    type_counts = Counter()
    type_names = {1: "文本", 3: "图片", 34: "语音", 43: "视频",
                  47: "表情", 49: "链接/文件", 10000: "系统"}
    for m in messages:
        t = type_names.get(m["type"], "")
        if t:
            type_counts[t] += 1

    type_parts = [f"{t}:{c}" for t, c in type_counts.most_common() if c > 0]
    lines.append(f"📊 {', '.join(type_parts)}")
    lines.append("")

    # ── 关键词（过滤表情和无意义词） ──
    all_text = " ".join(
        m["content"] for m in messages
        if m["type"] == 1 and m["content"] and not m["content"].startswith("<")
    )
    # 扩展停用词：过滤表情名、语气词、常见无意义词
    stopwords = {
        "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
        "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
        "会", "着", "没有", "看", "好", "自己", "这", "他", "她", "它",
        "吗", "吧", "啊", "呢", "嗯", "哈", "呀", "哦", "嘛", "不是",
        # 表情名
        "捂脸", "破涕为笑", "旺柴", "强", "加油", "破涕", "例如",
        # 无意义高频词
        "现在", "这个", "那个", "什么", "怎么", "可以", "应该", "需要",
        "真的", "其实", "比较", "这样", "那样", "就是", "还是", "不过",
    }
    words = re.findall(r"[\u4e00-\u9fff]{2,6}", all_text)
    word_freq = Counter(w for w in words if w not in stopwords and len(w) >= 2)

    hot_words = [w for w, c in word_freq.most_common(10) if c >= 3]
    if hot_words:
        lines.append(f"🔥 热词: {' / '.join(hot_words)}")
        lines.append("")

    # ── 关键讨论（精简） ──
    discussions = _extract_discussions(messages, contacts, group_nicknames)
    if discussions:
        lines.append("💬 今日话题")
        for d in discussions[:3]:
            # 只显示话题标题，不显示详细消息
            lines.append(f"  • {d['topic']}")
        lines.append("")

    # ── 概览 ──
    total = len(messages)
    text_count = sum(1 for m in messages if m["type"] == 1)
    unique_senders = len(set(m["sender_wxid"] for m in messages if not m["is_me"]))

    lines.append(f"📈 共 {total} 条消息, {text_count} 条文本, {unique_senders} 人参与")

    return "\n".join(lines)

def generate_brief_summary(messages, contacts, group_name="", group_nicknames=None):
    """生成精简版总结（适合群内发送）"""
    if not messages:
        return "今日无消息"

    lines = []
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    lines.append(f"📋 今日群聊总结 ({today})")
    lines.append("")

    # 活跃度 Top 5
    sender_counts = Counter()
    for m in messages:
        if not m["is_me"]:
            name = resolve_name(m["sender_wxid"], contacts, group_nicknames)
            sender_counts[name] += 1

    lines.append("🏆 活跃 Top 5")
    for name, count in sender_counts.most_common(5):
        lines.append(f"  {name}: {count}条")
    lines.append("")

    # 关键词
    all_text = " ".join(
        m["content"] for m in messages
        if m["type"] == 1 and m["content"] and not m["content"].startswith("<")
    )
    stopwords = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人",
                 "也", "很", "到", "说", "要", "去", "你", "会", "好", "这", "那"}
    words = re.findall(r"[\u4e00-\u9fff]{2,6}", all_text)
    word_freq = Counter(w for w in words if w not in stopwords and len(w) >= 2)

    hot_words = [w for w, c in word_freq.most_common(10) if c >= 3]
    if hot_words:
        lines.append(f"🔥 热词: {' / '.join(hot_words)}")
        lines.append("")

    # 概览
    total = len(messages)
    text_count = sum(1 for m in messages if m["type"] == 1)
    unique_senders = len(set(m["sender_wxid"] for m in messages if not m["is_me"]))

    lines.append(f"📊 共 {total} 条消息, {text_count} 条文本, {unique_senders} 人参与")

    return "\n".join(lines)

def _filter_noise(messages):
    """过滤噪音消息：短消息、表情、系统消息"""
    filtered = []
    for m in messages:
        # 只保留文本消息
        if m["type"] != 1:
            continue
        content = m.get("content", "")
        if not content or content.startswith("<"):
            continue
        # 过滤短消息（< 5字符）
        if len(content.strip()) < 5:
            continue
        # 过滤纯表情/符号
        if re.match(r"^[\[\]【】a-zA-Z0-9\s\u4e00-\u9fff]{0,2}$", content.strip()):
            continue
        # 过滤纯 emoji 标记
        if content.strip() in ("[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[链接]"):
            continue
        filtered.append(m)
    return filtered

def _segment_topics(messages, gap_minutes=10):
    """按时间间隔将消息分段为不同话题"""
    if not messages:
        return []

    segments = []
    current_segment = [messages[0]]

    for i in range(1, len(messages)):
        prev_ts = messages[i-1]["time"]
        curr_ts = messages[i]["time"]
        gap = (curr_ts - prev_ts) / 60  # 分钟

        if gap > gap_minutes:
            # 时间间隔超过阈值，开始新话题
            if len(current_segment) >= 3:  # 至少3条消息才算一个话题
                segments.append(current_segment)
            current_segment = [messages[i]]
        else:
            current_segment.append(messages[i])

    # 最后一段
    if len(current_segment) >= 3:
        segments.append(current_segment)

    return segments

def _extract_topic_keywords(messages, top_n=5):
    """从一组消息中提取关键词（只看有实质内容的长消息）"""
    stopwords = {
        "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
        "也", "很", "到", "说", "要", "去", "你", "会", "好", "这", "那",
        "吗", "吧", "啊", "呢", "嗯", "哈", "呀", "哦", "嘛", "不是",
        "一下", "没有", "觉得", "知道", "时候", "问题", "东西", "开始",
        "起来", "出来", "直接", "如果", "因为", "所以", "但是", "然后",
        "现在", "这个", "那个", "什么", "怎么", "可以", "应该", "需要",
        "真的", "其实", "比较", "这样", "那样", "就是", "还是", "不过",
        "捂脸", "破涕为笑", "旺柴", "强", "加油", "破涕", "例如",
        "哈哈", "好看", "厉害", "不错", "可以的", "好的", "对的",
        "是啊", "也是", "确实", "收到", "了解", "明白", "嗯嗯",
    }
    # 只看 >= 10 字符的消息（有实质内容）
    texts = [m["content"] for m in messages if len(m["content"]) >= 10]
    if not texts:
        return []
    all_text = " ".join(texts)
    words = re.findall(r"[\u4e00-\u9fff]{2,6}", all_text)
    word_freq = Counter(w for w in words if w not in stopwords and len(w) >= 2)
    return [w for w, c in word_freq.most_common(top_n) if c >= 2]

def _summarize_segment(segment, contacts, group_nicknames=None):
    """将一段消息总结为一个话题描述"""
    if not segment:
        return ""

    senders = set()
    for m in segment:
        if not m["is_me"]:
            name = resolve_name(m["sender_wxid"], contacts, group_nicknames)
            if name:
                senders.add(name)

    first_time = segment[0]["time_str"]
    last_time = segment[-1]["time_str"]

    # 找话题发起消息：第一条 >= 8 字符的消息
    topic_msg = None
    for m in segment:
        if len(m["content"]) >= 8 and not m["content"].startswith("["):
            topic_msg = m
            break

    if topic_msg:
        topic = topic_msg["content"][:50]
        if len(topic_msg["content"]) > 50:
            topic += "..."
    else:
        # 回退到关键词
        keywords = _extract_topic_keywords(segment, top_n=3)
        topic = ", ".join(keywords) if keywords else "杂聊"

    participants = ", ".join(sorted(senders)[:3])
    if len(senders) > 3:
        participants += f" 等{len(senders)}人"

    return f"{topic} ({participants}, {first_time}-{last_time}, {len(segment)}条)"

def _extract_discussions(messages, contacts, group_nicknames=None):
    """提取讨论话题（优化版：时间分段 + 关键词聚类）"""
    # 过滤噪音
    clean = _filter_noise(messages)
    if not clean:
        return []

    # 按时间分段
    # 过滤后按时间分段（更短间隔，更细粒度）
    segments = _segment_topics(clean, gap_minutes=2)

    # 合并太短的段（< 3条消息）到相邻段
    merged = []
    for seg in segments:
        if merged and len(seg) < 3:
            merged[-1].extend(seg)
        else:
            merged.append(seg)
    segments = [s for s in merged if len(s) >= 3]

    # 每段生成话题摘要
    discussions = []
    for seg in segments:
        summary = _summarize_segment(seg, contacts, group_nicknames)
        if summary:
            discussions.append({"topic": summary, "msg_count": len(seg)})

    # 按消息数量排序（取最活跃的讨论）
    discussions.sort(key=lambda x: x["msg_count"], reverse=True)

    return discussions[:5]

def _extract_action_items(messages, contacts):
    items = []
    patterns = [
        r"(明天|下周|今天晚上?|今晚|后天|周六|周日).{2,20}",
        r"(记得|别忘了|提醒我|帮我|请).{2,20}",
        r"(约|定|安排).{2,20}",
    ]
    for m in messages:
        if m["type"] != 1 or not m["content"]:
            continue
        text = m["content"]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                sender = resolve_name(m["sender_wxid"], contacts, group_nicknames)
                snippet = text[max(0, match.start() - 10):match.end() + 10]
                if len(snippet) > 60:
                    snippet = snippet[:57] + "..."
                items.append(f"[{m['time_str']}] {sender}: {snippet}")
                break
    seen = set()
    unique = []
    for item in items:
        key = item[:30]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:10]

# ── 发送 ──

# 预设发送目标
SEND_TARGETS = {
    "我": "文件传输助手",
    "filehelper": "文件传输助手",
    "file_transfer": "文件传输助手",
}

def resolve_send_target(target, name_map=None):
    """解析发送目标名称（wxid → 群名）"""
    if not target:
        return None
    resolved = SEND_TARGETS.get(target, target)
    if name_map and resolved in name_map:
        return name_map[resolved]
    return resolved

def send_to_wechat(text, chat_name):
    """通过 wechat-mcp-macos 发送消息"""
    if not VENV_PYTHON.exists():
        print("⚠️  未找到 venv python，跳过发送")
        return False

    resolved = resolve_send_target(chat_name)
    if resolved:
        chat_name = resolved

    site_packages = str(PROJECT_DIR / "backend" / ".venv" / "lib" / "python3.14" / "site-packages")
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped_chat = chat_name.replace("\\", "\\\\").replace('"', '\\"')

    # 将文本写入临时文件，避免 shell 转义问题
    import tempfile
    text_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    text_file.write(text)
    text_file.close()

    code = f"""
import sys, time
sys.path.insert(0, '{site_packages}')
from wechat_mcp_macos.sender import activate_wechat, search_and_select_chat, send_message

# 从文件读取文本
with open('{text_file.name}', 'r', encoding='utf-8') as f:
    text = f.read()

ok, msg = activate_wechat()
if not ok:
    print(f"激活失败: {{msg}}")
    sys.exit(1)
time.sleep(0.5)

ok, msg = search_and_select_chat("{escaped_chat}")
if not ok:
    print(f"搜索失败: {{msg}}")
    sys.exit(1)
time.sleep(1)

max_len = 2000
chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]

for chunk in chunks:
    ok, msg = send_message(chunk)
    if not ok:
        print(f"发送失败: {{msg}}")
        sys.exit(1)
    time.sleep(0.5)

print(f"已发送到 {escaped_chat} ({{len(chunks)}} 条)")
"""
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"发送错误: {result.stderr[:200]}")
        return False
    return True

def preview_send(text, chat_name, max_preview=500):
    """预览发送内容"""
    resolved = resolve_send_target(chat_name) or chat_name
    print(f"\n📤 发送预览")
    print(f"  目标: {resolved}")
    print(f"  长度: {len(text)} 字符")
    if len(text) > max_preview:
        print(f"  内容: {text[:max_preview]}...")
    else:
        print(f"  内容: {text}")

# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(description="WeChat 群聊每日总结")
    parser.add_argument("--chat", help="群 wxid 关键词")
    parser.add_argument("--hours", type=int, default=0, help="0=今天自然日, >0=过去N小时")
    parser.add_argument("--dry-run", action="store_true", help="只生成不发送")
    parser.add_argument("--output", help="输出到文件")
    parser.add_argument("--voice-engine", default="auto", help="语音引擎: auto/whisper/none")
    parser.add_argument("--cleanup", action="store_true", help="生成后清理缓存")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    print("=== WeChat 群聊总结 ===")
    print(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    contacts = load_contacts()
    group_nicknames = _load_group_nicknames()
    name_map = {}
    try:
        cfg_db = load_config()
        with open(cfg_db['keys_file']) as f:
            keys_db = json.load(f)
        db_inst = WeChatDB(cfg_db['db_dir'], keys_db)
        for g in db_inst.get_groups():
            name_map[g['username']] = g['name']
    except Exception:
        pass

    # 确定要总结的群
    groups = WATCHED_GROUPS
    if args.chat:
        groups = [g for g in WATCHED_GROUPS if args.chat in g]
        if not groups:
            groups = [args.chat]

    all_summaries = []
    for group_wxid in groups:
        print(f"── {group_wxid} ──")
        messages = extract_messages(group_wxid, hours=args.hours, voice_engine=args.voice_engine)
        print(f"   拉取到 {len(messages)} 条消息 ({args.hours}h)")

        if not messages:
            print("   跳过（无消息）")
            continue

        summary = generate_summary(messages, contacts, group_wxid, group_nicknames)
        all_summaries.append({"wxid": group_wxid, "summary": summary, "msg_count": len(messages)})

        if args.json:
            # JSON 模式：输出结构化数据供 AI 读取
            import json as _json
            print(_json.dumps({
                "group": group_wxid,
                "message_count": len(messages),
                "summary": summary,
                "period_hours": args.hours,
            }, ensure_ascii=False, indent=2))
        elif args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(summary)
            print(f"   已保存到 {args.output}")
        else:
            print()
            print(summary)

    # 清理
    if args.cleanup:
        print()
        from cleanup import cleanup_decrypted, cleanup_logs
        n = cleanup_decrypted(7)
        n2 = cleanup_logs(30)
        print(f"🧹 清理缓存: 解密文件 {n} 个, 日志 {n2} 个")

    print()
    print("=== 完成 ===")

if __name__ == "__main__":
    import subprocess
    main()
