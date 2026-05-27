#!/bin/bash
# 安装依赖（幂等）
set -e

echo "=== 安装依赖 ==="

# sqlcipher
if command -v sqlcipher &>/dev/null; then
    echo "⏭️  sqlcipher 已安装，跳过"
else
    echo "📦 安装 sqlcipher..."
    brew install sqlcipher
    echo "✅ sqlcipher 安装完成"
fi

# llvm
if [ -d "/opt/homebrew/Cellar/llvm" ] || [ -d "/usr/local/Cellar/llvm" ]; then
    echo "⏭️  llvm 已安装，跳过"
else
    echo "📦 安装 llvm..."
    brew install llvm
    echo "✅ llvm 安装完成"
fi

echo ""
echo "=== 依赖安装完成 ==="
