#!/usr/bin/env python3
"""状态信息 API 服务，提供磁盘、上传状态和 NapCat 登录状态"""
import base64
import hashlib
import json
import re
import subprocess
import os
import time
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

NAPCAT_PORT = 6099
NAPCAT_TOKEN = "your_napcat_token"
ONEBOT_URL = "http://127.0.0.1:5700"
NAPCAT_TIMEOUT = 8
NOTIFY_CONFIG_PATH = "/etc/bili-notify.json"
RECORD_CONFIG_PATH = "/etc/bili-record.json"
BREC_URL = "http://127.0.0.1:2233"
BREC_USER = "your_brec_username"
BREC_PASS = "your_brec_password"
BREC_START_SH = "/usr/local/bin/brec-start.sh"

_napcat_cache = {"ts": 0, "data": None}
_CACHE_TTL = 10
_recording_last_true = {}  # room_id -> timestamp，上次 recording=True 的时间
_RECORDING_GRACE = 5       # 秒：recording 变 False 后宽限期，避免切片间隙闪烁

# ── 直播状态轮询（推送逻辑，与录制完全解耦）────────────────────
_live_status = {}   # room_id -> bool，上次已知直播状态
_live_lock   = threading.Lock()


def _bilibili_live_status(room_id):
    """查询 B站 API，返回直播状态：True=在播, False=下播, None=查询失败"""
    try:
        url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        return data.get("data", {}).get("live_status") == 1
    except Exception:
        return None


def _poll_live():
    """后台线程：轮询所有房间直播状态，状态变化时发通知"""
    while True:
        time.sleep(10)
        try:
            rooms = get_brec_rooms()
        except Exception:
            continue
        for rm in rooms:
            room_id   = rm["id"]
            room_name = rm["name"]
            is_live = _bilibili_live_status(room_id)
            if is_live is None:
                continue
            with _live_lock:
                prev = _live_status.get(room_id)
                if prev == is_live:
                    continue
                _live_status[room_id] = is_live
            if prev is None:
                continue  # 首次检测，不发通知
            if is_live:
                msg = f"🔴 直播开始！\n主播：{room_name}\n房间：{room_id}\nhttps://live.bilibili.com/{room_id}"
            else:
                msg = f"⚫ 直播结束\n主播：{room_name}\n房间：{room_id}"
            send_notifications(msg, room_id)


# ── 录制开关配置 ──────────────────────────────────────────────

