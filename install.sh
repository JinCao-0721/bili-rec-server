#!/bin/bash
# bili-rec-server 一键安装脚本
set -e

echo "=== bili-rec-server 一键部署 ==="
echo ""

# ── 检查 root ─────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  echo "请以 root 运行: sudo bash install.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAPCAT_DIR="/root/Napcat"

# ── 1. 安装系统依赖 ──────────────────────────────────────────
echo "[1/6] 安装系统依赖..."
apt update -qq
apt install -y -qq inotify-tools nginx python3 xvfb unzip wget curl > /dev/null 2>&1
echo "  系统依赖安装完成"

# ── 2. 安装 BililiveRecorder ─────────────────────────────────
echo "[2/6] 安装 BililiveRecorder..."
if [ ! -f /opt/brec/BililiveRecorder.Cli ]; then
  mkdir -p /opt/brec
  BREC_URL="https://github.com/BililiveRecorder/BililiveRecorder/releases/latest/download/BililiveRecorder-CLI-linux-x64.zip"
  wget -q "$BREC_URL" -O /tmp/brec.zip
  unzip -o /tmp/brec.zip -d /opt/brec > /dev/null
  chmod +x /opt/brec/BililiveRecorder.Cli
  rm -f /tmp/brec.zip
  echo "  BililiveRecorder 安装完成"
else
  echo "  BililiveRecorder 已存在，跳过"
fi

# ── 3. 安装 BaiduPCS-Go ─────────────────────────────────────
echo "[3/6] 安装 BaiduPCS-Go..."
if ! command -v BaiduPCS-Go &>/dev/null; then
  ARCH=$(uname -m)
  case "$ARCH" in
    x86_64) BPCS_ARCH="amd64" ;;
    aarch64) BPCS_ARCH="arm64" ;;
    *) echo "不支持的架构: $ARCH"; exit 1 ;;
  esac
  BPCS_URL="https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/v4.0.0/BaiduPCS-Go-v4.0.0-linux-${BPCS_ARCH}.zip"
  wget -q "$BPCS_URL" -O /tmp/bpcs.zip
  unzip -o /tmp/bpcs.zip -d /tmp/bpcs > /dev/null
  find /tmp/bpcs -name "BaiduPCS-Go" -type f -exec cp {} /usr/local/bin/ \;
  chmod +x /usr/local/bin/BaiduPCS-Go
  rm -rf /tmp/bpcs /tmp/bpcs.zip
  echo "  BaiduPCS-Go 安装完成"
else
  echo "  BaiduPCS-Go 已存在，跳过"
fi

