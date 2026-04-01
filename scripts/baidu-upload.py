#!/usr/bin/env python3
"""百度网盘 Open API 上传工具，支持分片上传大文件"""
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

CONFIG_PATH = "/etc/baidu-openapi.json"
SLICE_SIZE = 4 * 1024 * 1024  # 4MB per slice
RESUME_DIR = "/tmp/baidu-upload-resume"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

def refresh_token(cfg):
    data = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': cfg['refresh_token'],
        'client_id': cfg['app_key'],
        'client_secret': cfg['secret_key'],
    }).encode()
    req = urllib.request.Request('https://openapi.baidu.com/oauth/2.0/token', data=data)
    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read().decode())
    if 'access_token' in result:
        cfg['access_token'] = result['access_token']
        cfg['refresh_token'] = result['refresh_token']
        cfg['expires_in'] = result['expires_in']
        save_config(cfg)
        return True
    return False

def api_request(url, cfg, data=None, method='GET', retry=True):
    sep = '&' if '?' in url else '?'
    full_url = f"{url}{sep}access_token={cfg['access_token']}"
    req = urllib.request.Request(full_url, data=data, method=method)
    if data and not req.has_header('Content-Type'):
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        err = json.loads(body) if body else {}
        if err.get('errno') == -6 and retry:
            # token expired, refresh
            if refresh_token(cfg):
                return api_request(url, cfg, data=data, method=method, retry=False)
        raise

def precreate(cfg, remote_path, file_size, block_md5_list):
    """预创建文件"""
    data = urllib.parse.urlencode({
        'path': remote_path,
        'size': file_size,
        'isdir': 0,
        'autoinit': 1,
        'rtype': 3,  # 覆盖同名文件
        'block_list': json.dumps(block_md5_list),
    }).encode()
    return api_request('https://pan.baidu.com/rest/2.0/xpan/file?method=precreate', cfg, data=data, method='POST')

def upload_slice(cfg, upload_id, part_seq, slice_data):
    """上传单个分片"""
    boundary = '----WebKitFormBoundary' + hashlib.md5(str(time.time()).encode()).hexdigest()[:16]
    body = b''
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="chunk"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    body += slice_data
    body += f'\r\n--{boundary}--\r\n'.encode()

    params = urllib.parse.urlencode({
        'method': 'upload',
        'type': 'tmpfile',
        'path': '/',
        'uploadid': upload_id,
        'partseq': part_seq,
        'access_token': cfg['access_token'],
    })
    url = f"https://d.pcs.baidu.com/rest/2.0/pcs/superfile2?{params}"
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    resp = urllib.request.urlopen(req, timeout=300)
    return json.loads(resp.read().decode())

def create_file(cfg, remote_path, file_size, upload_id, block_md5_list):
    """合并分片，创建文件"""
    data = urllib.parse.urlencode({
        'path': remote_path,
        'size': file_size,
        'isdir': 0,
        'rtype': 3,
        'uploadid': upload_id,
        'block_list': json.dumps(block_md5_list),
    }).encode()
    return api_request('https://pan.baidu.com/rest/2.0/xpan/file?method=create', cfg, data=data, method='POST')

def get_resume_path(local_path):
    """获取断点续传状态文件路径"""
    os.makedirs(RESUME_DIR, exist_ok=True)
    name = hashlib.md5(local_path.encode()).hexdigest()
    return os.path.join(RESUME_DIR, f"{name}.json")

def load_resume(local_path):
    """加载断点续传状态"""
    path = get_resume_path(local_path)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def save_resume(local_path, state):
    """保存断点续传状态"""
    path = get_resume_path(local_path)
    with open(path, 'w') as f:
        json.dump(state, f)

def clear_resume(local_path):
    """清除断点续传状态"""
    path = get_resume_path(local_path)
    if os.path.exists(path):
        os.remove(path)

