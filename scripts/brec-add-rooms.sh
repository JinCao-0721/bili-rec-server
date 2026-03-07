#!/bin/bash
# brec 启动后：清理幽灵房间，从配置文件恢复房间列表
AUTH="your_username:your_password"
API="http://127.0.0.1:2233/api/room"
ROOMS_FILE="/etc/brec-rooms.json"

# Wait for API
for i in $(seq 1 30); do
  curl -s -u "$AUTH" "$API" > /dev/null 2>&1 && break
  sleep 1
done

# Delete any roomId=0 ghost rooms
curl -s -u "$AUTH" "$API" 2>/dev/null | python3 -c "
import json, sys, subprocess
for r in json.load(sys.stdin):
    if r['roomId'] == 0:
        subprocess.run(['curl', '-s', '-u', '$AUTH', '-X', 'DELETE', '$API/' + r['objectId']], capture_output=True)
" 2>/dev/null

# Load rooms from config file
if [ -f "$ROOMS_FILE" ]; then
    ROOMS=$(python3 -c "
import json
with open('$ROOMS_FILE') as f:
    rooms = json.load(f)
for r in rooms:
    print(r['roomId'])
" 2>/dev/null)

    for ROOM in $ROOMS; do
        EXISTS=$(curl -s -u "$AUTH" "$API" 2>/dev/null | python3 -c "
import json, sys
print(any(r['roomId'] == $ROOM for r in json.load(sys.stdin)))
" 2>/dev/null)
        if [ "$EXISTS" = "True" ]; then
            continue
        fi
        curl -s -u "$AUTH" -X POST "$API" \
            -H 'Content-Type: application/json' \
            -d "{\"roomId\": $ROOM, \"autoRecord\": true}" > /dev/null 2>&1
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restored room $ROOM from config"
    done
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No rooms config found at $ROOMS_FILE, creating from current rooms"
fi

# Set global cutting mode: BY_SIZE, 4096MB (4GB) via GraphQL
# Note: --cutting-mode CLI option does NOT work in portable mode, must use GraphQL API
curl -s -u "$AUTH" -X POST "http://127.0.0.1:2233/graphql" \
    -H 'Content-Type: application/json' \
    -d '{"query":"mutation { setConfig(config: { optionalCuttingMode: { hasValue: true, value: BY_SIZE }, optionalCuttingNumber: { hasValue: true, value: 4096 } }) { optionalCuttingMode { hasValue value } optionalCuttingNumber { hasValue value } } }"}' > /dev/null 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Set cutting mode: BY_SIZE 4096MB"

/usr/local/bin/brec-save-rooms.sh
