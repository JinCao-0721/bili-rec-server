#!/bin/bash
# BililiveRecorder 启动脚本
# 修改以下配置后再运行

# B站 Cookie（需要 SESSDATA + buvid3 + DedeUserID）
COOKIE="SESSDATA=your_sessdata; buvid3=your_buvid3; DedeUserID=your_dede_user_id;"

exec /opt/brec/BililiveRecorder.Cli portable \
  --bind http://0.0.0.0:2233 \
  --http-basic-user your_username \
  --http-basic-pass your_password \
  --cookie "$COOKIE" \
  --danmaku All \
  --webhook-url http://127.0.0.1:2234/api/brec-webhook \
  --filename '{{ roomId }} - {{ name }}/{{ roomId }}_{{ "now" | time_zone: "Asia/Shanghai" | format_date: "yyyyMMdd-HHmmss" }}' \
  --mode RawData \
  /data/recordings \
  YOUR_ROOM_ID_1 YOUR_ROOM_ID_2
