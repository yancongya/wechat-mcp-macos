#!/bin/bash
# WeChat MCP 环境检查（幂等）
# 输出每一步的状态：✅ 已完成 / ❌ 缺失 / ⚠️ 需要操作

set -o pipefail

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-mcp-macos"
VENV_DIR="$PROJECT_DIR/backend/.venv"
MCP_DIR="$HOME/.wechat-mcp"
WECHAT_APP="/Applications/WeChat.app"

ok() { echo "✅ $1"; }
fail() { echo "❌ $1"; }
warn() { echo "⚠️ $1"; }
skip() { echo "⏭️  $1"; }

echo "=== WeChat MCP 环境检查 ==="
echo ""

# ── Step 1: 基础环境 ──
echo "── 基础环境 ──"

# Python
if command -v python3 &>/dev/null; then
    py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    py_major=$(python3 -c 'import sys; print(sys.version_info.major)')
    py_minor=$(python3 -c 'import sys; print(sys.version_info.minor)')
    if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 10 ]; then
        ok "Python $py_ver"
    else
        fail "Python $py_ver (需要 ≥ 3.10)"
    fi
else
    fail "Python 3 未安装"
fi

# Homebrew
if command -v brew &>/dev/null; then
    ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
else
    fail "Homebrew 未安装"
fi

# sqlcipher
if command -v sqlcipher &>/dev/null; then
    ok "sqlcipher $(sqlcipher --version 2>/dev/null || echo 'installed')"
else
    fail "sqlcipher 未安装"
fi

# llvm (需要 lldb Python bindings)
if [ -d "/opt/homebrew/Cellar/llvm" ] || [ -d "/usr/local/Cellar/llvm" ]; then
    ok "llvm 已安装"
else
    fail "llvm 未安装"
fi

# Xcode CLT
if xcode-select -p &>/dev/null; then
    ok "Xcode Command Line Tools"
else
    fail "Xcode CLT 未安装 (xcode-select --install)"
fi

echo ""

# ── Step 2: 微信 ──
echo "── 微信 ──"

if [ -d "$WECHAT_APP" ]; then
    ok "WeChat.app 已安装"
    
    # 检查签名
    sig_flags=$(codesign -dv "$WECHAT_APP" 2>&1 | grep "flags=" | awk '{print $NF}')
    if echo "$sig_flags" | grep -q "0x2"; then
        ok "微信签名: ad-hoc (已重签名)"
    elif echo "$sig_flags" | grep -q "0x1"; then
        fail "微信签名: hardened runtime (需要重签名)"
    else
        warn "微信签名: 未知 ($sig_flags)"
    fi
    
    # 检查微信是否运行
    if pgrep -x "WeChat" &>/dev/null; then
        ok "微信正在运行"
    else
        warn "微信未运行"
    fi
else
    fail "WeChat.app 未安装"
fi

echo ""

# ── Step 3: 项目部署 ──
echo "── 项目部署 ──"

if [ -d "$PROJECT_DIR/.git" ]; then
    ok "项目已克隆"
else
    fail "项目未克隆"
fi

if [ -d "$VENV_DIR" ]; then
    ok "虚拟环境已创建"
else
    fail "虚拟环境未创建"
fi

if [ -f "$VENV_DIR/bin/python" ]; then
    # 测试 MCP import
    if "$VENV_DIR/bin/python" -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; then
        ok "MCP 依赖已安装"
    else
        fail "MCP 依赖未安装"
    fi
    
    # 测试 wechat-mcp-macos
    if "$VENV_DIR/bin/python" -c "import wechat_mcp_macos" 2>/dev/null; then
        ok "wechat-mcp-macos 已安装"
    else
        fail "wechat-mcp-macos 未安装"
    fi
    
    # 测试 zstandard
    if "$VENV_DIR/bin/python" -c "import zstandard" 2>/dev/null; then
        ok "zstandard 已安装"
    else
        fail "zstandard 未安装"
    fi
else
    fail "虚拟环境 Python 不存在"
fi

echo ""

# ── Step 4: 密钥 ──
echo "── 密钥 ──"

