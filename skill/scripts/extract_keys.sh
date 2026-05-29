#!/bin/bash
# 密钥提取（幂等）
# ⚠️ 需要：微信在运行 + sudo 密码

set -e

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-mcp-macos"
LLVM_DIR=$(ls -d /opt/homebrew/Cellar/llvm/*/libexec/python3.* 2>/dev/null | sort -V | tail -1)

echo "=== 密钥提取 ==="

# 检查密钥是否已存在且有效
if [ -f "$PROJECT_DIR/key.txt" ]; then
    key_len=$(wc -c < "$PROJECT_DIR/key.txt" | tr -d ' ')
    if [ "$key_len" -ge 64 ]; then
        # 验证密钥能否解密
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
                echo "⏭️  密钥已存在且有效，跳过"
                # 确保 also 转换为 wechat-mcp-macos 格式
                bash "$(dirname "$0")/configure_mcp.sh"
                exit 0
            fi
        fi
    fi
fi

# 检查微信是否运行
if ! pgrep -x "WeChat" &>/dev/null; then
    echo "❌ 微信未运行，请先打开微信并登录"
    exit 1
fi

# 检查签名状态
sig_flags=$(codesign -dv "/Applications/WeChat.app" 2>&1 | grep "flags=" | awk '{print $NF}')
if ! echo "$sig_flags" | grep -q "0x2"; then
    echo "❌ 微信未重签名，请先运行 sign_wechat.sh"
    exit 1
fi

# 检查 lldb Python 路径
if [ -z "$LLVM_DIR" ]; then
    echo "❌ 未找到 llvm lldb Python bindings"
    echo "   请确认已运行 install_deps.sh"
    exit 1
fi

echo "📦 从内存提取密钥..."
echo "   (需要 sudo 权限，会弹出密码输入框)"

# 克隆密钥提取脚本（如果不存在）
if [ ! -f "/tmp/wechat-db-decrypt-macos/find_key_memscan.py" ]; then
    echo "📥 下载密钥提取工具..."
    cd /tmp
    git clone --depth 1 https://github.com/Thearas/wechat-db-decrypt-macos.git 2>/dev/null
fi

# 执行提取
cd "$PROJECT_DIR"
PYTHONPATH="$LLVM_DIR" sudo env PYTHONPATH="$LLVM_DIR" \
    python3 /tmp/wechat-db-decrypt-macos/find_key_memscan.py

# 验证
if [ -f "$PROJECT_DIR/wechat_keys.json" ]; then
    key_count=$(python3 -c "import json; d=json.load(open('$PROJECT_DIR/wechat_keys.json')); print(len([k for k in d if k!='__salts__']))" 2>/dev/null)
    echo "✅ 提取到 $key_count 个数据库密钥"
    
    # 转换为 key.txt（使用 message_0.db 的密钥）
    python3 -c "
import json
with open('$PROJECT_DIR/wechat_keys.json') as f:
    data = json.load(f)
key = data.get('message/message_0.db', '')
if key:
    with open('$PROJECT_DIR/key.txt', 'w') as f:
        f.write(key)
    print('✅ key.txt 已生成')
"
    
    # 配置 wechat-mcp-macos
    bash "$(dirname "$0")/configure_mcp.sh"
else
    echo "❌ 密钥提取失败"
    echo "   查看日志: $HOME/.wechat-mcp/extract_keys.log"
    exit 1
fi
