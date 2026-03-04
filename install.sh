#!/bin/bash
# bili-rec-server 一键安装脚本
set -e

echo "=== bili-rec-server 安装 ==="

# 检查 root
if [ "$EUID" -ne 0 ]; then
  echo "请以 root 运行: sudo bash install.sh"
  exit 1
fi

# 检查依赖
for cmd in inotifywait BaiduPCS-Go; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "缺少依赖: $cmd"
    echo "  inotifywait: apt install inotify-tools"
    echo "  BaiduPCS-Go:  https://github.com/qjfoidnh/BaiduPCS-Go/releases"
    exit 1
  fi
done

# 创建目录
mkdir -p /data/recordings
mkdir -p /var/www/status

# 复制脚本
echo "复制脚本..."
cp scripts/bili-status.py  /usr/local/bin/bili-status.py
cp scripts/bili-upload.sh  /usr/local/bin/bili-upload.sh
cp scripts/brec-start.sh   /usr/local/bin/brec-start.sh
chmod +x /usr/local/bin/bili-status.py \
         /usr/local/bin/bili-upload.sh \
         /usr/local/bin/brec-start.sh

# 复制 Web 页面
echo "复制 Web 页面..."
cp web/index.html /var/www/status/index.html

# 复制 systemd 服务
echo "安装 systemd 服务..."
cp services/brec.service        /etc/systemd/system/
cp services/napcat.service      /etc/systemd/system/
cp services/bili-upload.service /etc/systemd/system/
cp services/bili-status.service /etc/systemd/system/

# 复制 nginx 配置
echo "配置 nginx..."
cp nginx/blrec.conf /etc/nginx/sites-enabled/blrec
nginx -t && nginx -s reload

# 初始化配置文件（如果不存在）
[ -f /etc/bili-notify.json ] || cp config.example/bili-notify.json /etc/bili-notify.json
[ -f /etc/bili-record.json ] || cp config.example/bili-record.json /etc/bili-record.json

# 重载 systemd
systemctl daemon-reload

echo ""
echo "=== 安装完成 ==="
echo ""
echo "下一步："
echo "1. 编辑 /usr/local/bin/brec-start.sh 填写 B站 Cookie、账号密码和房间号"
echo "2. 登录百度云: BaiduPCS-Go login -bduss=YOUR_BDUSS -stoken=YOUR_STOKEN"
echo "3. 启动服务:"
echo "   systemctl enable --now brec bili-upload bili-status"
echo "   systemctl enable --now napcat  # 如果使用 QQ 通知"
echo "4. 访问 http://YOUR_SERVER_IP 查看状态页面"
