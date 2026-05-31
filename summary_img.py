#!/usr/bin/env python3
"""
群聊日报长图生成器 - 纸张纹理版
将 AI 总结的结构化内容渲染为精美长图，用于分享。

用法：
  # 交互式（输入 JSON）
  python3 summary_img.py --input /tmp/summary.json --output /tmp/summary.png

  # 管道输入
  python3 summary_img.py --output /tmp/summary.png << 'EOF'
  {"header": {...}, "topics": [...]}
  EOF

输入 JSON 格式见 --schema。
"""

import argparse
import json
import sys
import os
import hashlib
import sqlite3
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    print("需要安装 Pillow: pip3 install Pillow --break-system-packages", file=sys.stderr)
    sys.exit(1)

# ── 配置 ──

BASE_W = 780
BASE_PAD = 40
BASE_CONTENT_W = BASE_W - 2 * BASE_PAD - 24

# 纸张纹理风格颜色
BG_PAPER = (245, 240, 232)        # 米黄色纸张背景
CARD_PAPER = (252, 250, 245)      # 卡片纸张色
C_TITLE = (45, 35, 25)            # 深棕色标题
C_BODY = (60, 50, 40)             # 正文深棕
C_BODY_LIGHT = (100, 90, 80)      # 浅棕色正文
C_META = (120, 110, 100)          # 元信息灰棕
C_DIV = (200, 190, 175)           # 分隔线
C_QUOTE_BG = (240, 235, 225)      # 引用背景
ACCENT = [
    (180, 83, 9),    # 棕红
    (34, 120, 85),   # 墨绿
    (59, 100, 145),  # 蓝灰
    (160, 80, 50),   # 赭石
    (100, 70, 120),  # 紫灰
]

# 字体（macOS 兼容）
FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]


def get_font(size):
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size, index=0)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap_text(text, f, max_w):
    """中文友好的自动换行"""
    lines = []
    for para in text.split('\n'):
        if not para:
            lines.append('')
            continue
        cur = ''
        for ch in para:
            t = cur + ch
            if f.getbbox(t)[2] > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur = t
        if cur:
            lines.append(cur)
    return lines


