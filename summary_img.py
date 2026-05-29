#!/usr/bin/env python3
"""
群聊日报长图生成器
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
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("需要安装 Pillow: pip3 install Pillow --break-system-packages", file=sys.stderr)
    sys.exit(1)

# ── 配置 ──

BASE_W = 780
BASE_PAD = 40
BASE_CONTENT_W = BASE_W - 2 * BASE_PAD - 24

# 颜色
BG = (245, 245, 247)
CARD = (255, 255, 255)
C_TITLE = (15, 23, 42)
C_BODY = (51, 65, 85)
C_BODY_LIGHT = (100, 116, 139)
C_META = (100, 116, 139)
C_DIV = (226, 232, 240)
C_QUOTE_BAR = (203, 213, 225)
ACCENT = [
    (59, 130, 246), (16, 185, 129), (245, 158, 11),
    (239, 68, 68), (139, 92, 246), (236, 72, 153), (20, 184, 166),
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


# ── JSON Schema ──

SCHEMA = """{
  "header": {
    "title": "群名称",
    "date": "2026-05-29",
    "stats": "23 人参与 · 206 条消息 · 文本 151 · 图片 27",
    "hot_word": "强的可怕"
  },
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

def calc_topic_height(topic, f_body, f_quote, f_name, content_w, s):
    h = int(28 * s)
    h += int(20 * s)
    h += int(4 * s)
    detail_lines = wrap_text(topic["detail"], f_body, content_w - int(16 * s))
    h += len(detail_lines) * int(18 * s)
    h += int(10 * s)
    for name, q in topic.get("quotes", []):
        h += int(18 * s)
        ql = wrap_text(q, f_quote, content_w - int(60 * s))
        h += len(ql) * int(16 * s)
        h += int(6 * s)
    h += int(16 * s)
    return h


def render(data, output_path, scale=2):
    """scale: 缩放倍数，2=2x 高清"""
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

    # 计算高度
    H = int(60 * s)
    H += int(32 * s) + int(8 * s) + int(20 * s) + int(24 * s) + int(16 * s)
    for t in topics:
        H += calc_topic_height(t, f_body, f_quote, f_name, CONTENT_W, s)
    H += int(50 * s)

    # 创建画布
    img = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(img)

    y = 20
    draw.rounded_rectangle([20, y, W - 20, H - 20], radius=16, fill=CARD)
    cy = y + int(36 * s)

    # 标题
    t = header["title"]
    bb = f_title.getbbox(t)
    draw.text(((W - (bb[2] - bb[0])) // 2, cy), t, fill=C_TITLE, font=f_title)
    cy += int(32 * s)

    # 日期和统计
    m = f"📅 {header['date']}  ·  {header['stats']}"
    bb = f_meta.getbbox(m)
    draw.text(((W - (bb[2] - bb[0])) // 2, cy), m, fill=C_META, font=f_meta)
    cy += int(20 * s)

    # 热词标签
    hw = f"🔥 热词：{header['hot_word']}"
    bb = f_tag.getbbox(hw)
    hw_w = bb[2] - bb[0] + int(20 * s)
    draw.rounded_rectangle([(W - hw_w) // 2, cy, (W + hw_w) // 2, cy + int(22 * s)],
                           radius=int(8 * s), fill=(239, 68, 68))
    draw.text(((W - (bb[2] - bb[0])) // 2, cy + int(4 * s)), hw, fill=(255, 255, 255), font=f_tag)
    cy += int(34 * s)

    draw.line([(PAD + int(24 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
    cy += int(16 * s)

    # 话题
    for si, topic in enumerate(topics):
        color = ACCENT[si % len(ACCENT)]

        # 编号徽章
        nt = str(si + 1)
        badge = int(22 * s)
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
            draw.rounded_rectangle([PAD + int(30 * s), cy, PAD + int(34 * s), cy + int(16 * s)],
                                   radius=2, fill=C_QUOTE_BAR)
            draw.text((PAD + int(42 * s), cy), name, fill=color, font=f_name)
            cy += int(16 * s)
            for line in wrap_text(q, f_quote, CONTENT_W - int(60 * s)):
                draw.text((PAD + int(42 * s), cy), line, fill=C_BODY_LIGHT, font=f_quote)
                cy += int(16 * s)
            cy += int(6 * s)

        # 分隔线
        if si < len(topics) - 1:
            cy += int(4 * s)
            draw.line([(PAD + int(30 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
            cy += int(12 * s)

    # 底部
    cy += int(8 * s)
    draw.line([(PAD + int(24 * s), cy), (W - PAD - int(24 * s), cy)], fill=C_DIV, width=s)
    cy += int(14 * s)
    footer = "Hana · 群聊日报自动生成"
    bb = f_footer.getbbox(footer)
    draw.text(((W - (bb[2] - bb[0])) // 2, cy), footer, fill=C_META, font=f_footer)

    img.save(output_path, "PNG")
    return f"{output_path} ({W}x{H})"


# ── 入口 ──

def main():
    parser = argparse.ArgumentParser(description="群聊日报长图生成器")
    parser.add_argument("--input", "-i", help="输入 JSON 文件路径")
    parser.add_argument("--output", "-o", default="/tmp/group-summary.png", help="输出图片路径")
    parser.add_argument("--scale", "-s", type=int, default=2, help="缩放倍数，默认 2（2x 高清）")
    parser.add_argument("--schema", action="store_true", help="输出 JSON schema")
    args = parser.parse_args()

    if args.schema:
        print(SCHEMA)
        return

    if args.input:
        with open(args.input) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    result = render(data, args.output, scale=args.scale)
    print(f"Saved: {result}")


if __name__ == "__main__":
    main()