# ── 4. 安装 NapCat QQ 机器人（可选）────────────────────────
echo "[4/6] 安装 NapCat QQ 机器人..."
if [ ! -d "$NAPCAT_DIR/opt/QQ" ]; then
  read -p "  是否安装 NapCat QQ 机器人？(y/N) " INSTALL_NAPCAT
  if [[ "$INSTALL_NAPCAT" =~ ^[Yy]$ ]]; then
    # 获取 NapCat 推荐的 QQ 版本
    echo "  下载 LinuxQQ..."
    QQ_DEB_URL=$(curl -sL https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest \
      | python3 -c "
import sys, json, re
body = json.load(sys.stdin).get('body', '')
m = re.search(r'\(https://dldir1\.qq\.com/qqfile/qq/QQNT/[^)]*linuxqq_[^)]*amd64\.deb\)', body)
print(m.group(0)[1:-1] if m else '')
" 2>/dev/null)
    if [ -z "$QQ_DEB_URL" ]; then
      QQ_DEB_URL="https://dldir1.qq.com/qqfile/qq/QQNT/94704804/linuxqq_3.2.23-44343_amd64.deb"
    fi
    wget -q "$QQ_DEB_URL" -O /tmp/qq.deb
    mkdir -p "$NAPCAT_DIR"
    dpkg -x /tmp/qq.deb "$NAPCAT_DIR"
    rm -f /tmp/qq.deb

    # 下载最新 NapCat Shell
    echo "  下载 NapCat Shell..."
    NAPCAT_URL=$(curl -sL https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest \
      | python3 -c "import sys,json; [print(a['browser_download_url']) for a in json.load(sys.stdin)['assets'] if a['name']=='NapCat.Shell.zip']" 2>/dev/null)
    if [ -z "$NAPCAT_URL" ]; then
      echo "  无法获取 NapCat 下载地址，请手动安装"
    else
      wget -q "$NAPCAT_URL" -O /tmp/napcat.zip
      NAPCAT_APP="$NAPCAT_DIR/opt/QQ/resources/app/app_launcher/napcat"
      mkdir -p "$NAPCAT_APP"
      unzip -o /tmp/napcat.zip -d "$NAPCAT_APP" > /dev/null
      rm -f /tmp/napcat.zip

      # 修补 QQ 加载 NapCat
      LOADER="$NAPCAT_DIR/opt/QQ/resources/app/app_launcher/loadNapCat.js"
      cp "$NAPCAT_APP/loadNapCat.js" "$LOADER" 2>/dev/null
      python3 -c "
import json
p = '$NAPCAT_DIR/opt/QQ/resources/app/package.json'
d = json.load(open(p))
d['main'] = './app_launcher/loadNapCat.js'
json.dump(d, open(p, 'w'), indent=2, ensure_ascii=False)
"
      echo "  NapCat 安装完成"
    fi
  else
    echo "  跳过 NapCat 安装"
  fi
else
  echo "  NapCat 已存在，跳过"
fi

# ── 5. 部署项目文件 ─────────────────────────────────────────
echo "[5/6] 部署项目文件..."

# 创建目录
mkdir -p /data/recordings
mkdir -p /var/www/status

# 复制脚本
cp "$SCRIPT_DIR/scripts/bili-status.py"  /usr/local/bin/bili-status.py
cp "$SCRIPT_DIR/scripts/bili-upload.sh"  /usr/local/bin/bili-upload.sh
cp "$SCRIPT_DIR/scripts/brec-start.sh"   /usr/local/bin/brec-start.sh
cp "$SCRIPT_DIR/scripts/napcat-restart.sh" /usr/local/bin/napcat-restart.sh
chmod +x /usr/local/bin/bili-status.py \
         /usr/local/bin/bili-upload.sh \
         /usr/local/bin/brec-start.sh \
         /usr/local/bin/napcat-restart.sh

# 复制 Web 页面
cp "$SCRIPT_DIR/web/index.html" /var/www/status/index.html

# 复制 systemd 服务
cp "$SCRIPT_DIR/services/brec.service"        /etc/systemd/system/
cp "$SCRIPT_DIR/services/napcat.service"      /etc/systemd/system/
cp "$SCRIPT_DIR/services/bili-upload.service" /etc/systemd/system/
cp "$SCRIPT_DIR/services/bili-status.service" /etc/systemd/system/

# 复制 nginx 配置
rm -f /etc/nginx/sites-enabled/default
cp "$SCRIPT_DIR/nginx/blrec.conf" /etc/nginx/sites-enabled/blrec
nginx -t 2>/dev/null && nginx -s reload 2>/dev/null || systemctl restart nginx

# 初始化配置文件（如果不存在）
[ -f /etc/bili-notify.json ] || cp "$SCRIPT_DIR/config.example/bili-notify.json" /etc/bili-notify.json
[ -f /etc/bili-record.json ] || cp "$SCRIPT_DIR/config.example/bili-record.json" /etc/bili-record.json

# 确保 /root 目录可被 nginx 遍历（读取二维码图片）
chmod o+x /root

# 重载 systemd
systemctl daemon-reload

echo "  项目文件部署完成"

# ── 6. 自动检测并挂载数据盘（可选）────────────────────────
echo "[6/6] 检查数据盘..."
# 检查 /data 是否已经是独立挂载点
if ! mountpoint -q /data 2>/dev/null; then
  # 查找未挂载的大容量磁盘
  UNUSED_DISK=$(lsblk -dpno NAME,SIZE,TYPE,MOUNTPOINT | awk '$3=="disk" && $4=="" {print $1}' | head -1)
  if [ -n "$UNUSED_DISK" ]; then
    DISK_SIZE=$(lsblk -dpno SIZE "$UNUSED_DISK" | tr -d ' ')
    echo "  发现未挂载的磁盘: $UNUSED_DISK ($DISK_SIZE)"
    read -p "  是否格式化并挂载到 /data？(y/N) " MOUNT_DISK
    if [[ "$MOUNT_DISK" =~ ^[Yy]$ ]]; then
      mkfs.ext4 -q "$UNUSED_DISK"
      mount "$UNUSED_DISK" /data
      mkdir -p /data/recordings
      UUID=$(blkid -s UUID -o value "$UNUSED_DISK")
      echo "UUID=$UUID /data ext4 defaults 0 2" >> /etc/fstab
      echo "  已挂载 $UNUSED_DISK 到 /data（开机自动挂载）"
    fi
  else
    echo "  /data 使用系统盘，无独立数据盘"
  fi
else
  echo "  /data 已挂载"
fi

# ── 启动基础服务 ─────────────────────────────────────────────
systemctl enable --now bili-status 2>/dev/null
systemctl enable --now bili-upload 2>/dev/null
systemctl enable --now nginx 2>/dev/null

echo ""
echo "=========================================="
echo "  安装完成！"
echo "=========================================="
echo ""
echo "接下来请完成以下配置："
echo ""
echo "1. 编辑录播配置（填写 B站 Cookie 和 BRec 账号密码）："
echo "   nano /usr/local/bin/brec-start.sh"
echo ""
echo "2. 同步修改状态 API 中的 BRec 账号密码和 NapCat Token："
echo "   nano /usr/local/bin/bili-status.py"
echo "   （修改顶部的 BREC_USER、BREC_PASS、NAPCAT_TOKEN）"
echo ""
echo "3. 启动录播服务："
echo "   systemctl enable --now brec"
echo ""
echo "4. 登录百度云："
echo "   BaiduPCS-Go login -bduss=YOUR_BDUSS -stoken=YOUR_STOKEN"
echo ""
echo "5.（可选）启动 QQ 机器人："
echo "   systemctl enable --now napcat"
echo "   然后访问 http://YOUR_IP 扫码登录"
echo ""
echo "6. 访问状态页面："
echo "   http://YOUR_SERVER_IP"
echo ""
