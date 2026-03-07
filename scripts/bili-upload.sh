#!/bin/bash
# 监控录播目录，文件写入完成后自动上传百度云

WATCH_DIR="/data/recordings"
BAIDU_BASE="/录播"
LOG="/var/log/bili-upload.log"

# 从本地文件路径解析百度云目标目录
# 格式1（自定义模板）: /data/recordings/{name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.ext
# 格式2（默认格式）:   /data/recordings/{roomId}-{name}/录制-{roomId}-{yyyyMMdd}-{HHmmss}-{index}-{title}.ext
# 目标路径: /录播/{name}/{yyyyMMdd}/filename.ext
parse_target() {
    local FILE="$1"
    local PARENT_DIR
    PARENT_DIR=$(basename "$(dirname "$FILE")")
    local GRANDPARENT_DIR
    GRANDPARENT_DIR=$(basename "$(dirname "$(dirname "$FILE")")")

    # 判断父目录是否为日期格式 (yyyyMMdd)
    if [[ "$PARENT_DIR" =~ ^[0-9]{8}$ ]]; then
        # 格式1: {name}/{yyyyMMdd}/filename.ext
        REMOTE_DIR="$BAIDU_BASE/$GRANDPARENT_DIR/$PARENT_DIR"
    else
        # 格式2: {roomId}-{name}/filename.ext — 从文件名提取日期
        local NAME_DIR="$PARENT_DIR"
        # 去掉 roomId- 前缀（如 "23251279-初初Rachel" → "初初Rachel"）
        if [[ "$NAME_DIR" =~ ^[0-9]+-(.+)$ ]]; then
            NAME_DIR="${BASH_REMATCH[1]}"
        fi
        # 从文件名中提取日期（匹配 yyyyMMdd）
        local FILENAME
        FILENAME=$(basename "$FILE")
        local FILE_DATE
        FILE_DATE=$(echo "$FILENAME" | grep -oP '20\d{6}' | head -1)
        if [ -z "$FILE_DATE" ]; then
            FILE_DATE=$(date '+%Y%m%d')
        fi
        REMOTE_DIR="$BAIDU_BASE/$NAME_DIR/$FILE_DATE"
    fi
    REMOTE_NAME=$(basename "$FILE")
}

# 上传函数：建目录后上传，通过对比本地和远端文件大小确认上传完整
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
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 上传命令报错: $REMOTE_NAME" >> "$LOG"
        return 1
    fi

    # 验证远端文件存在且大小与本地一致
    local LOCAL_SIZE
    LOCAL_SIZE=$(stat -c%s "$FILE" 2>/dev/null || echo 0)
    local REMOTE_INFO
    REMOTE_INFO=$(BaiduPCS-Go ls -l "$REMOTE_DIR/" 2>&1)
    if ! echo "$REMOTE_INFO" | grep -qF "$REMOTE_NAME"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 验证失败：远端文件不存在: $REMOTE_DIR/$REMOTE_NAME" >> "$LOG"
        return 1
    fi
    # 用python从ls -l输出中提取文件大小（格式如 45.70MB）
    local REMOTE_BYTES
    REMOTE_BYTES=$(echo "$REMOTE_INFO" | python3 -c "
import sys, re
name = '$REMOTE_NAME'
for line in sys.stdin:
    if name not in line:
        continue
    # 匹配带单位的大小，如 45.70MB、5.00KB、1.23GB
    m = re.search(r'(\d+\.?\d*)(B|KB|MB|GB|TB)', line)
    if m:
        val, unit = float(m.group(1)), m.group(2)
        units = {'B':1, 'KB':1024, 'MB':1024**2, 'GB':1024**3, 'TB':1024**4}
        print(int(val * units[unit]))
        break
" 2>/dev/null)
    if [ -z "$REMOTE_BYTES" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 验证失败：无法解析远端文件大小: $REMOTE_NAME" >> "$LOG"
        return 1
    fi
    # 允许 2% 误差（百度云显示的大小有四舍五入）
    local DIFF=$(( LOCAL_SIZE - REMOTE_BYTES ))
    DIFF=${DIFF#-}  # 取绝对值
    local THRESHOLD=$(( LOCAL_SIZE / 50 + 1024 ))
    if [ "$DIFF" -gt "$THRESHOLD" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 验证失败：大小不匹配 本地=${LOCAL_SIZE} 远端=${REMOTE_BYTES}: $REMOTE_NAME" >> "$LOG"
        return 1
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 验证通过：本地=${LOCAL_SIZE} 远端=${REMOTE_BYTES}: $REMOTE_NAME" >> "$LOG"
    return 0
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
