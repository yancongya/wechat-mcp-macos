#!/bin/bash
# 部署项目（幂等）
set -e

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-mcp-macos"
VENV_DIR="$PROJECT_DIR/backend/.venv"

echo "=== 部署项目 ==="

# 1. 克隆项目
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "⏭️  项目已克隆，跳过"
else
    echo "📦 克隆 wechat-mcp-macos..."
    cd "$HOME/Desktop/OH-WorkSpace"
    git clone https://github.com/yancongya/wechat-mcp-macos.git
    echo "✅ 项目克隆完成"
fi

# 2. 创建虚拟环境
if [ -d "$VENV_DIR" ]; then
    echo "⏭️  虚拟环境已创建，跳过"
else
    echo "🐍 创建虚拟环境..."
    mkdir -p "$PROJECT_DIR/backend"
    python3 -m venv "$VENV_DIR"
    echo "✅ 虚拟环境创建完成"
fi

# 3. 安装 wechat-mcp-macos（PyPI 包：Python 后端）
cd "$PROJECT_DIR"
source "$VENV_DIR/bin/activate"

if python3 -c "import wechat_mcp_macos" 2>/dev/null; then
    echo "⏭️  wechat-mcp-macos 已安装，跳过"
else
    echo "📦 安装 wechat-mcp-macos..."
    pip install wechat-mcp-macos --quiet
    echo "✅ wechat-mcp-macos 安装完成"
fi

# 4. 安装项目依赖
if python3 -c "import zstandard, pycryptodome" 2>/dev/null; then
    echo "⏭️  项目依赖已安装，跳过"
else
    echo "📦 安装项目依赖..."
    pip install -r "$PROJECT_DIR/requirements.txt" --quiet
    echo "✅ 项目依赖安装完成"
fi

echo ""
echo "=== 项目部署完成 ==="
