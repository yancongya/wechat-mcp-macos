#!/bin/bash
# 联系人映射（幂等）
set -e

PROJECT_DIR="$HOME/Desktop/OH-WorkSpace/wechat-decrypt-macos"

echo "=== 联系人映射 ==="

# 检查是否已有数据
if [ -f "$PROJECT_DIR/contacts.json" ]; then
    count=$(python3 -c "import json; d=json.load(open('$PROJECT_DIR/contacts.json')); print(len(d))" 2>/dev/null || echo "0")
    if [ "$count" -gt 0 ]; then
        echo "⏭️  contacts.json 已有 $count 个联系人，跳过"
        exit 0
    fi
fi

# 检查 wechat_keys.json 是否有 contact.db 密钥
if [ ! -f "$PROJECT_DIR/wechat_keys.json" ]; then
    echo "❌ wechat_keys.json 不存在，请先运行 extract_keys.sh"
    exit 1
fi

has_contact_key=$(python3 -c "
import json
with open('$PROJECT_DIR/wechat_keys.json') as f:
    data = json.load(f)
print('yes' if 'contact/contact.db' in data else 'no')
" 2>/dev/null)

if [ "$has_contact_key" = "no" ]; then
    echo "⚠️  contact.db 密钥不可用"
    echo "   请手动编辑 $PROJECT_DIR/contacts.json"
    echo "   格式: {\"wxid_xxx\": {\"nickname\": \"昵称\", \"remark\": \"备注\"}}"
    exit 0
fi

echo "🔍 从 contact.db 提取联系人映射..."

# 使用 Python 提取
cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" -c "
import sys, json, subprocess, csv, io, os, hashlib, glob

# Load keys
with open('wechat_keys.json') as f:
    data = json.load(f)

contact_key = data.get('contact/contact.db', '')
if not contact_key:
    print('No contact.db key found')
    sys.exit(1)

# Find contact.db
pattern = os.path.expanduser(
    '~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/'
    '*/db_storage/contact/contact.db'
)
dbs = glob.glob(pattern)
if not dbs:
    print('contact.db not found')
    sys.exit(1)

db_path = dbs[0]
sqlcipher = '/opt/homebrew/bin/sqlcipher'

# Query contacts
preamble = (
    f'PRAGMA key = \"x\\'{contact_key}\\'\";\n'
    'PRAGMA cipher_compatibility = 4;\n'
    'PRAGMA cipher_page_size = 4096;\n'
)
cmd = preamble + '.headers on\n.mode csv\nSELECT username, nick_name, remark FROM Contact;\n'

result = subprocess.run(
    [sqlcipher, db_path],
    input=cmd.encode(), capture_output=True, timeout=10,
)
text = result.stdout.decode('utf-8', errors='replace').strip()
lines = [l.strip() for l in text.split('\n') if l.strip() and l.strip() != 'ok']

if len(lines) < 2:
    print(f'Query failed: {result.stderr.decode()[:200]}')
    sys.exit(1)

reader = csv.DictReader(io.StringIO('\n'.join(lines)))
contacts = list(reader)

# Get group member wxids
from server import _load_key, _query, _get_message_dbs
import server as srv

msg_key = srv._load_key()
dbs2 = srv._get_message_dbs()

# Find all chatrooms in Name2Id
all_wxids = set()
for db in dbs2:
    rows = srv._query(db, 'SELECT user_name FROM Name2Id WHERE user_name LIKE \"%@chatroom\";')
    for r in rows:
        all_wxids.add(r['user_name'])
    # Also add private contacts
    rows2 = srv._query(db, 'SELECT user_name FROM Name2Id WHERE user_name NOT LIKE \"%@chatroom\";')
    for r in rows2:
        all_wxids.add(r['user_name'])

# Match contacts
contacts_json = {}
matched = 0
for c in contacts:
    username = c.get('username', '')
    if username in all_wxids:
        nickname = c.get('nick_name', '')
        remark = c.get('remark', '')
        if nickname or remark:
            contacts_json[username] = {'nickname': nickname, 'remark': remark}
            matched += 1

# Save
with open('contacts.json', 'w', encoding='utf-8') as f:
    json.dump(contacts_json, f, ensure_ascii=False, indent=2)

print(f'✅ 提取了 {matched} 个联系人映射')
"

if [ $? -eq 0 ]; then
    echo "✅ 联系人映射完成"
else
    echo "⚠️  自动提取失败，请手动编辑 contacts.json"
fi