def create_paper_texture(w, h):
    """创建纸张纹理背景"""
    import random
    img = Image.new('RGB', (w, h), BG_PAPER)
    draw = ImageDraw.Draw(img)
    
    # 添加纸张纹理噪点
    random.seed(42)  # 固定种子保证一致性
    for _ in range(w * h // 8):
        x = random.randint(0, w - 1)
        y = random.randint(0, h - 1)
        # 随机颜色偏移
        r = BG_PAPER[0] + random.randint(-8, 8)
        g = BG_PAPER[1] + random.randint(-8, 8)
        b = BG_PAPER[2] + random.randint(-8, 8)
        draw.point((x, y), fill=(r, g, b))
    
    # 添加轻微的纹理线条
    for i in range(0, h, 3):
        offset = random.randint(-2, 2)
        draw.line([(0, i + offset), (w, i + offset)], fill=(0, 0, 0, 3), width=1)
    
    return img


def create_card_texture(w, h):
    """创建卡片纸张纹理"""
    import random
    img = Image.new('RGB', (w, h), CARD_PAPER)
    draw = ImageDraw.Draw(img)
    
    # 添加卡片纹理
    random.seed(123)
    for _ in range(w * h // 12):
        x = random.randint(0, w - 1)
        y = random.randint(0, h - 1)
        r = CARD_PAPER[0] + random.randint(-5, 5)
        g = CARD_PAPER[1] + random.randint(-5, 5)
        b = CARD_PAPER[2] + random.randint(-5, 5)
        draw.point((x, y), fill=(r, g, b))
    
    return img


def load_avatar(username, db_dir, keys, size=48, display_name_to_username=None):
    """加载用户头像"""
    try:
        head_enc = os.path.join(db_dir, 'head_image/head_image.db')
        if not os.path.exists(head_enc):
            return None
            
        head_key = bytes.fromhex(keys['head_image/head_image.db']['enc_key'])
        head_tmp = '/tmp/head_image_avatar.db'
        
        # 简化解密（假设已解密或使用缓存）
        if not os.path.exists(head_tmp):
            from crypto.decrypt import full_decrypt, decrypt_wal
            full_decrypt(head_enc, head_tmp, head_key)
            wal = head_enc + '-wal'
            if os.path.exists(wal):
                decrypt_wal(wal, head_tmp, head_key)
        
        # 如果传入了显示名到用户名的映射，尝试转换
        actual_username = username
        if display_name_to_username and username in display_name_to_username:
            actual_username = display_name_to_username[username]
        
        conn = sqlite3.connect(head_tmp)
        c = conn.cursor()
        c.execute("SELECT image_buffer FROM head_image WHERE username=?", (actual_username,))
        row = c.fetchone()
        conn.close()
        
        if row and row[0]:
            # 保存为临时文件
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
                f.write(row[0])
                tmp_path = f.name
            
            # 加载并调整大小
            avatar = Image.open(tmp_path)
            avatar = avatar.resize((size, size), Image.LANCZOS)
            
            # 创建圆形遮罩
            mask = Image.new('L', (size, size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse([0, 0, size - 1, size - 1], fill=255)
            
            # 应用圆形遮罩
            result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            result.paste(avatar, mask=mask)
            
            # 清理临时文件
            os.unlink(tmp_path)
            
            return result
    except Exception as e:
        pass
    
    return None


def draw_avatar_placeholder(draw, x, y, size, name, color):
    """绘制头像占位符（首字母）"""
    # 圆形背景
    draw.ellipse([x, y, x + size, y + size], fill=color)
    
    # 首字母
    font = get_font(size // 2)
    first_char = name[0] if name else '?'
    bbox = font.getbbox(first_char)
    char_w = bbox[2] - bbox[0]
    char_h = bbox[3] - bbox[1]
    draw.text((x + (size - char_w) // 2, y + (size - char_h) // 2 - 2), 
              first_char, fill=(255, 255, 255), font=font)


# ── JSON Schema ──

SCHEMA = """{
  "header": {
    "title": "群名称",
    "date": "2026-05-29",
    "stats": "23 人参与 · 206 条消息 · 文本 151 · 图片 27",
    "hot_word": "强的可怕"
  },
  "summary": [
    "一句话总结第一条",
    "一句话总结第二条",
    "一句话总结第三条"
  ],
  "topics": [
    {
      "title": "话题标题",
      "time": "10:00 - 11:00",
      "summary": "一句话摘要",
      "detail": "详细分析段落，可以多段用换行分隔。",
      "quotes": [
        ["发言人", "引用内容"],
        ["发言人2", "引用内容2"]
      ]
    }
  ]
}"""


# ── 渲染 ──

def calc_topic_height(topic, f_body, f_quote, f_name, content_w, s, avatars_enabled):
    """计算话题高度"""
    h = int(28 * s)
    h += int(20 * s)
    h += int(4 * s)
    detail_lines = wrap_text(topic["detail"], f_body, content_w - int(16 * s))
    h += len(detail_lines) * int(18 * s)
    h += int(10 * s)
    
    avatar_size = int(32 * s) if avatars_enabled else 0
    
    for name, q in topic.get("quotes", []):
        h += int(18 * s)
        ql = wrap_text(q, f_quote, content_w - int(60 * s) - avatar_size)
        h += len(ql) * int(16 * s)
        h += int(8 * s)
    h += int(16 * s)
    return h


def calc_summary_height(summary_items, f_summary, content_w, s):
    """计算省流版高度"""
    h = int(24 * s)  # 标题
    h += int(8 * s)   # 间隔
    for item in summary_items:
        lines = wrap_text(item, f_summary, content_w - int(20 * s))
        h += len(lines) * int(16 * s)
        h += int(6 * s)  # 行间距
    h += int(8 * s)  # 底部间隔
    return h


def render(data, output_path, scale=2, db_dir=None, keys=None):
    """scale: 缩放倍数，2=2x 高清"""
    import random
    
    s = scale
    W = BASE_W * s
    PAD = BASE_PAD * s
    CONTENT_W = BASE_CONTENT_W * s

    f_title = get_font(int(22 * s))
    f_meta = get_font(int(13 * s))
    f_sec = get_font(int(16 * s))
    f_body = get_font(int(13 * s))
    f_quote = get_font(int(12 * s))
    f_name = get_font(int(12 * s))
    f_footer = get_font(int(11 * s))
    f_tag = get_font(int(11 * s))

    header = data["header"]
    topics = data["topics"]

    # 构建显示名到用户名的映射（用于头像查找）
    display_name_to_username = {}
    if db_dir and keys:
        try:
            contact_enc = os.path.join(db_dir, 'contact/contact.db')
            contact_key = bytes.fromhex(keys['contact/contact.db']['enc_key'])
            contact_tmp = '/tmp/contact_avatar_map.db'
            
            if not os.path.exists(contact_tmp):
                from crypto.decrypt import full_decrypt, decrypt_wal
                full_decrypt(contact_enc, contact_tmp, contact_key)
                wal = contact_enc + '-wal'
                if os.path.exists(wal):
                    decrypt_wal(wal, contact_tmp, contact_key)
            
            import sqlite3
            conn = sqlite3.connect(contact_tmp)
            c = conn.cursor()
            c.execute("SELECT username, nick_name, remark FROM contact")
            for r in c.fetchall():
                username, nick_name, remark = r
                display_name = remark if remark else (nick_name if nick_name else username)
                if display_name and display_name != username:
                    display_name_to_username[display_name] = username
            conn.close()
        except Exception:
            pass

    # 计算高度
    avatars_enabled = db_dir is not None and keys is not None
    H = int(60 * s)
    H += int(32 * s) + int(8 * s) + int(20 * s) + int(24 * s) + int(16 * s)
    
    # 省流版高度
    summary_items = data.get("summary", [])
    if summary_items:
        f_summary = get_font(int(12 * s))
        H += calc_summary_height(summary_items, f_summary, CONTENT_W, s)
        H += int(8 * s)  # 额外间隔
    
    for t in topics:
        H += calc_topic_height(t, f_body, f_quote, f_name, CONTENT_W, s, avatars_enabled)
    H += int(50 * s)

    # 创建纸张纹理背景
    img = create_paper_texture(W, H)
    draw = ImageDraw.Draw(img)

    y = 20
    
    # 创建卡片纹理
    card_img = create_card_texture(W - 40, H - 40)
    card_mask = Image.new('L', (W - 40, H - 40), 0)
    card_draw = ImageDraw.Draw(card_mask)
    card_draw.rounded_rectangle([0, 0, W - 41, H - 41], radius=16, fill=255)
    
    # 粘贴卡片
    img.paste(card_img, (20, y), card_mask)
    
    cy = y + int(36 * s)

    # 标题
    t = header["title"]
    bb = f_title.getbbox(t)
    title_w = bb[2] - bb[0]
    
    # 标题装饰线
    draw.rounded_rectangle([(W - title_w - int(24 * s)) // 2, cy - int(2 * s), 
                           (W + title_w + int(24 * s)) // 2, cy + int(4 * s)], 
                          radius=int(2 * s), fill=ACCENT[0])
    
    draw.text(((W - title_w) // 2, cy + int(8 * s)), t, fill=C_TITLE, font=f_title)
    cy += int(40 * s)

    # 日期和统计
    m = f"📅 {header['date']}  ·  {header['stats']}"
    bb = f_meta.getbbox(m)
    draw.text(((W - (bb[2] - bb[0])) // 2, cy), m, fill=C_META, font=f_meta)
    cy += int(20 * s)

    # 热词标签
    hw = f"🔥 热词：{header['hot_word']}"
    bb = f_tag.getbbox(hw)
    hw_w = bb[2] - bb[0] + int(20 * s)
    
    # 标签阴影
    draw.rounded_rectangle([(W - hw_w) // 2 + 1, cy + 1, (W + hw_w) // 2 + 1, cy + int(22 * s) + 1],
                           radius=int(8 * s), fill=(0, 0, 0, 8))
    draw.rounded_rectangle([(W - hw_w) // 2, cy, (W + hw_w) // 2, cy + int(22 * s)],
                           radius=int(8 * s), fill=(239, 68, 68))
    draw.text(((W - (bb[2] - bb[0])) // 2, cy + int(4 * s)), hw, fill=(255, 255, 255), font=f_tag)
    cy += int(34 * s)

    # 分隔线
    draw.line([(PAD + int(24 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
    cy += int(16 * s)

    # 省流版
    if summary_items:
        # 省流版标题
        draw.rounded_rectangle([PAD, cy, PAD + int(4 * s), cy + int(18 * s)],
                               radius=int(2 * s), fill=ACCENT[1])
        draw.text((PAD + int(10 * s), cy), "📌 省流版", fill=C_TITLE, font=f_sec)
        cy += int(24 * s)
        
        # 省流版内容
        for item in summary_items:
            # 圆点
            draw.ellipse([PAD + int(10 * s), cy + int(6 * s), 
                         PAD + int(14 * s), cy + int(10 * s)], fill=C_META)
            
            for line in wrap_text(item, f_summary, CONTENT_W - int(20 * s)):
                draw.text((PAD + int(20 * s), cy), line, fill=C_BODY, font=f_summary)
                cy += int(16 * s)
            cy += int(6 * s)
        
        cy += int(8 * s)
        draw.line([(PAD + int(24 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
        cy += int(16 * s)

    # 话题
    avatar_size = int(32 * s) if avatars_enabled else 0
    
    for si, topic in enumerate(topics):
        color = ACCENT[si % len(ACCENT)]

        # 编号徽章
        nt = str(si + 1)
        badge = int(22 * s)
        
        # 徽章阴影
        draw.rounded_rectangle([PAD + 1, cy + 1, PAD + badge + 1, cy + badge + 1], 
                               radius=int(6 * s), fill=(0, 0, 0, 8))
        draw.rounded_rectangle([PAD, cy, PAD + badge, cy + badge], radius=int(6 * s), fill=color)
        
        nb = f_name.getbbox(nt)
        draw.text((PAD + (badge - (nb[2] - nb[0])) // 2,
                   cy + (badge - (nb[3] - nb[1])) // 2 - s),
                  nt, fill=(255, 255, 255), font=f_name)

        # 话题标题
        draw.text((PAD + int(30 * s), cy + s), topic["title"], fill=C_TITLE, font=f_sec)

        # 时间标签
        tw = f_sec.getbbox(topic["title"])[2]
        draw.text((PAD + int(30 * s) + tw + int(10 * s), cy + int(5 * s)), topic["time"], fill=C_META, font=f_tag)
        cy += int(28 * s)

        # 一句话摘要
        draw.text((PAD + int(30 * s), cy), topic["summary"], fill=color, font=f_body)
        cy += int(20 * s)

        # 详细分析
        cy += int(4 * s)
        for line in wrap_text(topic["detail"], f_body, CONTENT_W - int(16 * s)):
            draw.text((PAD + int(30 * s), cy), line, fill=C_BODY, font=f_body)
            cy += int(18 * s)
        cy += int(8 * s)

        # 关键引用
        for name, q in topic.get("quotes", []):
            # 引用条背景
            draw.rounded_rectangle([PAD + int(30 * s), cy, W - PAD - int(24 * s), cy + int(20 * s)],
                                   radius=int(4 * s), fill=C_QUOTE_BG)
            
            # 头像
            if avatars_enabled:
                avatar = load_avatar(name, db_dir, keys, avatar_size, display_name_to_username)
                if avatar:
                    img.paste(avatar, (PAD + int(30 * s), cy), avatar)
                else:
                    draw_avatar_placeholder(draw, PAD + int(30 * s), cy, avatar_size, name, color)
                text_x = PAD + int(30 * s) + avatar_size + int(8 * s)
            else:
                draw.rounded_rectangle([PAD + int(30 * s), cy, PAD + int(34 * s), cy + int(16 * s)],
                                       radius=2, fill=C_DIV)
                text_x = PAD + int(42 * s)
            
            draw.text((text_x, cy + int(2 * s)), name, fill=color, font=f_name)
            cy += int(20 * s)
            
            for line in wrap_text(q, f_quote, CONTENT_W - int(60 * s) - avatar_size):
                draw.text((text_x, cy), line, fill=C_BODY_LIGHT, font=f_quote)
                cy += int(16 * s)
            cy += int(8 * s)

        # 分隔线
        if si < len(topics) - 1:
            cy += int(4 * s)
            draw.line([(PAD + int(30 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
            cy += int(12 * s)

    # 底部
    cy += int(8 * s)
    draw.line([(PAD + int(24 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
    cy += int(14 * s)
    
    footer = "烟囱鸭 · 群聊日报自动生成"
    bb = f_footer.getbbox(footer)
    footer_w = bb[2] - bb[0]
    
    # 底部装饰
    draw.rounded_rectangle([(W - footer_w - int(16 * s)) // 2, cy - int(4 * s), 
                           (W + footer_w + int(16 * s)) // 2, cy + int(18 * s)], 
                          radius=int(8 * s), fill=C_QUOTE_BG)
    draw.text(((W - footer_w) // 2, cy), footer, fill=C_META, font=f_footer)

    img.save(output_path, "PNG")
    return f"{output_path} ({W}x{H})"


# ── 入口 ──

def main():
    parser = argparse.ArgumentParser(description="群聊日报长图生成器 - 纸张纹理版")
    parser.add_argument("--input", "-i", help="输入 JSON 文件路径")
    parser.add_argument("--output", "-o", default="/tmp/group-summary.png", help="输出图片路径")
    parser.add_argument("--scale", "-s", type=int, default=2, help="缩放倍数，默认 2（2x 高清）")
    parser.add_argument("--schema", action="store_true", help="输出 JSON schema")
    parser.add_argument("--no-avatars", action="store_true", help="不显示头像")
    args = parser.parse_args()

    if args.schema:
        print(SCHEMA)
        return

    if args.input:
        with open(args.input) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    # 尝试加载数据库配置
    db_dir = None
    keys = None
    
    if not args.no_avatars:
        try:
            # 尝试从当前目录加载
            sys.path.insert(0, os.getcwd())
            from crypto.config import load_config
            cfg, keys_file = load_config()
            db_dir = cfg['db_dir']
            with open(keys_file) as f:
                keys = json.load(f)
        except Exception:
            pass

    result = render(data, args.output, scale=args.scale, db_dir=db_dir, keys=keys)
    print(f"Saved: {result}")


if __name__ == "__main__":
    main()
