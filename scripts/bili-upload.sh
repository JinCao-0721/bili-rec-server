#!/bin/bash
# 监控录播目录，文件写入完成后自动上传百度云

WATCH_DIR="/data/recordings"
BAIDU_BASE="/录播"
LOG="/var/log/bili-upload.log"

# 从本地文件路径解析百度云目标目录
# 本地路径: /data/recordings/{name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.ext
# 目标路径: /录播/{name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.ext（文件名相同，无需重命名）
parse_target() {
    local FILE="$1"
    local DATE_DIR
    DATE_DIR=$(basename "$(dirname "$FILE")")       # "20260304"
    local NAME_DIR
    NAME_DIR=$(basename "$(dirname "$(dirname "$FILE")")")  # "拉拉肥探险家"

    REMOTE_DIR="$BAIDU_BASE/$NAME_DIR/$DATE_DIR"
    REMOTE_NAME=$(basename "$FILE")                 # "拉拉肥探险家-20260304-105200.flv"
}

# 上传函数：建目录后上传，文件名与本地一致无需重命名
# 参数：$1=本地文件路径  $2=远端目录  $3=远端文件名（用于验证）
baidu_upload() {
    local FILE="$1"
    local REMOTE_DIR="$2"
    local REMOTE_NAME="$3"
    local TMP
    TMP=$(mktemp)

    BaiduPCS-Go mkdir "$REMOTE_DIR" >> "$LOG" 2>&1

    BaiduPCS-Go upload --policy overwrite "$FILE" "$REMOTE_DIR/" 2>&1 \
        | tr '\r' '\n' | tee -a "$LOG" > "$TMP"
    local OUT
    OUT=$(cat "$TMP")
    rm -f "$TMP"
    if echo "$OUT" | grep -q "上传失败\|错误\|error\|Error\|failed\|未检测到"; then
        return 1
    fi
    if BaiduPCS-Go ls "$REMOTE_DIR/$REMOTE_NAME" > /dev/null 2>&1; then
        return 0
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
        if baidu_upload "$FILE" "$REMOTE_DIR" "$REMOTE_NAME"; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 上传成功，删除本地文件: $FILE" >> "$LOG"
            rm -f "$FILE"
            XML="${FILE%.*}.xml"
            if [ -f "$XML" ]; then
                parse_target "$XML"
                if baidu_upload "$XML" "$REMOTE_DIR" "$REMOTE_NAME"; then
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
            if baidu_upload "$FILE" "$REMOTE_DIR" "$REMOTE_NAME"; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 弹幕上传成功，删除: $FILE" >> "$LOG"
                rm -f "$FILE"
            else
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 弹幕上传失败，保留: $FILE" >> "$LOG"
            fi
        fi
    fi
done
