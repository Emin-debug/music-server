#!/usr/bin/env python3
import http.server
import socketserver
import subprocess
import os
import logging
import tempfile
import shutil
from urllib.parse import urlparse

HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '8000'))
CACHE_DIR = os.environ.get('CACHE_DIR', os.path.join(tempfile.gettempdir(), 'music_cache'))
COOKIES_ENV = os.environ.get('COOKIES', '')

COOKIES_PATH = None
if COOKIES_ENV:
    COOKIES_PATH = os.path.join(tempfile.gettempdir(), 'cookies.txt')
    with open(COOKIES_PATH, 'w') as f:
        f.write(COOKIES_ENV)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('music')

os.makedirs(CACHE_DIR, exist_ok=True)

EXT_TYPES = [
    ('.m4a', 'audio/mp4'),
    ('.webm', 'audio/webm'),
    ('.opus', 'audio/ogg'),
    ('.mp3', 'audio/mpeg'),
]

DENO_CANDIDATES = [
    '/opt/render/project/.deno/bin/deno',
    os.path.expanduser('~/.deno/bin/deno'),
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
    log.warning('deno NAO encontrado. yt-dlp vai falhar sem runtime JS.')

YT_CLIENTS = 'web_embedded,tv,ios,mweb,web_safari'


class Handler(http.server.SimpleHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Range')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self._cors()
            self.end_headers()
            self.wfile.write(b'ok')
            return

        if parsed.path.startswith('/debug/'):
            video_id = parsed.path.split('/debug/')[-1]
            video_id = ''.join(c for c in video_id if c.isalnum() or c in '-_')
            if not video_id:
                self.send_response(400)
                self._cors()
                self.end_headers()
                return

            cmd = [
                'yt-dlp',
                '--list-formats',
                '--no-warnings',
                '--extractor-args', 'youtube:player_client=' + YT_CLIENTS,
            ]
            if DENO_PATH:
                cmd.extend(['--js-runtimes', 'deno:' + DENO_PATH])
            if COOKIES_PATH:
                cmd.extend(['--cookies', COOKIES_PATH])
            cmd.append('https://www.youtube.com/watch?v=' + video_id)

            log.info('debug list-formats %s', video_id)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                out = (
                    '=== COMMAND ===\n' + ' '.join(cmd) + '\n\n'
                    '=== DENO ===\n' + (DENO_PATH or 'nao encontrado') + '\n\n'
                    '=== COOKIES ===\n' + ('configurado' if COOKIES_PATH else 'ausente') + '\n\n'
                    '=== STDOUT ===\n' + (r.stdout or '(vazio)') + '\n\n'
                    '=== STDERR ===\n' + (r.stderr or '(vazio)') + '\n'
                )
            except Exception as e:
                out = 'erro: ' + str(e)

            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self._cors()
            self.end_headers()
            self.wfile.write(out.encode())
            return

        if parsed.path.startswith('/audio/'):
            video_id = parsed.path.split('/audio/')[-1]
            self.serve_audio(video_id)
            return

        super().do_GET()

    def _find_cached(self, base):
        for ext, ct in EXT_TYPES:
            candidate = base + ext
            if os.path.exists(candidate):
                return candidate, ct
        return None, None

    def serve_audio(self, video_id):
        video_id = ''.join(c for c in video_id if c.isalnum() or c in '-_')
        if not video_id:
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        cache_base = os.path.join(CACHE_DIR, video_id)
        cache_file, content_type = self._find_cached(cache_base)

        if not cache_file:
            log.info('baixando %s', video_id)

            cmd = [
                'yt-dlp',
                '-f', 'bestaudio/best',
                '-o', cache_base + '.%(ext)s',
                '--no-playlist',
                '--no-warnings',
                '--extractor-args', 'youtube:player_client=' + YT_CLIENTS,
            ]
            if DENO_PATH:
                cmd.extend(['--js-runtimes', 'deno:' + DENO_PATH])
            if COOKIES_PATH:
                cmd.extend(['--cookies', COOKIES_PATH])
            cmd.append('https://www.youtube.com/watch?v=' + video_id)

            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    timeout=300,
                    capture_output=True,
                    text=True
                )
                log.info('baixado %s', video_id)
            except subprocess.CalledProcessError as e:
                err = e.stderr[-800:] if e.stderr else str(e)
                log.error('yt-dlp falhou: %s', err)
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self._cors()
                self.end_headers()
                self.wfile.write(('Falha: ' + err).encode())
                return
            except Exception as e:
                log.error('erro inesperado: %s', e)
                self.send_response(500)
                self._cors()
                self.end_headers()
                return

            cache_file, content_type = self._find_cached(cache_base)

        if not cache_file:
            log.error('arquivo nao encontrado apos download: %s', cache_base)
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self._cors()
            self.end_headers()
            self.wfile.write(b'Falha: arquivo nao encontrado apos download')
            return

        file_size = os.path.getsize(cache_file)
        range_header = self.headers.get('Range')

        if range_header:
            try:
                range_val = range_header.replace('bytes=', '')
                parts = range_val.split('-')
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
        log.info('cookies: %s', 'configurado' if COOKIES_PATH else 'ausente')
        httpd.serve_forever()
