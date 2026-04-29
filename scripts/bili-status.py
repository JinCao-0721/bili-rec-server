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
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime

NAPCAT_PORT = 6099
NAPCAT_TOKEN = "your_napcat_token"
NAPCAT_WEBUI_CONFIG_PATH = "/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json"
ONEBOT_URL = "http://127.0.0.1:5700"
NAPCAT_TIMEOUT = 8
NAPCAT_QRCODE_PATH = "/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png"
NOTIFY_CONFIG_PATH = "/etc/bili-notify.json"
RECORD_CONFIG_PATH = "/etc/bili-record.json"
AUTH_CONFIG_PATH = "/etc/bili-auth.json"
BREC_URL = "http://127.0.0.1:2233"
BREC_USER = "your_brec_username"
BREC_PASS = "your_brec_password"
BREC_START_SH = "/usr/local/bin/brec-start.sh"
BREC_CONFIG_JSON = "/data/recordings/config.json"

_napcat_cache = {"ts": 0, "data": None}
_baidu_cache  = {"ts": 0, "data": None}
_CACHE_TTL = 10
_recording_last_true = {}  # room_id -> timestamp，上次 recording=True 的时间
_RECORDING_GRACE = 5       # 秒：recording 变 False 后宽限期，避免切片间隙闪烁
_napcat_recovery = {
    "active": False,
    "attempt": 0,
    "max_attempts": 2,
    "need_manual_login": False,
    "last_state": "idle",
    "message": "",
    "updated_at": 0,
}
_napcat_recovery_lock = threading.Lock()
_napcat_last_login_seen = None

# ── 直播状态轮询（推送逻辑，与录制完全解耦）────────────────────
_live_status = {}   # room_id -> bool，上次已知直播状态
_live_lock   = threading.Lock()


