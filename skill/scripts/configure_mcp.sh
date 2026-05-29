#!/bin/bash
# MCP Server 配置（幂等）
set -e

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-mcp-macos"
VENV_DIR="$PROJECT_DIR/backend/.venv"
MCP_DIR="$HOME/.wechat-mcp"

echo "=== MCP Server 配置 ==="

# 1. 配置密钥文件
if [ -f "$PROJECT_DIR/wechat_keys.json" ]; then
    if [ -f "$MCP_DIR/all_keys.json" ]; then
        echo "⏭️  all_keys.json 已存在，跳过"
    else
        echo "📝 生成 all_keys.json..."
        mkdir -p "$MCP_DIR"
        python3 -c "
import json, os
with open('$PROJECT_DIR/wechat_keys.json') as f:
    data = json.load(f)
new_keys = {}
for k, v in data.items():
    if k == '__salts__':
        continue
    new_keys[k] = {'enc_key': v}
with open('$MCP_DIR/all_keys.json', 'w') as f:
    json.dump(new_keys, f, indent=2)
print(f'✅ 生成 {len(new_keys)} 个密钥')
"
    fi
fi

# 2. 配置 config.json
if [ -f "$MCP_DIR/config.json" ]; then
    echo "⏭️  config.json 已存在，跳过"
else
    echo "📝 生成 config.json..."
    mkdir -p "$MCP_DIR"
    # 自动检测 db_dir
    db_dir=$(ls -d ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*/db_storage 2>/dev/null | head -1)
    if [ -n "$db_dir" ]; then
        python3 -c "
import json, os
config = {
    'db_dir': '$db_dir',
    'keys_file': '$MCP_DIR/all_keys.json',
    'decrypted_dir': '$MCP_DIR/decrypted',
    'self_name': ''
}
with open('$MCP_DIR/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print('✅ config.json 已生成')
"
    else
        echo "⚠️  未找到微信数据目录"
    fi
fi

# 3. 补丁 sender.py
sender_py=$(ls "$VENV_DIR/lib/python3.*/site-packages/wechat_mcp_macos/sender.py" 2>/dev/null | head -1)
if [ -n "$sender_py" ]; then
    if grep -q 're.findall' "$sender_py" 2>/dev/null; then
        echo "⏭️  sender.py 已补丁，跳过"
    else
        echo "🔧 补丁 sender.py..."
        # 备份
        cp "$sender_py" "${sender_py}.bak"
        
        # 添加 import re
        sed -i '' 's/^import logging/import logging\nimport re/' "$sender_py"
        
        # 替换 get_window_info 中的解析逻辑
        python3 -c "
import re

with open('$sender_py', 'r') as f:
    content = f.read()

# Replace the parsing block
old = '''    try:
        parts = [int(p.strip()) for p in output.split(\",\")]
        return {\"x\": parts[0], \"y\": parts[1], \"width\": parts[2], \"height\": parts[3]}
    except (ValueError, IndexError):'''

new = '''    try:
        # AppleScript list-to-string may add extra commas/spaces
        numbers = re.findall(r\"\\\\d+\", output)
        if len(numbers) >= 4:
            return {\"x\": int(numbers[0]), \"y\": int(numbers[1]), \"width\": int(numbers[2]), \"height\": int(numbers[3])}
        parts = [int(p.strip()) for p in output.split(\",\") if p.strip().isdigit()]
        return {\"x\": parts[0], \"y\": parts[1], \"width\": parts[2], \"height\": parts[3]}
    except (ValueError, IndexError):'''

content = content.replace(old, new)

# Replace search_and_select_chat
old_search = '''def search_and_select_chat(chat_name: str) -> tuple[bool, str]:
    \"\"\"Search for a contact/group in WeChat and select it.

    Uses the search box to find and click on the chat.
    \"\"\"
    ok, msg = activate_wechat()
    if not ok:
        return False, f\"Failed to activate WeChat: {msg}\"

    time.sleep(0.3)
    win = get_window_info()
    if not win:
        return False, \"Cannot get WeChat window info\"

    # Search box position: ~17% from left, ~50px from top
    search_x = win[\"x\"] + int(win[\"width\"] * 0.17)
    search_y = win[\"y\"] + 50

    # Click search box, paste chat name, wait for results
    script = f\"\"\"
        set the clipboard to \"{chat_name}\"
        tell application \"System Events\"
            tell process \"WeChat\"
                set frontmost to true
                delay 0.2
                click at {{{search_x}, {search_y}}}
                delay 0.3
                keystroke \"v\" using command down
                delay 0.8
                -- Press Return to select first result
                key code 36
                delay 0.3
            end tell
        end tell
    \"\"\"
    return _run_osascript(script)'''

new_search = '''def search_and_select_chat(chat_name: str) -> tuple[bool, str]:
    \"\"\"Search for a contact/group in WeChat and select it.

    Uses Cmd+F to open search, types the chat name, and selects the first result.
    \"\"\"
    ok, msg = activate_wechat()
    if not ok:
        return False, f\"Failed to activate WeChat: {msg}\"

    time.sleep(0.3)

    # Set clipboard with the chat name (handles CJK)
    escaped_name = chat_name.replace(\"\\\\\", \"\\\\\\\\\").replace('\"', '\\\\\"')
    script = f\"\"\"
        set the clipboard to \"{escaped_name}\"
        tell application \"System Events\"
            tell process \"WeChat\"
                set frontmost to true
                delay 0.2
                -- Open search with Cmd+F
                keystroke \"f\" using command down
                delay 0.5
                -- Paste chat name into search field
                keystroke \"v\" using command down
                delay 1.0
                -- Press Down to move to first result, then Enter to select
                key code 125
                delay 0.3
                key code 36
                delay 0.5
            end tell
        end tell
    \"\"\"
    return _run_osascript(script)'''

content = content.replace(old_search, new_search)

with open('$sender_py', 'w') as f:
    f.write(content)

print('✅ sender.py 已补丁')
"
    fi
else
    echo "⚠️  sender.py 未找到"
fi

echo ""
echo "=== MCP 配置完成 ==="
