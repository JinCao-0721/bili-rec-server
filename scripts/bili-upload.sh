#!/bin/bash
# 监控录播目录，文件写入完成后自动上传百度云

WATCH_DIR="/data/recordings"
BAIDU_BASE="/录播"
LOG="/var/log/bili-upload.log"

# 从本地文件路径解析百度云目标目录和文件名
# 本地路径: /data/recordings/{roomId} - {name}/{roomId}_{yyyyMMdd-HHmmss}.ext
# 目标路径: /录播/{roomId} - {name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.ext
parse_target() {
    local FILE="$1"
    local EXT="${FILE##*.}"
    local ROOM_DIR
    ROOM_DIR=$(basename "$(dirname "$FILE")")       # "1854704441 - 拉拉肥探险家"
    local BASENAME
    BASENAME=$(basename "$FILE" ".$EXT")            # "1854704441_20260304-105200"

    # 提取主播名（" - " 之后的部分）
    local NAME="${ROOM_DIR#* - }"                   # "拉拉肥探险家"

    # 提取日期时间（"_" 之后的部分）
    local DATETIME="${BASENAME#*_}"                 # "20260304-105200"

    # 提取日期（前 8 位）
    local DATE="${DATETIME:0:8}"                    # "20260304"

    REMOTE_DIR="$BAIDU_BASE/$ROOM_DIR/$DATE"
    REMOTE_NAME="$NAME-$DATETIME.$EXT"
}

# 上传函数：实时写日志（用于速度监控），通过输出内容判断成功/失败
baidu_upload() {
    local FILE="$1"
    local REMOTE_PATH="$2"
    local TMP
    TMP=$(mktemp)
    BaiduPCS-Go upload --policy overwrite "$FILE" "$REMOTE_PATH" 2>&1 \
        | tr '\r' '\n' | tee -a "$LOG" > "$TMP"
    local OUT
    OUT=$(cat "$TMP")
    rm -f "$TMP"
    if echo "$OUT" | grep -q "上传失败\|错误\|error\|Error\|failed\|未检测到"; then
        return 1
    fi
    if echo "$OUT" | grep -q "加入上传队列\|上传完毕\|总大小:"; then
        if BaiduPCS-Go ls "$REMOTE_PATH" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 上传监控启动，监控目录: $WATCH_DIR" >> "$LOG"

inotifywait -m -r -e close_write --format '%w%f' "$WATCH_DIR" | while read FILE; do
    EXT="${FILE##*.}"
    if [[ "$EXT" == "flv" || "$EXT" == "ts" || "$EXT" == "mp4" ]]; then
        FILE_SIZE=$(stat -c%s "$FILE" 2>/dev/null || echo 0)
        parse_target "$FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始上传: $FILE → $REMOTE_DIR/$REMOTE_NAME ($(($FILE_SIZE / 1024 / 1024))MB)" >> "$LOG"
        if baidu_upload "$FILE" "$REMOTE_DIR/$REMOTE_NAME"; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 上传成功，删除本地文件: $FILE" >> "$LOG"
            rm -f "$FILE"
            XML="${FILE%.*}.xml"
            if [ -f "$XML" ]; then
                parse_target "$XML"
                if baidu_upload "$XML" "$REMOTE_DIR/$REMOTE_NAME"; then
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 弹幕上传成功，删除: $XML" >> "$LOG"
                    rm -f "$XML"
                else
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 弹幕上传失败，保留: $XML" >> "$LOG"
                fi
            fi
        else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 上传失败，保留本地文件: $FILE" >> "$LOG"
        fi
    elif [[ "$EXT" == "xml" ]]; then
        VIDEO_LOCAL="${FILE%.*}.flv"
        if [ ! -f "$VIDEO_LOCAL" ]; then
            parse_target "$FILE"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 上传弹幕文件: $FILE → $REMOTE_DIR/$REMOTE_NAME" >> "$LOG"
            if baidu_upload "$FILE" "$REMOTE_DIR/$REMOTE_NAME"; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 弹幕上传成功，删除: $FILE" >> "$LOG"
                rm -f "$FILE"
            else
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 弹幕上传失败，保留: $FILE" >> "$LOG"
            fi
        fi
    fi
done
