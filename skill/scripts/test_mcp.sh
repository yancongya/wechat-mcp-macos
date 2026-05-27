#!/bin/bash
# 功能测试（幂等）
set -e

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-decrypt-macos"

echo "=== 功能测试 ==="

cd "$PROJECT_DIR"
source .venv/bin/activate

# 测试 1: 读取功能
echo "── 测试 1: 读取消息 ──"
result=$(python3 -c "
import sys
sys.path.insert(0, '.')
from wechat_mcp_macos.config import load_config, KEYS_FILE
from wechat_mcp_macos.db import WeChatDB
import json

cfg = load_config()
with open(str(KEYS_FILE)) as f:
    keys = json.load(f)
db = WeChatDB(cfg['db_dir'], keys)

# 找一个群读取消息
groups = db.get_groups()
if groups:
    g = groups[0]
    msgs = db.get_messages(g['username'], limit=3)
    if msgs:
        print(f'OK: {g[\"name\"]} - {len(msgs)} messages')
    else:
        print('WARN: group found but no messages')
else:
    print('FAIL: no groups found')
" 2>&1)

if echo "$result" | grep -q "^OK:"; then
    echo "✅ 读取测试通过: $result"
else
    echo "❌ 读取测试失败: $result"
fi

# 测试 2: 搜索功能
echo "── 测试 2: 搜索消息 ──"
result=$(python3 -c "
import sys, time
sys.path.insert(0, '.')
from wechat_mcp_macos.config import load_config, KEYS_FILE
from wechat_mcp_macos.db import WeChatDB
import json

cfg = load_config()
with open(str(KEYS_FILE)) as f:
    keys = json.load(f)
db = WeChatDB(cfg['db_dir'], keys)

# 搜索最近24小时
groups = db.get_groups()
if groups:
    usernames = [g['username'] for g in groups[:5]]
    results = db.search_messages(['测试'], usernames, time.time() - 86400, time.time())
    total = sum(len(msgs) for msgs in results.values())
    print(f'OK: searched {len(usernames)} groups, found {total} matches')
else:
    print('FAIL: no groups')
" 2>&1)

if echo "$result" | grep -q "^OK:"; then
    echo "✅ 搜索测试通过: $result"
else
    echo "❌ 搜索测试失败: $result"
fi

# 测试 3: 发送功能（仅检查窗口，不实际发送）
echo "── 测试 3: 发送能力检查 ──"
win_info=$(osascript -e '
tell application "System Events"
    tell process "WeChat"
        set frontmost to true
        set p to position of window 1
        set s to size of window 1
        return (item 1 of p) & " " & (item 2 of p) & " " & (item 1 of s) & " " & (item 2 of s)
    end tell
end tell
' 2>/dev/null)

if echo "$win_info" | grep -qE '[0-9]'; then
    echo "✅ 发送能力正常 (窗口可访问)"
else
    echo "❌ 发送能力不可用 (辅助功能权限问题)"
fi

echo ""
echo "=== 测试完成 ==="
