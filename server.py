#!/usr/bin/env python3
import http.server
import socketserver
import subprocess
import os
import json
import logging
import tempfile
import shutil
import hashlib
import hmac
import secrets
import base64
import time
import threading
from urllib.parse import urlparse
from http.cookies import SimpleCookie

HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '8000'))
CACHE_DIR = os.environ.get('CACHE_DIR', os.path.join(tempfile.gettempdir(), 'music_cache'))
COOKIES_ENV = os.environ.get('COOKIES', '')
PROXY_URL = os.environ.get('IPLOOP_PROXY', '')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
PLAYLIST_DIR = os.path.join(DATA_DIR, 'playlists')
SECRET_FILE = os.path.join(DATA_DIR, 'secret.key')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PLAYLIST_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

users_lock = threading.Lock()

if os.path.exists(SECRET_FILE):
    with open(SECRET_FILE, 'rb') as f:
        SECRET_KEY = f.read()
else:
    SECRET_KEY = secrets.token_bytes(64)
    with open(SECRET_FILE, 'wb') as f:
        f.write(SECRET_KEY)
    os.chmod(SECRET_FILE, 0o600)

COOKIES_PATH = None
if COOKIES_ENV:
    COOKIES_PATH = os.path.join(tempfile.gettempdir(), 'cookies.txt')
    with open(COOKIES_PATH, 'w') as f:
        f.write(COOKIES_ENV)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('music')

EXT_TYPES = [
    ('.m4a', 'audio/mp4'),
    ('.webm', 'audio/webm'),
    ('.opus', 'audio/ogg'),
    ('.mp3', 'audio/mpeg'),
]

DENO_CANDIDATES = [
    os.path.expanduser('~/.deno/bin/deno'),
    '/opt/render/project/.deno/bin/deno',
    '/usr/local/bin/deno',
    '/usr/bin/deno',
]

def find_deno():
    for path in DENO_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which('deno')

DENO_PATH = find_deno()
if DENO_PATH:
    log.info('deno encontrado em %s', DENO_PATH)
else:
    log.warning('deno NAO encontrado')

if PROXY_URL:
    log.info('proxy configurado: %s', PROXY_URL.split('@')[0] + '@...')
else:
    log.warning('proxy NAO configurado — YouTube vai bloquear')

YT_CLIENTS = 'web_embedded,tv,ios,mweb,web_safari'
SESSION_DAYS = 30
DEFAULT_PLAYLIST = 'Favoritas'

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users):
    tmp = USERS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(users, f, indent=2)
    os.replace(tmp, USERS_FILE)

def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000)
    return salt, h.hex()

def check_password(password, salt, stored_hash):
    _, h = hash_password(password, salt)
    return hmac.compare_digest(h, stored_hash)

