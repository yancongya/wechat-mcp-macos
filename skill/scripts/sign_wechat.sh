#!/bin/bash
# 微信重签名（幂等）
# ⚠️ 需要用户配合：退出微信 + sudo 密码

WECHAT_APP="/Applications/WeChat.app"

echo "=== 微信重签名 ==="

# 检查签名状态
sig_flags=$(codesign -dv "$WECHAT_APP" 2>&1 | grep "flags=" | awk '{print $NF}')

if echo "$sig_flags" | grep -q "0x2"; then
    echo "⏭️  微信已是 ad-hoc 签名，跳过"
    exit 0
fi

# 检查微信是否运行
if pgrep -x "WeChat" &>/dev/null; then
    echo "⚠️  微信正在运行，请先退出微信"
    echo "    退出后按回车继续..."
    read -r
fi

# 清除扩展属性
echo "🧹 清除扩展属性..."
sudo xattr -cr "$WECHAT_APP" 2>/dev/null || true

# 重签名
echo "✍️  重签名微信..."
sudo codesign --force --deep --sign - "$WECHAT_APP"

# 验证
new_flags=$(codesign -dv "$WECHAT_APP" 2>&1 | grep "flags=" | awk '{print $NF}')
if echo "$new_flags" | grep -q "0x2"; then
    echo "✅ 微信重签名成功"
    echo ""
    echo "接下来请："
    echo "  1. 重新打开微信并登录"
    echo "  2. 登录后运行密钥提取脚本"
else
    echo "❌ 重签名失败，请检查："
    echo "  - 是否是 Mac App Store 版本（可能不支持）"
    echo "  - 尝试从官网下载微信安装"
fi
