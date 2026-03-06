# bili-rec-server

B站直播自动录制 + 百度云上传 + QQ 机器人通知，适合部署在 Linux 服务器上。

## 功能

- **自动录播**：基于 [BililiveRecorder](https://github.com/BililiveRecorder/BililiveRecorder)，支持多直播间、弹幕录制
- **自动上传百度云**：录制完成后自动上传，验证文件完整后删除本地文件
- **QQ 通知**：开播/下播时通过 [NapCat](https://github.com/NapNeko/NapCatQQ) 发送 QQ 消息
- **状态监控页面**：Web 界面实时查看磁盘、上传、QQ 机器人状态
- **房间管理**：Web 界面添加/删除直播间、开关录制、更新 Cookie

## 一键部署

在全新的 Ubuntu 20.04+ 服务器上执行：

```bash
git clone https://github.com/JinCao-0721/bili-rec-server.git
cd bili-rec-server
sudo bash install.sh
```

安装脚本会自动完成：
- 安装系统依赖（inotify-tools、nginx、python3、xvfb 等）
- 下载安装 BililiveRecorder CLI
- 下载安装 BaiduPCS-Go
- 可选安装 NapCat QQ 机器人（交互式选择）
- 部署脚本、服务、网页、nginx 配置
- 可选检测并挂载数据盘
- 启动基础服务

## 部署后配置

### 1. 编辑录播启动脚本

```bash
nano /usr/local/bin/brec-start.sh
```

填写：
- B站 Cookie（从浏览器 DevTools 获取 SESSDATA、buvid3、DedeUserID）
- BRec Web UI 账号密码
- 初始录制的直播间号

### 2. 同步修改状态 API 凭据

```bash
nano /usr/local/bin/bili-status.py
```

修改顶部的 `BREC_USER`、`BREC_PASS`（与上一步一致），以及 `NAPCAT_TOKEN`（NapCat WebUI Token）。

### 3. 启动录播服务

```bash
systemctl enable --now brec
```

### 4. 登录百度云

```bash
BaiduPCS-Go login -bduss=YOUR_BDUSS -stoken=YOUR_STOKEN
```

从浏览器 Cookie 中获取 BDUSS 和 STOKEN。

### 5. （可选）启动 QQ 机器人

```bash
systemctl enable --now napcat
```

然后访问 `http://YOUR_IP` 扫码登录 QQ。

### 6. 访问状态页面

```
http://YOUR_SERVER_IP
```

## 目录结构

```
bili-rec-server/
├── scripts/
│   ├── bili-status.py        # 状态 API 服务（端口 2234）
│   ├── bili-upload.sh        # 文件监控 + 百度云上传
│   ├── brec-start.sh         # BililiveRecorder 启动脚本（需填写配置）
│   └── napcat-restart.sh     # NapCat 重启辅助脚本
├── services/
│   ├── brec.service          # 录播姬 systemd 服务
│   ├── napcat.service        # NapCat QQ 机器人服务
│   ├── bili-upload.service   # 上传监控服务
│   └── bili-status.service   # 状态 API 服务
├── nginx/
│   └── blrec.conf            # nginx 反向代理配置
├── web/
│   └── index.html            # 状态监控页面
├── config.example/
│   ├── bili-notify.json      # 通知配置示例
│   └── bili-record.json      # 录制开关配置示例
└── install.sh                # 一键安装脚本
```

## 文件路径规则

| 项目 | 路径 |
|------|------|
| 本地录制 | `/data/recordings/{name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.flv` |
| 百度云 | `/录播/{name}/{yyyyMMdd}/{name}-{yyyyMMdd-HHmmss}.flv` |
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

## License

MIT