def load_record_config():
    try:
        with open(RECORD_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {"disabled_rooms": []}


def save_record_config(cfg):
    with open(RECORD_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def is_room_disabled(room_id):
    cfg = load_record_config()
    return int(room_id) in [int(r) for r in cfg.get("disabled_rooms", [])]


def set_room_disabled(room_id, disabled):
    cfg = load_record_config()
    rooms = [int(r) for r in cfg.get("disabled_rooms", [])]
    room_id = int(room_id)
    if disabled and room_id not in rooms:
        rooms.append(room_id)
    elif not disabled:
        rooms = [r for r in rooms if r != room_id]
    cfg["disabled_rooms"] = rooms
    save_record_config(cfg)


# ── 配置读写 ──────────────────────────────────────────────────

def _normalize_entry(entry):
    """把旧格式 int 统一成 {id, rooms:[]} 字典"""
    if isinstance(entry, dict):
        entry['rooms'] = [int(r) for r in entry.get('rooms', [])]
        return entry
    return {'id': int(entry), 'rooms': []}


def load_notify_config():
    try:
        with open(NOTIFY_CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    # 兼容旧格式
    if 'notify_qq' in cfg and 'notify_qqs' not in cfg:
        cfg['notify_qqs'] = [{'id': cfg.pop('notify_qq'), 'rooms': []}] if cfg.get('notify_qq') else []
    cfg.setdefault('notify_qqs', [])
    cfg.setdefault('notify_groups', [])
    cfg['notify_qqs']    = [_normalize_entry(e) for e in cfg['notify_qqs']]
    cfg['notify_groups'] = [_normalize_entry(e) for e in cfg['notify_groups']]
    return cfg


def save_notify_config(cfg):
    with open(NOTIFY_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── BililiveRecorder 房间管理 ────────────────────────────────

def _brec_request(path, method="GET", data=None):
    auth = base64.b64encode(f"{BREC_USER}:{BREC_PASS}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{BREC_URL}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def get_brec_rooms():
    try:
        rooms = _brec_request("/api/room")
        disabled = set(int(r) for r in load_record_config().get("disabled_rooms", []))
        now = time.time()
        result = []
        for rm in rooms:
            room_id   = rm["roomId"]
            recording = rm.get("recording", False)
            if recording:
                _recording_last_true[room_id] = now
            elif room_id in _recording_last_true and now - _recording_last_true[room_id] < _RECORDING_GRACE:
                recording = True  # 宽限期内，保持显示录制中
            result.append({
                "objectId":  rm["objectId"],
                "id":        room_id,
                "name":      rm.get("name", str(room_id)),
                "streaming": rm.get("streaming", False),
                "recording": recording,
                "autoRecord": room_id not in disabled,
            })
        return result
    except Exception:
        return []


def _get_sh_room_ids():
    """从 brec-start.sh 最后一行读取房间号列表"""
    with open(BREC_START_SH, 'r') as f:
        lines = f.read().rstrip().split('\n')
    return [int(t) for t in lines[-1].split() if t.isdigit()]


def _set_sh_room_ids(room_ids):
    """将房间号列表写回 brec-start.sh 最后一行"""
    with open(BREC_START_SH, 'r') as f:
        lines = f.read().rstrip().split('\n')
    lines[-1] = '  ' + ' '.join(str(r) for r in room_ids) if room_ids else ''
    with open(BREC_START_SH, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def get_brec_cookie():
    """从 brec-start.sh 读取当前 Cookie 字符串"""
    with open(BREC_START_SH, 'r') as f:
        content = f.read()
    m = re.search(r'COOKIE="([^"]+)"', content)
    return m.group(1) if m else ''


def set_brec_cookie(cookie_str):
    """更新 brec-start.sh 中的 Cookie，并重启服务"""
    with open(BREC_START_SH, 'r') as f:
        content = f.read()
    new_content = re.sub(r'COOKIE="[^"]*"', f'COOKIE="{cookie_str}"', content)
    with open(BREC_START_SH, 'w') as f:
        f.write(new_content)
    subprocess.run(['systemctl', 'restart', 'brec'])


def parse_cookie_fields(cookie_str):
    """从 Cookie 字符串提取各字段值"""
    fields = {}
    for part in cookie_str.split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            fields[k.strip()] = v.strip()
    return fields


def build_cookie_str(sessdata, buvid3, dede_user_id):
    parts = []
    if sessdata:    parts.append(f'SESSDATA={sessdata}')
    if buvid3:      parts.append(f'buvid3={buvid3}')
    if dede_user_id: parts.append(f'DedeUserID={dede_user_id}')
    return '; '.join(parts) + (';' if parts else '')


def brec_add_room(room_id):
    result = _brec_request("/api/room", method="POST", data={"roomId": room_id})
    # 同步写入 brec-start.sh
    ids = _get_sh_room_ids()
    if room_id not in ids:
        _set_sh_room_ids(ids + [room_id])
    return result


def brec_remove_room(object_id):
    _brec_request(f"/api/room/{object_id}", method="DELETE")
    # 从 brec-start.sh 移除对应房间号（需先查出 roomId）
    rooms = get_brec_rooms()
    remaining_ids = [rm["id"] for rm in rooms if rm["objectId"] != object_id]
    _set_sh_room_ids(remaining_ids)


# ── NapCat ───────────────────────────────────────────────────

def _napcat_credential():
    h = hashlib.sha256((NAPCAT_TOKEN + ".napcat").encode()).hexdigest()
    req = urllib.request.Request(
        f"http://127.0.0.1:{NAPCAT_PORT}/api/auth/login",
        data=json.dumps({"hash": h}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=NAPCAT_TIMEOUT) as r:
            d = json.loads(r.read())
            return d.get("data", {}).get("Credential", "")
    except Exception:
        return ""


def _napcat_post(path, cred):
    req = urllib.request.Request(
        f"http://127.0.0.1:{NAPCAT_PORT}{path}",
        data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cred}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=NAPCAT_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def get_napcat_status():
    global _napcat_cache
    now = time.time()
    if _napcat_cache["data"] is not None and now - _napcat_cache["ts"] < _CACHE_TTL:
        return _napcat_cache["data"]
    cred = _napcat_credential()
    if not cred:
        ps = subprocess.run(['pgrep', '-f', 'libnapcat_launcher'], capture_output=True, text=True)
        result = {"running": bool(ps.stdout.strip()), "logged_in": False}
    else:
        d = _napcat_post("/api/QQLogin/CheckLoginStatus", cred)
        is_login = d.get("data", {}).get("isLogin", False)
        result = {"running": True, "logged_in": is_login, "_cred": cred}
    _napcat_cache = {"ts": now, "data": result}
    return result


def refresh_qrcode(cred):
    _napcat_post("/api/QQLogin/RefreshQRcode", cred)


# ── 系统状态 ─────────────────────────────────────────────────

def get_disk_info():
    result = subprocess.run(['df', '-B1', '/data/recordings'], capture_output=True, text=True)
    lines = result.stdout.strip().split('\n')
    if len(lines) < 2:
        return {'total': 0, 'used': 0, 'free': 0, 'percent': 0}
    parts = lines[1].split()
    total, used, free = int(parts[1]), int(parts[2]), int(parts[3])
    return {'total': total, 'used': used, 'free': free,
            'percent': round(used / total * 100, 1) if total else 0}


def get_baidu_status():
    result = subprocess.run(['BaiduPCS-Go', 'who'], capture_output=True, text=True)
    output = result.stdout.strip()
    uid_match  = re.search(r'uid:\s*(\d+)', output)
    name_match = re.search(r'用户名:\s*([^,]+)', output)
    uid  = int(uid_match.group(1)) if uid_match else 0
    name = name_match.group(1).strip() if name_match else ''
    return {'logged_in': uid != 0, 'username': name if uid != 0 else ''}


def baidu_login(bduss, stoken):
    result = subprocess.run(
        ['BaiduPCS-Go', 'login', f'-bduss={bduss}', f'-stoken={stoken}'],
        capture_output=True, text=True
    )
    output = (result.stdout + result.stderr).strip()
    success = '登录成功' in output
    return {'success': success, 'message': output}


def baidu_logout():
    result = subprocess.run(
        ['BaiduPCS-Go', 'logout', '-y'],
        capture_output=True, text=True, input='y'
    )
    return {'success': result.returncode == 0}


def qq_logout(cred):
    return _napcat_post("/api/QQLogin/Logout", cred)


def get_upload_status():
    ps = subprocess.run(['pgrep', '-f', 'BaiduPCS-Go upload'], capture_output=True, text=True)
    active = bool(ps.stdout.strip())
    upload_file, upload_speed, upload_progress = '', '', ''
    log_path = '/var/log/bili-upload.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        # 从近 20 行找文件名和上传状态
        for line in reversed(lines[-20:]):
            if '开始上传' in line:
                m = re.search(r'开始上传: (.+?) 大小:', line)
                if m:
                    upload_file = os.path.basename(m.group(1).strip())
                active = True
                break
            if '上传成功' in line or '上传失败' in line:
                break
        # 从近 200 行找最新速度（BaiduPCS-Go 进度行格式：↑ xx/xx xx/s）
        if active:
            for line in reversed(lines[-200:]):
                m = re.search(r'↑\s+([\d.]+\s*\w+)/([\d.]+\s*\w+)\s+([\d.]+\s*\w+/s)', line)
                if m:
                    upload_progress = f"{m.group(1)}/{m.group(2)}"
                    upload_speed    = m.group(3)
                    break
    return {'active': active, 'file': upload_file,
            'speed': upload_speed, 'progress': upload_progress}


# ── 通知发送 ─────────────────────────────────────────────────

def _onebot_post(endpoint, payload):
    req = urllib.request.Request(
        f"{ONEBOT_URL}/{endpoint}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _should_notify(entry, room_id):
    """rooms 为空列表表示接收全部房间"""
    rooms = entry.get('rooms', [])
    return not rooms or not room_id or int(room_id) in rooms


def send_notifications(msg, room_id=None):
    cfg = load_notify_config()
    results = []
    for entry in cfg.get('notify_qqs', []):
        if entry.get('id') and _should_notify(entry, room_id):
            results.append(_onebot_post("send_private_msg", {"user_id": entry['id'], "message": msg}))
    for entry in cfg.get('notify_groups', []):
        if entry.get('id') and _should_notify(entry, room_id):
            results.append(_onebot_post("send_group_msg", {"group_id": entry['id'], "message": msg}))
    return results


# ── HTTP 工具 ────────────────────────────────────────────────

def _json_response(self, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode()
    self.send_response(status)
    self.send_header('Content-Type', 'application/json; charset=utf-8')
    self.send_header('Access-Control-Allow-Origin', '*')
    self.end_headers()
    self.wfile.write(body)


# ── HTTP Handler ─────────────────────────────────────────────

class StatusHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path in ('/api/brec-webhook', '/api/blrec-webhook'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                event = json.loads(body)
                etype = event.get('EventType', '') or event.get('type', '')
                edata = event.get('EventData', {}) or event.get('data', {})
                room_id   = edata.get('RoomId') or edata.get('room_id', '')
                room_name = edata.get('Name') or edata.get('name', '') or \
                            edata.get('room_info', {}).get('room_name', '')
                if etype in ('SessionStarted', 'LiveBeganEvent'):
                    # 录播逻辑：该房间被用户关闭录制时，立即停止本次会话
                    if room_id and is_room_disabled(room_id):
                        try:
                            all_rooms = _brec_request("/api/room")
                            for rm in all_rooms:
                                if rm.get("roomId") == int(room_id):
                                    _brec_request(f"/api/room/{rm['objectId']}/stop", method="POST")
                                    break
                        except Exception:
                            pass
            except Exception:
                pass
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')

        elif self.path == '/api/notify-config':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                new_cfg = json.loads(body)
                cfg = load_notify_config()
                if 'notify_qqs' in new_cfg:
                    cfg['notify_qqs'] = [
                        {'id': int(e['id']), 'rooms': [int(r) for r in e.get('rooms', [])]}
                        for e in new_cfg['notify_qqs'] if e.get('id')
                    ]
                if 'notify_groups' in new_cfg:
                    cfg['notify_groups'] = [
                        {'id': int(e['id']), 'rooms': [int(r) for r in e.get('rooms', [])]}
                        for e in new_cfg['notify_groups'] if e.get('id')
                    ]
                save_notify_config(cfg)
                _json_response(self, {'code': 0, 'message': 'ok', 'config': cfg})
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)

        elif self.path == '/api/brec-cookie':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                d = json.loads(body)
                cookie_str = build_cookie_str(
                    d.get('sessdata', ''),
                    d.get('buvid3', ''),
                    d.get('dede_user_id', '')
                )
                set_brec_cookie(cookie_str)
                _json_response(self, {'code': 0, 'message': 'ok'})
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)

        elif self.path == '/api/baidu-login':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                d = json.loads(body)
                result = baidu_login(d.get('bduss', ''), d.get('stoken', ''))
                _json_response(self, result)
            except Exception as e:
                _json_response(self, {'success': False, 'message': str(e)}, 400)

        elif self.path == '/api/baidu-logout':
            _json_response(self, baidu_logout())

        elif self.path == '/api/qq-logout':
            napcat = get_napcat_status()
            cred = napcat.get('_cred', '')
            if cred:
                result = qq_logout(cred)
                _napcat_cache['data'] = None  # 清缓存
                _json_response(self, {'success': True, 'result': result})
            else:
                _json_response(self, {'success': False, 'message': 'napcat not running'})

        elif self.path == '/api/room-add':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                room_id = int(json.loads(body).get('roomId', 0))
                result = brec_add_room(room_id)
                _json_response(self, {'code': 0, 'result': result})
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)

        elif self.path == '/api/room-start':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                d = json.loads(body)
                object_id = d.get('objectId', '')
                room_id   = d.get('roomId', 0)
                result = _brec_request(f"/api/room/{object_id}/start", method="POST")
                if room_id:
                    set_room_disabled(room_id, False)
                _json_response(self, {'code': 0, 'result': result})
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)

        elif self.path == '/api/room-stop':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                d = json.loads(body)
                object_id = d.get('objectId', '')
                room_id   = d.get('roomId', 0)
                result = _brec_request(f"/api/room/{object_id}/stop", method="POST")
                if room_id:
                    set_room_disabled(room_id, True)
                    _recording_last_true.pop(int(room_id), None)  # 清宽限期缓存
                _json_response(self, {'code': 0, 'result': result})
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)

        elif self.path == '/api/room-remove':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                object_id = json.loads(body).get('objectId', '')
                brec_remove_room(object_id)
                _json_response(self, {'code': 0, 'message': 'ok'})
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)

        elif self.path == '/api/notify-test':
            results = send_notifications("🔔 测试通知：QQ 机器人工作正常")
            _json_response(self, {'code': 0, 'results': results})

        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/api/status':
            napcat = get_napcat_status()
            cookie_str = get_brec_cookie()
            cookie_fields = parse_cookie_fields(cookie_str)
            _json_response(self, {
                'disk':   get_disk_info(),
                'upload': get_upload_status(),
                'napcat': {'running': napcat['running'], 'logged_in': napcat['logged_in']},
                'baidu':  get_baidu_status(),
                'notify': load_notify_config(),
                'brec_cookie': {
                    'has_sessdata':     bool(cookie_fields.get('SESSDATA')),
                    'has_buvid3':       bool(cookie_fields.get('buvid3')),
                    'dede_user_id':     cookie_fields.get('DedeUserID', ''),
                },
                'timestamp': datetime.now().isoformat()
            })

        elif self.path == '/api/notify-config':
            _json_response(self, load_notify_config())

        elif self.path == '/api/rooms':
            _json_response(self, get_brec_rooms())

        elif self.path == '/api/refresh-qrcode':
            napcat = get_napcat_status()
            if napcat.get('_cred'):
                refresh_qrcode(napcat['_cred'])
                _json_response(self, {'code': 0, 'message': 'ok'})
            else:
                _json_response(self, {'code': -1, 'message': 'napcat not running'})

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    threading.Thread(target=_poll_live, daemon=True).start()
    server = HTTPServer(('127.0.0.1', 2234), StatusHandler)
    print('状态 API 启动在 127.0.0.1:2234')
    server.serve_forever()
