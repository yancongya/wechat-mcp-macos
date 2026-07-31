#!/usr/bin/env bash
# 兼容入口：统一转发到支持群聊/私聊和日期区间的新工作流。
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT_DIR/scripts/chat-summary-workflow.sh" "$@"
