# bili-rec-server

B站直播自动录制 + 百度云上传 + QQ 机器人通知，适合部署在 Linux 服务器上。

## 功能

- **自动录播**：基于 [BililiveRecorder](https://github.com/BililiveRecorder/BililiveRecorder)，支持多直播间、弹幕录制
- **自动上传百度云**：录制完成后自动上传，上传成功后删除本地文件
- **QQ 通知**：开播/下播时通过 [NapCat](https://github.com/NapNeko/NapCatQQ) 发送 QQ 消息
- **状态监控页面**：Web 界面实时查看磁盘、上传、QQ 机器人状态
- **房间管理**：Web 界面添加/删除直播间、开关录制、更新 Cookie

## 效果预览

状态页面 `http://YOUR_SERVER_IP`：
- 磁盘使用 / 百度云账号 / B站 Cookie / QQ 机器人状态
- 直播间列表（实时录制状态 + 录制开关）
- QQ 通知配置

## 依赖

| 组件 | 版本 | 用途 |
|------|------|------|
| [BililiveRecorder](https://github.com/BililiveRecorder/BililiveRecorder/releases) | ≥ 2.18 | 录播核心 |
| [BaiduPCS-Go](https://github.com/qjfoidnh/BaiduPCS-Go/releases) | ≥ 4.0 | 百度云上传 |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 最新版 | QQ 机器人（可选） |
| inotify-tools | - | 文件监控 |
| nginx | - | 反向代理 |
| python3 | ≥ 3.8 | 状态 API |

```bash
apt install inotify-tools nginx python3
```

## 目录结构

```
bili-rec-server/
├── scripts/
│   ├── bili-status.py      # 状态 API 服务（端口 2234）
│   ├── bili-upload.sh      # 文件监控 + 百度云上传
│   └── brec-start.sh       # BililiveRecorder 启动脚本（需填写配置）
├── services/
│   ├── brec.service        # 录播姬 systemd 服务
│   ├── napcat.service      # NapCat QQ 机器人服务
│   ├── bili-upload.service # 上传监控服务
│   └── bili-status.service # 状态 API 服务
├── nginx/
│   └── blrec.conf          # nginx 反向代理配置
├── web/
│   └── index.html          # 状态监控页面（React + Tailwind）
├── config.example/
│   ├── bili-notify.json    # 通知配置示例
│   └── bili-record.json    # 录制开关配置示例
└── install.sh              # 一键安装脚本
```

## 安装

### 1. 下载依赖

```bash
# BililiveRecorder
mkdir -p /opt/brec
wget https://github.com/BililiveRecorder/BililiveRecorder/releases/latest/download/BililiveRecorder-CLI-linux-x64.zip
unzip -o BililiveRecorder-CLI-linux-x64.zip -d /opt/brec
chmod +x /opt/brec/BililiveRecorder.Cli

# BaiduPCS-Go（选择对应架构的版本）
wget https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/v4.0.0/BaiduPCS-Go-v4.0.0-linux-amd64.zip
unzip BaiduPCS-Go-v4.0.0-linux-amd64.zip
cp BaiduPCS-Go /usr/local/bin/
chmod +x /usr/local/bin/BaiduPCS-Go
```

### 2. 克隆并安装

```bash
git clone https://github.com/YOUR_USERNAME/bili-rec-server.git
cd bili-rec-server
bash install.sh
```

### 3. 填写配置

编辑 `/usr/local/bin/brec-start.sh`：

```bash
# 替换为你的 B站 Cookie（登录后从浏览器 DevTools 获取）
COOKIE="SESSDATA=xxx; buvid3=xxx; DedeUserID=xxx;"

# 替换为你设置的 BililiveRecorder Web UI 账号密码
--http-basic-user YOUR_USERNAME
--http-basic-pass YOUR_PASSWORD

# 替换为要录制的直播间号（空格分隔）
/data/recordings \
  YOUR_ROOM_ID_1 YOUR_ROOM_ID_2
```

编辑 `/usr/local/bin/bili-status.py` 顶部常量：

```python
NAPCAT_TOKEN = "your_napcat_webui_token"
BREC_USER    = "your_brec_username"
BREC_PASS    = "your_brec_password"
```

### 4. 百度云登录

```bash
# 从浏览器 Cookie 中获取 BDUSS 和 STOKEN
BaiduPCS-Go login -bduss=YOUR_BDUSS -stoken=YOUR_STOKEN
```

### 5. 启动服务

```bash
systemctl enable --now brec bili-upload bili-status
systemctl enable --now napcat  # 如果使用 QQ 通知
```

访问 `http://YOUR_SERVER_IP` 查看状态页面。

## 文件路径规则

| 项目 | 路径 |
|------|------|
| 本地录制 | `/data/recordings/{roomId} - {name}/{roomId}_{yyyyMMdd-HHmmss}.flv` |
| 百度云 | `/录播/{roomId} - {name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.flv` |
| 上传日志 | `/var/log/bili-upload.log` |
| 通知配置 | `/etc/bili-notify.json` |
| 录制开关 | `/etc/bili-record.json` |

## 通知配置

编辑 `/etc/bili-notify.json`（或通过 Web 界面配置）：

```json
{
  "notify_qqs": [
    { "id": 123456789, "rooms": [] }
  ],
  "notify_groups": [
    { "id": 987654321, "rooms": [11111] }
  ]
}
```

`rooms` 为空数组表示接收所有房间的通知。

## NapCat 安装

参考 [NapCatQQ 文档](https://napneko.github.io/)。安装完成后修改 `services/napcat.service` 中的路径，再运行 `install.sh`。

## License

MIT