def _baidu_openapi_refresh(cfg):
    data = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': cfg['refresh_token'],
        'client_id': cfg['app_key'],
        'client_secret': cfg['secret_key'],
    }).encode()
    req = urllib.request.Request('https://openapi.baidu.com/oauth/2.0/token', data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
    if 'access_token' not in result:
        raise RuntimeError('Baidu OpenAPI token refresh failed')
    cfg['access_token'] = result['access_token']
    cfg['refresh_token'] = result.get('refresh_token', cfg.get('refresh_token', ''))
    cfg['expires_in'] = result.get('expires_in', cfg.get('expires_in', 0))
    with open('/etc/baidu-openapi.json', 'w') as f:
        json.dump(cfg, f, indent=2)
    return cfg


def _baidu_openapi_uinfo(cfg, retry=True):
    token = cfg.get('access_token', '')
    req = urllib.request.Request(
        f"https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo&access_token={token}"
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        payload = {}
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            pass
        if retry and payload.get('errno') == -6:
            return _baidu_openapi_uinfo(_baidu_openapi_refresh(cfg), retry=False)
        raise
    if retry and data.get('errno') == -6:
        return _baidu_openapi_uinfo(_baidu_openapi_refresh(cfg), retry=False)
    return data


def _load_napcat_webui_config():
    try:
        with open(NAPCAT_WEBUI_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


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


# ── 登录认证 ──────────────────────────────────────────────────

import secrets
import http.cookies

_sessions = {}  # token -> {"user": username, "ts": timestamp}
_SESSION_TTL = 86400 * 7  # 7 天


def _load_auth_config():
    try:
        with open(AUTH_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {"username": "admin", "password": "admin"}


def _check_login(username, password):
    cfg = _load_auth_config()
    return username == cfg.get("username") and password == cfg.get("password")


def _create_session(username):
    token = secrets.token_hex(32)
    _sessions[token] = {"user": username, "ts": time.time()}
    return token


def _validate_session(cookie_header):
    if not cookie_header:
        return False
    c = http.cookies.SimpleCookie()
    try:
        c.load(cookie_header)
    except Exception:
        return False
    token = c.get("session")
    if not token:
        return False
    token = token.value
    sess = _sessions.get(token)
    if not sess:
        return False
    if time.time() - sess["ts"] > _SESSION_TTL:
        _sessions.pop(token, None)
        return False
    # Sliding expiration: keep active sessions alive while the page is in use.
    sess["ts"] = time.time()
    return True


# 不需要认证的路径
_PUBLIC_PATHS = {'/api/login', '/api/auth-check', '/api/brec-webhook', '/api/blrec-webhook'}


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

def _resolve_brec_auth():
    try:
        with open(BREC_START_SH, 'r', encoding='utf-8') as f:
            text = f.read()
        user_match = re.search(r'--http-basic-user\s+([^\s\\]+)', text)
        pass_match = re.search(r'--http-basic-pass\s+([^\s\\]+)', text)
        if user_match and pass_match:
            return user_match.group(1), pass_match.group(1)
    except Exception:
        pass
    return BREC_USER, BREC_PASS


def _brec_request(path, method="GET", data=None):
    brec_user, brec_pass = _resolve_brec_auth()
    auth = base64.b64encode(f"{brec_user}:{brec_pass}".encode()).decode()
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
    """从 brec-start.sh 找到纯数字行读取房间号列表"""
    with open(BREC_START_SH, 'r') as f:
        lines = f.read().rstrip().split('\n')
    # 找最后一个只含数字（空格分隔）的行
    for line in reversed(lines):
        ids = [int(t) for t in line.split() if t.isdigit()]
        if ids:
            return ids
    return []


def _set_sh_room_ids(room_ids):
    """将房间号列表写回 brec-start.sh 中的房间号行"""
    with open(BREC_START_SH, 'r') as f:
        lines = f.read().rstrip().split('\n')
    # 找最后一个只含数字的行并替换，找不到则追加在 /data/recordings 行之后
    for i in reversed(range(len(lines))):
        if all(t.isdigit() for t in lines[i].split() if t):
            lines[i] = '  ' + ' '.join(str(r) for r in room_ids) if room_ids else ''
            break
    else:
        lines.append('  ' + ' '.join(str(r) for r in room_ids) if room_ids else '')
    with open(BREC_START_SH, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def get_brec_cookie():
    """从 config.json 读取当前 Cookie 字符串"""
    try:
        with open(BREC_CONFIG_JSON, 'r') as f:
            cfg = json.load(f)
        cookie = cfg.get('global', {}).get('Cookie', {})
        if cookie.get('HasValue'):
            return cookie.get('Value', '')
    except Exception:
        pass
    return ''


def set_brec_cookie(cookie_str):
    """通过 GraphQL API 热更新 Cookie（无需重启录播姬）"""
    escaped = cookie_str.replace('\\', '\\\\').replace('"', '\\"')
    query = ('mutation { setConfig(config: { optionalCookie: '
             '{ hasValue: true, value: "' + escaped + '" } }) '
             '{ optionalCookie { hasValue value } } }')
    brec_user, brec_pass = _resolve_brec_auth()
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        f'{BREC_URL}/graphql',
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Basic ' + base64.b64encode(
                f'{brec_user}:{brec_pass}'.encode()).decode(),
        },
    )
    urllib.request.urlopen(req, timeout=10)


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
    result = _brec_request("/api/room", method="POST", data={"roomId": room_id, "autoRecord": True})
    # 新添加的房间清除 disabled 状态，并自动开始录制
    set_room_disabled(room_id, False)
    try:
        obj_id = result.get("objectId", "")
        if obj_id:
            _brec_request(f"/api/room/{obj_id}/start", method="POST")
    except Exception:
        pass
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
    port = _resolve_napcat_port()
    token = _resolve_napcat_token()
    if not token:
        return ""
    h = hashlib.sha256((token + ".napcat").encode()).hexdigest()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/auth/login",
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


def _resolve_napcat_token():
    data = _load_napcat_webui_config()
    token = str(data.get("token", "")).strip()
    if token:
        return token
    return NAPCAT_TOKEN if NAPCAT_TOKEN != "your_napcat_token" else ""


def _resolve_napcat_port():
    data = _load_napcat_webui_config()
    try:
        port = int(data.get("port", 0))
        if port > 0:
            return port
    except Exception:
        pass
    return NAPCAT_PORT


def _is_napcat_process_running():
    commands = [
        ['systemctl', 'is-active', 'napcat'],
        ['pgrep', '-af', '/root/Napcat/opt/QQ/qq'],
        ['pgrep', '-af', 'Napcat'],
        ['pgrep', '-af', 'napcat'],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if cmd[:2] == ['systemctl', 'is-active']:
                if result.returncode == 0 and result.stdout.strip() == 'active':
                    return True
            elif result.stdout.strip():
                return True
        except Exception:
            continue
    return False


def _napcat_post(path, cred):
    port = _resolve_napcat_port()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cred}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=NAPCAT_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _napcat_recovery_snapshot():
    with _napcat_recovery_lock:
        return dict(_napcat_recovery)


def _napcat_recovery_update(**kwargs):
    with _napcat_recovery_lock:
        _napcat_recovery.update(kwargs)
        _napcat_recovery["updated_at"] = time.time()


def _napcat_restart_service():
    proc = subprocess.run(
        ["systemctl", "restart", "napcat"],
        capture_output=True,
        text=True,
        timeout=30
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-300:],
        "stderr": proc.stderr[-300:],
    }


def _napcat_probe_status_once():
    cred = _napcat_credential()
    token = _resolve_napcat_token()
    if not cred:
        return {
            "running": _is_napcat_process_running(),
            "logged_in": False,
            "_cred": "",
            "token": token,
        }
    d = _napcat_post("/api/QQLogin/CheckLoginStatus", cred)
    is_login = d.get("data", {}).get("isLogin", False)
    return {
        "running": True,
        "logged_in": is_login,
        "_cred": cred,
        "token": token,
    }


def _napcat_auto_recover_worker():
    global _napcat_cache
    for attempt in range(1, 3):
        _napcat_recovery_update(
            active=True,
            attempt=attempt,
            last_state="recovering",
            message=f"检测到掉线，正在尝试自动恢复（第 {attempt}/2 次）…",
            need_manual_login=False,
        )
        restart = _napcat_restart_service()
        if not restart["ok"]:
            _napcat_recovery_update(
                active=False,
                attempt=attempt,
                last_state="failed",
                message=f"自动恢复失败：重启 NapCat 失败（第 {attempt}/2 次）",
                need_manual_login=(attempt >= 2),
            )
            if attempt >= 2:
                return
            time.sleep(1)
            continue

        _napcat_recovery_update(
            active=True,
            attempt=attempt,
            last_state="waiting_check",
            message=f"NapCat 已重启，等待 30 秒后复查登录状态（第 {attempt}/2 次）…",
            need_manual_login=False,
        )

        restored = False
        last_status = {}
        for _ in range(30):
            time.sleep(1)
            _napcat_cache = {"ts": 0, "data": None}
            last_status = _napcat_probe_status_once()
            if last_status.get("running") and last_status.get("logged_in"):
                restored = True
                break

        if restored:
            _napcat_cache = {"ts": 0, "data": None}
            _napcat_recovery_update(
                active=False,
                attempt=attempt,
                last_state="recovered",
                message=f"QQ 登录已自动恢复（第 {attempt}/2 次尝试成功）",
                need_manual_login=False,
            )
            return

    _napcat_cache = {"ts": 0, "data": None}
    _napcat_recovery_update(
        active=False,
        attempt=2,
        last_state="manual_required",
        message="自动恢复两次均失败，需重新扫码登录",
        need_manual_login=True,
    )


def _napcat_maybe_start_auto_recovery(result):
    global _napcat_last_login_seen
    is_login = bool(result.get("logged_in"))
    running = bool(result.get("running"))
    recovery = _napcat_recovery_snapshot()
    should_trigger = (
        running
        and not is_login
        and _napcat_last_login_seen is True
        and not recovery.get("active")
        and not recovery.get("need_manual_login")
    )
    _napcat_last_login_seen = is_login
    if should_trigger:
        thread = threading.Thread(target=_napcat_auto_recover_worker, daemon=True)
        thread.start()


def get_napcat_status():
    global _napcat_cache
    now = time.time()
    if _napcat_cache["data"] is not None and now - _napcat_cache["ts"] < _CACHE_TTL:
        return _napcat_cache["data"]
    result = _napcat_probe_status_once()
    if result.get("logged_in"):
        _napcat_recovery_update(
            active=False,
            attempt=0,
            last_state="idle",
            message="",
            need_manual_login=False,
        )
    _napcat_maybe_start_auto_recovery(result)
    result["recovery"] = _napcat_recovery_snapshot()
    _napcat_cache = {"ts": now, "data": result}
    return result


def refresh_qrcode(cred):
    before = get_qrcode_meta()
    refresh_result = _napcat_post("/api/QQLogin/RefreshQRcode", cred)
    # NapCat's refresh callback returns before the QR image is necessarily
    # written to disk, so actively request the latest QR code and wait a bit.
    get_result = _napcat_post("/api/QQLogin/GetQQLoginQrcode", cred)
    after = None
    changed = False
    for _ in range(12):
        time.sleep(0.5)
        after = get_qrcode_meta()
        if after and (not before or before.get("mtime") != after.get("mtime") or before.get("size") != after.get("size")):
            changed = True
            break
    if after is None:
        after = get_qrcode_meta()
    return {
        "upstream": refresh_result,
        "get_qrcode": get_result,
        "qrcode_url": (get_result.get("data") or {}).get("qrcode", ""),
        "before": before,
        "after": after,
        "changed": changed,
    }


def restart_napcat_and_refresh_qrcode():
    global _napcat_cache
    _napcat_recovery_update(
        active=False,
        attempt=0,
        last_state="manual_restart",
        message="",
        need_manual_login=False,
    )
    try:
        proc = subprocess.run(
            ["systemctl", "restart", "napcat"],
            capture_output=True,
            text=True,
            timeout=30
        )
    except Exception as e:
        return {
            "ok": False,
            "message": f"重启 NapCat 失败: {e}",
            "restart": {"returncode": -1, "stderr": str(e)},
        }

    if proc.returncode != 0:
        return {
            "ok": False,
            "message": "重启 NapCat 失败",
            "restart": {
                "returncode": proc.returncode,
                "stdout": proc.stdout[-500:],
                "stderr": proc.stderr[-500:],
            },
        }

    _napcat_cache = {"ts": 0, "data": None}
    last_status = {}
    for _ in range(30):
        time.sleep(1)
        _napcat_cache = {"ts": 0, "data": None}
        last_status = get_napcat_status()
        if last_status.get("running"):
            break

    cred = last_status.get("_cred") or _napcat_credential()
    if not cred:
        return {
            "ok": False,
            "message": "NapCat 已重启，但暂时未拿到新的登录凭据",
            "restart": {"returncode": proc.returncode},
            "status": last_status,
        }

    refresh = refresh_qrcode(cred)
    upstream = refresh.get("upstream") or {}
    upstream_ok = upstream.get("status") == "ok" or upstream.get("code") in (0, 200)
    changed = refresh.get("changed") is True
    qrcode_url = refresh.get("qrcode_url", "")
    if upstream_ok and qrcode_url:
        message = "已重启 NapCat 并刷新二维码" if changed else "已重启 NapCat，但二维码链接仍未变化"
        return {
            "ok": True,
            "message": message,
            "changed": changed,
            "qrcode": refresh.get("after"),
            "qrcode_url": qrcode_url,
            "status": last_status,
            "upstream": upstream,
        }

    return {
        "ok": False,
        "message": "NapCat 已重启，但未拿到新的二维码链接",
        "changed": changed,
        "qrcode": refresh.get("after"),
        "qrcode_url": qrcode_url,
        "status": last_status,
        "upstream": upstream,
    }


def get_napcat_qrcode_url(cred):
    result = _napcat_post("/api/QQLogin/GetQQLoginQrcode", cred)
    return (result.get("data") or {}).get("qrcode", "")


def get_qrcode_meta():
    try:
        st = os.stat(NAPCAT_QRCODE_PATH)
        return {
            "path": NAPCAT_QRCODE_PATH,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(),
        }
    except Exception:
        return None


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
    global _baidu_cache
    now = time.time()
    if _baidu_cache["data"] is not None and now - _baidu_cache["ts"] < _CACHE_TTL:
        return _baidu_cache["data"]
    try:
        cfg = json.load(open('/etc/baidu-openapi.json'))
        uinfo = _baidu_openapi_uinfo(cfg)
        name = uinfo.get('baidu_name', '')
        data = {
            'logged_in': bool(name),
            'username': name,
            'mode': 'openapi',
            'auto_refresh': True,
            'message': '使用百度网盘 Open API 授权，access token 过期后会自动刷新',
        }
    except Exception as e:
        data = {
            'logged_in': False,
            'username': '',
            'mode': 'openapi',
            'auto_refresh': True,
            'message': str(e),
        }
    _baidu_cache = {"ts": now, "data": data}
    return data

def qq_switch_account():
    subprocess.run(['systemctl', 'stop', 'napcat'], capture_output=True)
    # 清除 NapCat 自动登录账号
    webui_path = '/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json'
    try:
        with open(webui_path, 'r') as f:
            cfg = json.load(f)
        cfg['autoLoginAccount'] = ''
        with open(webui_path, 'w') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception:
        pass
    # 清除 QQ 登录 session 文件
    import glob
    for p in glob.glob('/root/.config/QQ/nt_qq/global/nt_data/Login/.*'):
        try:
            os.remove(p)
        except Exception:
            pass
    subprocess.run(['systemctl', 'start', 'napcat'], capture_output=True)
    return True


def get_upload_status():
    ps = subprocess.run(['pgrep', '-f', 'baidu-upload.py'], capture_output=True, text=True)
    active = bool(ps.stdout.strip())
    upload_file, upload_speed, upload_progress = '', '', ''
    log_path = '/var/log/bili-upload.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        # 从近 2000 行找最近一次上传事件，记录其行号
        start_idx = -1
        for i in range(len(lines) - 1, max(len(lines) - 2000, -1), -1):
            line = lines[i]
            if '开始上传' in line:
                m = re.search(r'开始上传: (.+?) →', line)
                if m:
                    upload_file = os.path.basename(m.group(1).strip())
                active = True
                start_idx = i
                break
            if '上传成功' in line or '上传失败' in line:
                break
        # 只在 开始上传 之后的行中找进度，避免拿到上一个文件的进度
        if active:
            search_lines = lines[start_idx:] if start_idx >= 0 else lines[-200:]
            for line in reversed(search_lines):
                # Open API 格式: [upload] 38% (1610612736/4292091291) 410/1024 slices
                m = re.search(r'\[upload\]\s+(\d+)%\s+\((\d+)/(\d+)\)', line)
                if m:
                    pct = m.group(1)
                    uploaded = int(m.group(2))
                    total = int(m.group(3))
                    def human(b):
                        if b >= 1073741824: return f"{b/1073741824:.2f} GB"
                        if b >= 1048576: return f"{b/1048576:.1f} MB"
                        return f"{b/1024:.0f} KB"
                    upload_progress = f"{human(uploaded)}/{human(total)}"
                    break
    return {'active': active, 'file': upload_file,
            'speed': upload_speed, 'progress': upload_progress}


def get_upload_queue():
    """扫描录播目录，返回待上传的视频文件列表（已上传的会被删除，剩下的即为待上传）"""
    queue = []
    watch_dir = "/data/recordings"
    for root, dirs, files in os.walk(watch_dir):
        for f in sorted(files):
            if f.endswith(('.flv', '.ts', '.mp4')):
                path = os.path.join(root, f)
                try:
                    size = os.path.getsize(path)
                    queue.append({'name': f, 'size': size})
                except Exception:
                    pass
    return queue


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

    def _require_auth(self):
        if self.path in _PUBLIC_PATHS:
            return True
        cookie = self.headers.get('Cookie', '')
        if _validate_session(cookie):
            return True
        _json_response(self, {'code': 401, 'message': 'unauthorized'}, 401)
        return False

    def do_POST(self):
        if self.path == '/api/login':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                d = json.loads(body)
                username = d.get('username', '')
                password = d.get('password', '')
                if _check_login(username, password):
                    token = _create_session(username)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Set-Cookie', f'session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_SESSION_TTL}')
                    self.end_headers()
                    self.wfile.write(json.dumps({'code': 0, 'message': 'ok'}).encode())
                else:
                    _json_response(self, {'code': -1, 'message': '用户名或密码错误'}, 401)
            except Exception as e:
                _json_response(self, {'code': -1, 'message': str(e)}, 400)
            return

        if self.path == '/api/auth-check':
            cookie = self.headers.get('Cookie', '')
            ok = _validate_session(cookie)
            _json_response(self, {'code': 0, 'authenticated': ok})
            return

        if not self._require_auth():
            return

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

        elif self.path == '/api/qq-logout':
            ok = qq_switch_account()
            _napcat_cache['data'] = None  # 清缓存
            _json_response(self, {'success': ok})

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
        if self.path == '/api/auth-check':
            cookie = self.headers.get('Cookie', '')
            ok = _validate_session(cookie)
            _json_response(self, {'code': 0, 'authenticated': ok})
            return

        if not self._require_auth():
            return

        if self.path == '/api/status':
            napcat = get_napcat_status()
            qrcode_url = ""
            if napcat.get('running') and not napcat.get('logged_in') and napcat.get('_cred'):
                qrcode_url = get_napcat_qrcode_url(napcat['_cred'])
            cookie_str = get_brec_cookie()
            cookie_fields = parse_cookie_fields(cookie_str)
            _json_response(self, {
                'disk':   get_disk_info(),
                'upload': get_upload_status(),
                'napcat': {
                    'running': napcat['running'],
                    'logged_in': napcat['logged_in'],
                    'token': napcat.get('token', ''),
                    'qrcode_url': qrcode_url,
                    'recovery': napcat.get('recovery', {}),
                },
                'baidu':  get_baidu_status(),
                'notify': load_notify_config(),
                'brec_cookie': {
                    'has_sessdata':     bool(cookie_fields.get('SESSDATA')),
                    'has_buvid3':       bool(cookie_fields.get('buvid3')),
                    'dede_user_id':     cookie_fields.get('DedeUserID', ''),
                },
                'upload_queue': get_upload_queue(),
                'timestamp': datetime.now().isoformat()
            })

        elif self.path == '/api/notify-config':
            _json_response(self, load_notify_config())

        elif self.path == '/api/rooms':
            _json_response(self, get_brec_rooms())

        elif self.path == '/api/refresh-qrcode':
            result = restart_napcat_and_refresh_qrcode()
            if result.get('ok'):
                _json_response(self, {
                    'code': 0,
                    'message': result.get('message', '已重启 NapCat 并刷新二维码'),
                    'changed': result.get('changed', False),
                    'qrcode': result.get('qrcode'),
                    'qrcode_url': result.get('qrcode_url', ''),
                })
            else:
                _json_response(self, {
                    'code': -1,
                    'message': result.get('message', '重启 NapCat 并刷新二维码失败'),
                    'changed': result.get('changed', False),
                    'qrcode': result.get('qrcode'),
                    'qrcode_url': result.get('qrcode_url', ''),
                    'upstream': result.get('upstream', {}),
                    'status': result.get('status', {}),
                    'restart': result.get('restart', {}),
                }, 502)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    threading.Thread(target=_poll_live, daemon=True).start()
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    server = ThreadedHTTPServer(('127.0.0.1', 2234), StatusHandler)
    print('状态 API 启动在 127.0.0.1:2234')
    server.serve_forever()
