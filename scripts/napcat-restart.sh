#!/bin/bash
# 重启 NapCat 前确保 autoLoginAccount 已设置
# NapCat 运行期间会自行清空该字段，需要在重启前写回

WEBUI_JSON="/opt/napcat-shell/napcat/config/webui.json"
ONEBOT_URL="http://127.0.0.1:5700"

# 尝试从 OneBot 获取当前登录的 QQ 号
QQ=$(curl -s --max-time 5 "$ONEBOT_URL/get_login_info" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('user_id',''))" 2>/dev/null)

if [ -n "$QQ" ] && [ "$QQ" != "0" ]; then
    python3 -c "
import json
p = '$WEBUI_JSON'
d = json.load(open(p))
d['autoLoginAccount'] = '$QQ'
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=4)
"
fi

systemctl restart napcat
