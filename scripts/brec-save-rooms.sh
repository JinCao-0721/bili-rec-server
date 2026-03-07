#!/bin/bash
# 保存当前 brec 房间列表到配置文件
AUTH="admin:admin"
API="http://127.0.0.1:2233/api/room"
ROOMS_FILE="/etc/brec-rooms.json"

curl -s -u "$AUTH" "$API" 2>/dev/null | python3 -c "
import json, sys
rooms = json.load(sys.stdin)
valid = [{'roomId': r['roomId'], 'autoRecord': r['autoRecord']} for r in rooms if r['roomId'] > 0]
with open('$ROOMS_FILE', 'w') as f:
    json.dump(valid, f, indent=2)
print(f'Saved {len(valid)} rooms to $ROOMS_FILE')
" 2>/dev/null
