#!/usr/bin/env python3
import http.server
import socketserver
import subprocess
import os
import logging
import tempfile
from urllib.parse import urlparse

# Configuração via variáveis de ambiente (Render) ou padrão local
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '8000'))
CACHE_DIR = os.environ.get('CACHE_DIR', os.path.join(tempfile.gettempdir(), 'music_cache'))
COOKIES_ENV = os.environ.get('COOKIES', '')

# Escreve os cookies em arquivo temporário se vieram por env var
COOKIES_PATH = None
if COOKIES_ENV:
    COOKIES_PATH = os.path.join(tempfile.gettempdir(), 'cookies.txt')
    with open(COOKIES_PATH, 'w') as f:
        f.write(COOKIES_ENV)
else:
    # fallback local: arquivo cookies.txt ao lado do server.py
    local_cookies = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    if os.path.exists(local_cookies):
        COOKIES_PATH = local_cookies

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('music')

os.makedirs(CACHE_DIR, exist_ok=True)


class Handler(http.server.BaseHTTPRequestHandler):
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

        # Health check
        if parsed.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self._cors()
            self.end_headers()
            self.wfile.write(b'ok')
            return

        # Áudio
        if parsed.path.startswith('/audio/'):
            video_id = parsed.path.split('/audio/')[-1]
            self.serve_audio(video_id)
            return

        # HTML e arquivos estáticos (mesma pasta do server.py)
        super().do_GET()

    def serve_audio(self, video_id):
        video_id = ''.join(c for c in video_id if c.isalnum() or c in '-_')
        if not video_id:
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        cache_file = os.path.join(CACHE_DIR, video_id + '.m4a')

        if not os.path.exists(cache_file):
            log.info('baixando %s', video_id)
            cmd = [
                'yt-dlp',
                '-f', 'bestaudio[ext=m4a]/bestaudio',
                '-o', cache_file,
                '--no-playlist',
                '--no-warnings',
                'https://www.youtube.com/watch?v=' + video_id
            ]
            if COOKIES_PATH:
                cmd.extend(['--cookies', COOKIES_PATH])

            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    timeout=300,
                    capture_output=True,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                err = e.stderr[-500:] if e.stderr else str(e)
                log.error('yt-dlp falhou: %s', err)
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain')
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

        if not os.path.exists(cache_file):
            self.send_response(500)
            self._cors()
            self.end_headers()
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
            self.send_header('Content-Type', 'audio/mp4')
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
            self.send_header('Content-Type', 'audio/mp4')
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