if [ -f "$PROJECT_DIR/key.txt" ]; then
    key_len=$(wc -c < "$PROJECT_DIR/key.txt" | tr -d ' ')
    if [ "$key_len" -ge 64 ]; then
        ok "key.txt 已存在 (${key_len} chars)"
    else
        fail "key.txt 内容不完整 (${key_len} chars, 需要 64)"
    fi
else
    fail "key.txt 不存在"
fi

if [ -f "$PROJECT_DIR/wechat_keys.json" ]; then
    key_count=$(python3 -c "import json; d=json.load(open('$PROJECT_DIR/wechat_keys.json')); print(len([k for k in d if k!='__salts__']))" 2>/dev/null || echo "0")
    if [ "$key_count" -gt 0 ]; then
        ok "wechat_keys.json ($key_count 个密钥)"
    else
        fail "wechat_keys.json 无有效密钥"
    fi
else
    fail "wechat_keys.json 不存在"
fi

# 验证密钥能否解密
if [ -f "$PROJECT_DIR/key.txt" ] && [ -f "$PROJECT_DIR/key.txt" ]; then
    msg_key=$(cat "$PROJECT_DIR/key.txt")
    db_path=$(ls ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/*/db_storage/message/message_0.db 2>/dev/null | head -1)
    if [ -n "$db_path" ]; then
        result=$(sqlcipher "$db_path" 2>/dev/null <<EOF
PRAGMA key = "x'$msg_key'";
PRAGMA cipher_compatibility = 4;
PRAGMA cipher_page_size = 4096;
SELECT count(*) FROM sqlite_master;
EOF
)
        if echo "$result" | grep -qE "^[0-9]+$"; then
            ok "密钥验证通过 (可解密 message_0.db)"
        else
            fail "密钥验证失败 (无法解密 message_0.db)"
        fi
    else
        warn "未找到 message_0.db，跳过密钥验证"
    fi
fi

echo ""

# ── Step 5: 联系人映射 ──
echo "── 联系人映射 ──"

if [ -f "$PROJECT_DIR/contacts.json" ]; then
    contact_count=$(python3 -c "import json; d=json.load(open('$PROJECT_DIR/contacts.json')); print(len(d))" 2>/dev/null || echo "0")
    if [ "$contact_count" -gt 0 ]; then
        ok "contacts.json ($contact_count 个联系人)"
    else
        warn "contacts.json 为空"
    fi
else
    fail "contacts.json 不存在"
fi

echo ""

# ── Step 6: MCP 配置 ──
echo "── MCP 配置 ──"

if [ -f "$MCP_DIR/config.json" ]; then
    ok "config.json 已配置"
else
    fail "config.json 未配置"
fi

if [ -f "$MCP_DIR/all_keys.json" ]; then
    mcp_keys=$(python3 -c "import json; d=json.load(open('$MCP_DIR/all_keys.json')); print(len(d))" 2>/dev/null || echo "0")
    if [ "$mcp_keys" -gt 0 ]; then
        ok "all_keys.json ($mcp_keys 个密钥)"
    else
        fail "all_keys.json 无密钥"
    fi
else
    fail "all_keys.json 不存在"
fi

# 检查 sender.py 补丁
sender_py="$VENV_DIR/lib/python3.*/site-packages/wechat_mcp_macos/sender.py"
if ls $sender_py 1>/dev/null 2>&1; then
    if grep -q 're.findall' $sender_py 2>/dev/null; then
        ok "sender.py 已补丁"
    else
        fail "sender.py 未补丁 (窗口解析会失败)"
    fi
else
    warn "sender.py 未找到"
fi

echo ""

# ── Step 7: 权限 ──
echo "── 权限 ──"

# 测试辅助功能
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
    ok "辅助功能权限正常"
else
    fail "辅助功能权限未授予 (系统设置 → 隐私与安全性 → 辅助功能)"
fi

echo ""

# ── 总结 ──
echo "=== 检查完成 ==="
echo ""

# 统计
total=0
passed=0
failed=0
warnings=0
while IFS= read -r line; do
    total=$((total + 1))
    case "$line" in
        ✅*) passed=$((passed + 1)) ;;
        ❌*) failed=$((failed + 1)) ;;
        ⚠️*) warnings=$((warnings + 1)) ;;
    esac
done < <(echo "$0" | bash 2>/dev/null)

# 简单统计（从本次输出）
echo "运行 'bash $0' 查看完整状态"
