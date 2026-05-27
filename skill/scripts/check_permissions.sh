#!/bin/bash
# 权限检查（幂等）

echo "=== 权限检查 ==="

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
    echo "✅ 辅助功能权限正常"
    echo "   窗口信息: $win_info"
else
    echo "❌ 辅助功能权限未授予"
    echo ""
    echo "请手动操作："
    echo "  1. 打开 系统设置 → 隐私与安全性 → 辅助功能"
    echo "  2. 点击 + 添加 Terminal.app"
    echo "     路径: /Applications/Utilities/Terminal.app"
    echo "  3. 确保已勾选开启"
    echo ""
    echo "添加后重新运行此脚本验证"
    exit 1
fi

echo ""
echo "=== 权限检查完成 ==="
