#!/bin/bash
# 部署项目（幂等）
set -e

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-decrypt-macos"

echo "=== 部署项目 ==="

# 1. 克隆项目
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "⏭️  项目已克隆，跳过"
else
    echo "📦 克隆 wechat-decrypt-macos..."
    cd "$HOME/Desktop/OH-WorkSpace"
    git clone https://github.com/cocohahaha/wechat-decrypt-macos.git
    echo "✅ 项目克隆完成"
fi

# 2. 创建虚拟环境
if [ -d "$PROJECT_DIR/.venv" ]; then
    echo "⏭️  虚拟环境已创建，跳过"
else
    echo "🐍 创建虚拟环境..."
    cd "$PROJECT_DIR"
    python3 -m venv .venv
    echo "✅ 虚拟环境创建完成"
fi

# 3. 安装 MCP 依赖
cd "$PROJECT_DIR"
source .venv/bin/activate

if python3 -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; then
    echo "⏭️  MCP 依赖已安装，跳过"
else
    echo "📦 安装 MCP 依赖..."
    pip install "mcp[cli]>=1.0.0" --quiet
    echo "✅ MCP 依赖安装完成"
fi

# 4. 安装 wechat-mcp-macos
if python3 -c "import wechat_mcp_macos" 2>/dev/null; then
    echo "⏭️  wechat-mcp-macos 已安装，跳过"
else
    echo "📦 安装 wechat-mcp-macos..."
    pip install wechat-mcp-macos --quiet
    echo "✅ wechat-mcp-macos 安装完成"
fi

# 5. 安装 zstandard
if python3 -c "import zstandard" 2>/dev/null; then
    echo "⏭️  zstandard 已安装，跳过"
else
    echo "📦 安装 zstandard..."
    pip install zstandard --quiet
    echo "✅ zstandard 安装完成"
fi

echo ""
echo "=== 项目部署完成 ==="
