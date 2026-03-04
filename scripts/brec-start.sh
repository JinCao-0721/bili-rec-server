#!/bin/bash
COOKIE="YOUR_SESSDATA; buvid3=YOUR_BUVID3; DedeUserID=YOUR_DEDE_USER_ID;"

exec /opt/brec/BililiveRecorder.Cli portable \
  --bind http://0.0.0.0:2233 \
  --http-basic-user your_username \
  --http-basic-pass your_password \
  --cookie "$COOKIE" \
  --danmaku All \
  --webhook-url http://127.0.0.1:2234/api/brec-webhook \
  --filename '{{ name }}/{{ "now" | time_zone: "Asia/Shanghai" | format_date: "yyyyMMdd" }}/{{ name }}-{{ "now" | time_zone: "Asia/Shanghai" | format_date: "yyyyMMdd-HHmmss" }}' \
  --mode RawData \
  /data/recordings \
  YOUR_ROOM_ID_1 YOUR_ROOM_ID_2