def upload_file(local_path, remote_path):
    """上传文件到百度网盘（支持断点续传）"""
    cfg = load_config()
    file_size = os.path.getsize(local_path)
    num_slices = max(1, math.ceil(file_size / SLICE_SIZE))

    # 尝试恢复上次的上传状态
    resume = load_resume(local_path)
    if resume and resume.get('remote_path') == remote_path and resume.get('file_size') == file_size:
        upload_id = resume['upload_id']
        block_md5_list = resume['block_md5_list']
        start_slice = resume.get('completed_slices', 0)
        print(f"[upload] 恢复上传，从分片 {start_slice}/{num_slices} 继续", flush=True)
    else:
        start_slice = 0
        # 1. 计算分片 MD5
        block_md5_list = []
        with open(local_path, 'rb') as f:
            for i in range(num_slices):
                chunk = f.read(SLICE_SIZE)
                block_md5_list.append(hashlib.md5(chunk).hexdigest())

        # 2. 预创建
        pre = precreate(cfg, remote_path, file_size, block_md5_list)
        if pre.get('errno') != 0:
            print(f"precreate failed: {pre}", file=sys.stderr, flush=True)
            return False
        upload_id = pre['uploadid']

        # 保存初始状态
        save_resume(local_path, {
            'remote_path': remote_path,
            'file_size': file_size,
            'upload_id': upload_id,
            'block_md5_list': block_md5_list,
            'completed_slices': 0,
        })

    # 3. 上传分片
    with open(local_path, 'rb') as f:
        f.seek(start_slice * SLICE_SIZE)
        for i in range(start_slice, num_slices):
            chunk = f.read(SLICE_SIZE)
            for attempt in range(5):
                try:
                    result = upload_slice(cfg, upload_id, i, chunk)
                    if 'md5' in result:
                        uploaded = min((i + 1) * SLICE_SIZE, file_size)
                        pct = int(100 * uploaded / file_size)
                        print(f"[upload] {pct}% ({uploaded}/{file_size}) {i+1}/{num_slices} slices", flush=True)
                        # 每 10 个分片保存一次进度
                        if (i + 1) % 10 == 0 or i + 1 == num_slices:
                            save_resume(local_path, {
                                'remote_path': remote_path,
                                'file_size': file_size,
                                'upload_id': upload_id,
                                'block_md5_list': block_md5_list,
                                'completed_slices': i + 1,
                            })
                        break
                    else:
                        print(f"[upload] slice {i} unexpected: {result}", file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"[upload] slice {i} error (attempt {attempt+1}): {e}", file=sys.stderr, flush=True)
                    if attempt == 4:
                        print(f"[upload] 分片 {i} 失败5次，进度已保存，下次可续传", file=sys.stderr, flush=True)
                        return False
                    time.sleep(5 * (attempt + 1))

    # 4. 合并创建（带重试）
    for attempt in range(5):
        try:
            result = create_file(cfg, remote_path, file_size, upload_id, block_md5_list)
            if result.get('errno') == 0:
                print(f"[upload] OK: {remote_path} size={result.get('size',0)}", flush=True)
                clear_resume(local_path)
                return True
            else:
                print(f"[upload] create failed (attempt {attempt+1}): {result}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[upload] create error (attempt {attempt+1}): {e}", file=sys.stderr, flush=True)
        if attempt < 4:
            time.sleep(10 * (attempt + 1))
    return False

def list_dir(remote_dir):
    """列出远端目录"""
    cfg = load_config()
    params = urllib.parse.urlencode({'method': 'list', 'dir': remote_dir})
    result = api_request(f'https://pan.baidu.com/rest/2.0/xpan/file?{params}', cfg)
    return result

def get_file_size(remote_dir, filename):
    """获取远端文件大小"""
    result = list_dir(remote_dir)
    if result.get('errno') != 0:
        return -1
    for f in result.get('list', []):
        if f.get('server_filename') == filename:
            return f.get('size', 0)
    return -1

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <local_file> <remote_path>")
        print(f"       {sys.argv[0]} --list <remote_dir>")
        print(f"       {sys.argv[0]} --size <remote_dir> <filename>")
        sys.exit(1)

    if sys.argv[1] == '--list':
        result = list_dir(sys.argv[2])
        if result.get('errno') == 0:
            for f in result.get('list', []):
                kind = 'D' if f.get('isdir') else 'F'
                print(f"{kind} {f.get('server_filename','')} {f.get('size',0)}")
        else:
            print(f"Error: {result}", file=sys.stderr)
            sys.exit(1)
    elif sys.argv[1] == '--size':
        size = get_file_size(sys.argv[2], sys.argv[3])
        print(size)
    else:
        ok = upload_file(sys.argv[1], sys.argv[2])
        sys.exit(0 if ok else 1)