def make_token(username):
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = f'{username}:{expires}'
    sig = hmac.new(SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()
    raw = f'{payload}:{sig}'
    return base64.urlsafe_b64encode(raw.encode()).decode()

def verify_token(token):
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        username, expires_str, sig = decoded.rsplit(':', 2)
        expires = int(expires_str)
        if expires < time.time():
            return None
        expected = hmac.new(SECRET_KEY, f'{username}:{expires}'.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return username
    except Exception:
        return None

def get_username_from_request(handler):
    cookie_header = handler.headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except Exception:
        return None
    if 'session' not in cookies:
        return None
    return verify_token(cookies['session'].value)

def valid_username(u):
    if not u or not isinstance(u, str):
        return False
    if len(u) < 3 or len(u) > 32:
        return False
    return all(c.isalnum() or c in '-_.' for c in u)

def playlist_path(username):
    safe = ''.join(c for c in username if c.isalnum() or c in '-_.')
    return os.path.join(PLAYLIST_DIR, safe + '.json')

def _new_structure(tracks=None):
    return {
        'playlists': {DEFAULT_PLAYLIST: tracks or []},
        'active': DEFAULT_PLAYLIST
    }

def load_playlists(username):
    path = playlist_path(username)
    if not os.path.exists(path):
        return _new_structure()
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except Exception:
        return _new_structure()

    if isinstance(data, list):
        return _new_structure(data)

    if not isinstance(data, dict) or 'playlists' not in data:
        return _new_structure()

    playlists = data.get('playlists') or {}
    if not isinstance(playlists, dict) or not playlists:
        return _new_structure()

    active = data.get('active', '')
    if active not in playlists:
        active = next(iter(playlists))

    return {'playlists': playlists, 'active': active}

def save_playlists(username, data):
    path = playlist_path(username)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def sanitize_track(t):
    if not isinstance(t, dict):
        return None
    vid = t.get('id', '')
    if not isinstance(vid, str) or len(vid) != 11:
        return None
    return {
        'id': vid,
        'title': str(t.get('title', ''))[:300],
        'author': str(t.get('author', ''))[:200],
        'thumb': str(t.get('thumb', ''))[:500],
    }

def sanitize_playlists_payload(data):
    if not isinstance(data, dict):
        return None
    playlists = data.get('playlists')
    if not isinstance(playlists, dict):
        return None

    clean = {}
    for name, tracks in playlists.items():
        if not isinstance(name, str):
            continue
        name = name.strip()[:60]
        if not name:
            continue
        if not isinstance(tracks, list):
            continue
        clean_tracks = []
        for t in tracks:
            st = sanitize_track(t)
            if st:
                clean_tracks.append(st)
        clean[name] = clean_tracks

    if not clean:
        clean = {DEFAULT_PLAYLIST: []}

    active = data.get('active', '')
    if active not in clean:
        active = next(iter(clean))

    return {'playlists': clean, 'active': active}

class Handler(http.server.SimpleHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Range')

    def _json_response(self, code, obj, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _text_response(self, code, text, ctype='text/plain; charset=utf-8'):
        body = text.encode('utf-8') if isinstance(text, str) else text
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length == 0:
                return {}
            body = self.rfile.read(length)
            return json.loads(body.decode('utf-8'))
        except Exception:
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/health':
            self._text_response(200, 'ok')
            return

        if parsed.path == '/api/me':
            username = get_username_from_request(self)
            if username:
                self._json_response(200, {'username': username})
            else:
                self._json_response(401, {'error': 'nao autenticado'})
            return

        if parsed.path == '/api/playlist':
            username = get_username_from_request(self)
            if not username:
                self._json_response(401, {'error': 'nao autenticado'})
                return
            self._json_response(200, load_playlists(username))
            return

        if parsed.path.startswith('/debug/'):
            video_id = parsed.path.split('/debug/')[-1]
            video_id = ''.join(c for c in video_id if c.isalnum() or c in '-_')
            if not video_id:
                self._text_response(400, 'id invalido')
                return
            cmd = [
                'yt-dlp', '--list-formats', '--no-warnings',
                '--extractor-args', 'youtube:player_client=' + YT_CLIENTS,
            ]
            if DENO_PATH:
                cmd.extend(['--js-runtimes', 'deno:' + DENO_PATH])
            if COOKIES_PATH:
                cmd.extend(['--cookies', COOKIES_PATH])
            if PROXY_URL:
                cmd.extend(['--proxy', PROXY_URL])
            cmd.append('https://www.youtube.com/watch?v=' + video_id)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                out = '=== STDOUT ===\n' + (r.stdout or '') + '\n\n=== STDERR ===\n' + (r.stderr or '')
            except Exception as e:
                out = 'erro: ' + str(e)
            self._text_response(200, out)
            return

        if parsed.path.startswith('/audio/'):
            video_id = parsed.path.split('/audio/')[-1]
            self.serve_audio(video_id)
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/register':
            data = self._read_json_body()
            if data is None:
                self._json_response(400, {'error': 'json invalido'})
                return
            username = (data.get('username') or '').strip()
            password = data.get('password') or ''
            if not valid_username(username):
                self._json_response(400, {'error': 'username invalido (3-32, alfanumerico)'})
                return
            if len(password) < 4:
                self._json_response(400, {'error': 'senha muito curta (min 4)'})
                return

            with users_lock:
                users = load_users()
                if username in users:
                    self._json_response(409, {'error': 'usuario ja existe'})
                    return
                salt, h = hash_password(password)
                users[username] = {'salt': salt, 'hash': h, 'created': int(time.time())}
                save_users(users)

            save_playlists(username, _new_structure())
            token = make_token(username)
            cookie = f'session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS*86400}'
            self._json_response(200, {'username': username}, extra_headers={'Set-Cookie': cookie})
            return

        if parsed.path == '/api/login':
            data = self._read_json_body()
            if data is None:
                self._json_response(400, {'error': 'json invalido'})
                return
            username = (data.get('username') or '').strip()
            password = data.get('password') or ''

            with users_lock:
                users = load_users()
                u = users.get(username)

            if not u or not check_password(password, u['salt'], u['hash']):
                self._json_response(401, {'error': 'usuario ou senha invalidos'})
                return

            token = make_token(username)
            cookie = f'session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS*86400}'
            self._json_response(200, {'username': username}, extra_headers={'Set-Cookie': cookie})
            return

        if parsed.path == '/api/logout':
            cookie = 'session=; Path=/; HttpOnly; Max-Age=0'
            self._json_response(200, {'ok': True}, extra_headers={'Set-Cookie': cookie})
            return

        if parsed.path == '/api/playlist':
            username = get_username_from_request(self)
            if not username:
                self._json_response(401, {'error': 'nao autenticado'})
                return
            data = self._read_json_body()
            if data is None:
                self._json_response(400, {'error': 'payload invalido'})
                return
            clean = sanitize_playlists_payload(data)
            if clean is None:
                self._json_response(400, {'error': 'estrutura invalida'})
                return
            save_playlists(username, clean)
            self._json_response(200, {'ok': True})
            return

        self._json_response(404, {'error': 'endpoint nao encontrado'})

    def _find_cached(self, base):
        for ext, ct in EXT_TYPES:
            candidate = base + ext
            if os.path.exists(candidate):
                return candidate, ct
        return None, None

    def serve_audio(self, video_id):
        video_id = ''.join(c for c in video_id if c.isalnum() or c in '-_')
        if not video_id:
            self._text_response(400, 'id invalido')
            return

        cache_base = os.path.join(CACHE_DIR, video_id)
        cache_file, content_type = self._find_cached(cache_base)

        if not cache_file:
            log.info('baixando %s', video_id)
            cmd = [
                'yt-dlp', '-f', 'bestaudio/best',
                '-o', cache_base + '.%(ext)s',
                '--no-playlist', '--no-warnings',
                '--extractor-args', 'youtube:player_client=' + YT_CLIENTS,
            ]
            if DENO_PATH:
                cmd.extend(['--js-runtimes', 'deno:' + DENO_PATH])
            if COOKIES_PATH:
                cmd.extend(['--cookies', COOKIES_PATH])
            if PROXY_URL:
                cmd.extend(['--proxy', PROXY_URL])
            cmd.append('https://www.youtube.com/watch?v=' + video_id)
            try:
                subprocess.run(cmd, check=True, timeout=300, capture_output=True, text=True)
                log.info('baixado %s', video_id)
            except subprocess.CalledProcessError as e:
                err = e.stderr[-800:] if e.stderr else str(e)
                log.error('yt-dlp falhou: %s', err)
                self._text_response(500, 'Falha: ' + err)
                return
            except Exception as e:
                log.error('erro inesperado: %s', e)
                self._text_response(500, 'erro interno')
                return
            cache_file, content_type = self._find_cached(cache_base)

        if not cache_file:
            self._text_response(500, 'arquivo nao encontrado apos download')
            return

        file_size = os.path.getsize(cache_file)
        range_header = self.headers.get('Range')

        if range_header:
            try:
                rv = range_header.replace('bytes=', '')
                parts = rv.split('-')
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if parts[1] else file_size - 1
            except Exception:
                start, end = 0, file_size - 1

            length = end - start + 1
            self.send_response(206)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
            self.send_header('Accept-Ranges', 'bytes')
            self._cors()
            self.end_headers()
            with open(cache_file, 'rb') as f:
                f.seek(start)
                self.wfile.write(f.read(length))
        else:
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(file_size))
            self.send_header('Accept-Ranges', 'bytes')
            self._cors()
            self.end_headers()
            with open(cache_file, 'rb') as f:
                self.wfile.write(f.read())

    def log_message(self, fmt, *args):
        pass

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == '__main__':
    with ReusableTCPServer((HOST, PORT), Handler) as httpd:
        log.info('servidor em http://%s:%d', HOST, PORT)
        log.info('cache: %s', CACHE_DIR)
        log.info('dados: %s', DATA_DIR)
        log.info('cookies: %s', 'configurado' if COOKIES_PATH else 'ausente')
        log.info('proxy: %s', 'configurado' if PROXY_URL else 'ausente')
        httpd.serve_forever()
