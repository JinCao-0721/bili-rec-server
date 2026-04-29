# bili-rec-server

B站直播自动录制 + 百度云上传 + QQ 机器人通知，适合部署在 Linux 服务器上。

## 功能

- **自动录播**：基于 [BililiveRecorder](https://github.com/BililiveRecorder/BililiveRecorder)，支持多直播间同时录制、弹幕录制、4GB 自动分段
- **自动上传百度云**：录制完成后通过百度网盘 Open API 分片上传，支持断点重试，验证文件完整后自动清理本地文件
- **QQ 通知**：开播/下播时通过 [NapCat](https://github.com/NapNeko/NapCatQQ) 发送 QQ 私聊/群消息，支持按房间订阅
- **Web 状态监控**：实时查看磁盘用量、上传队列、QQ 机器人状态，支持登录认证
- **Web 房间管理**：添加/删除直播间、开关录制、更新 B站 Cookie，房间配置持久化
- **通知管理**：Web 界面配置 QQ 通知对象和订阅房间

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

### 3. 配置百度网盘 Open API

上传已从 BaiduPCS-Go 迁移至百度网盘 Open API，需要配置 access token：

```bash
nano /etc/baidu-openapi.json
```

填写以下 JSON：

```json
{
  "app_key": "你的百度网盘开放平台 App Key",
  "secret_key": "你的百度网盘开放平台 Secret Key",
  "access_token": "你的 access_token",
  "refresh_token": "你的 refresh_token",
  "expires_in": 2592000
}
```

说明：

- `app_key`：百度开放平台应用的 App Key
- `secret_key`：百度开放平台应用的 Secret Key
- `access_token`：当前可用访问令牌
- `refresh_token`：后续自动续期依赖它，必须填写
- `expires_in`：当前 access token 的有效期秒数，后续自动刷新时会被更新

程序会读取 `/etc/baidu-openapi.json`，而不是让你手改 `/usr/local/bin/baidu-upload.py`。  
如果机器上没有 `/usr/local/bin/baidu-upload.py`，说明上传脚本本身还没部署，需要先把脚本复制到该路径。

Token 可通过 [百度网盘开放平台](https://pan.baidu.com/union/doc/ol0rsap9s) 申请获取。配置完成后可用下面命令测试：

```bash
python3 /usr/local/bin/baidu-upload.py --list /
```

如果配置正确，会列出网盘根目录内容。

### 4. 启动服务

```bash
systemctl enable --now brec
systemctl enable --now bili-upload
systemctl enable --now bili-status
```

### 5. （可选）启动 QQ 机器人

```bash
systemctl enable --now napcat
```

然后访问 `http://YOUR_IP` 扫码登录 QQ。

### 6. 访问状态页面

```
http://YOUR_SERVER_IP
```

首次访问需设置登录密码。

## 目录结构

```
bili-rec-server/
├── scripts/
│   ├── bili-status.py        # 状态 API 服务（端口 2234）
│   ├── bili-upload.sh        # inotify 文件监控 + 上传调度
│   ├── baidu-upload.py       # 百度网盘 Open API 上传（分片 + 重试）
│   ├── brec-start.sh         # BililiveRecorder 启动脚本（需填写配置）
│   ├── brec-add-rooms.sh     # 批量添加直播间
│   ├── brec-save-rooms.sh    # 持久化房间配置
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
| 认证配置 | `/etc/bili-auth.json` |

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

## 上传机制

- 使用百度网盘 Open API 分片上传（4MB/片）
- 分片上传失败自动重试 5 次，退避时间递增
- 合并创建文件失败自动重试 5 次
- 上传完成后验证远端文件大小（带重试，应对百度索引延迟）
- 验证通过后自动删除本地文件
- 弹幕 XML 文件与视频文件一同上传

## License

MIT
