#!/usr/bin/env python3
"""
GeoProxy — Full-featured HTTP/HTTPS proxy server.
Configure your browser to use localhost:8080 as HTTP proxy.

Usage:
  python3 geoproxy.py                          # Direct proxy (no geo-bypass)
  python3 geoproxy.py --upstream http://user:pass@us-proxy:3128  # Route through US proxy

Browser setup:
  Chrome/Edge: Settings → Proxy → Manual → localhost:8080
  Firefox:    Settings → Network Proxy → Manual → localhost:8080
"""

import argparse
import base64
import logging
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

UPSTREAM_PROXY = None
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('geoproxy')


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── CONNECT (HTTPS tunnel) ──────────────────────────────────────────
    def do_CONNECT(self):
        host, port = self._parse_connect()
        if not host:
            self.send_error(400, "Bad CONNECT request")
            return
        log.info(f"🔗 CONNECT {host}:{port}")
        if UPSTREAM_PROXY:
            self._connect_upstream(host, port)
        else:
            self._connect_direct(host, port)

    def _parse_connect(self):
        try:
            parts = self.path.rsplit(':', 1)
            return parts[0], int(parts[1]) if len(parts) > 1 else 443
        except Exception:
            return None, None

    def _connect_direct(self, host, port):
        try:
            t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            t.settimeout(CONNECT_TIMEOUT)
            t.connect((host, port))
            t.settimeout(None)
            self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._relay(t)
        except Exception as e:
            log.warning(f"CONNECT fail {host}:{port}: {e}")
            self._error(502, str(e))

    def _connect_upstream(self, host, port):
        up = urlparse(UPSTREAM_PROXY)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect((up.hostname, up.port or 8080))
            req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            if up.username:
                cred = base64.b64encode(f"{up.username}:{up.password or ''}".encode()).decode()
                req += f"Proxy-Authorization: Basic {cred}\r\n"
            req += "\r\n"
            s.sendall(req.encode())
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(4096)
                if not chunk:
                    raise ConnectionError("Upstream closed")
                resp += chunk
            if b"200" not in resp.split(b"\r\n")[0]:
                log.warning(f"Upstream CONNECT failed: {resp[:100]}")
                self._error(502, "Upstream CONNECT failed")
                return
            self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._relay(s)
        except Exception as e:
            log.warning(f"Upstream CONNECT fail: {e}")
            self._error(502, str(e))

    # ── HTTP methods ────────────────────────────────────────────────────
    def do_GET(self):
        self._forward('GET')

    def do_POST(self):
        self._forward('POST')

    def do_HEAD(self):
        self._forward('HEAD')

    def do_PUT(self):
        self._forward('PUT')

    def do_DELETE(self):
        self._forward('DELETE')

    def do_OPTIONS(self):
        self._forward('OPTIONS')

    def _forward(self, method):
        url = self.path
        if not (url.startswith('http://') or url.startswith('https://')):
            self.send_error(400, "Absolute URL required")
            return
        parsed = urlparse(url)
        log.info(f"{method} {url[:150]}")
        if UPSTREAM_PROXY:
            self._forward_upstream(method, url)
        else:
            self._forward_direct(method, parsed.hostname, parsed.port or 80, url)

    def _forward_direct(self, method, host, port, url):
        try:
            t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            t.settimeout(CONNECT_TIMEOUT)
            t.connect((host, port))
            req = f"{method} {url} HTTP/1.1\r\nHost: {host}"
            for k, v in self.headers.items():
                if k.lower() not in ('connection', 'proxy-connection'):
                    req += f"\r\n{k}: {v}"
            cl = self.headers.get('Content-Length')
            req += "\r\n\r\n"
            t.sendall(req.encode())
            if cl:
                t.sendall(self.rfile.read(int(cl)))
            self._read_forward(t)
        except Exception as e:
            log.warning(f"Forward fail: {e}")
            self._error(502, str(e))

    def _forward_upstream(self, method, url):
        up = urlparse(UPSTREAM_PROXY)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect((up.hostname, up.port or 8080))
            parsed = urlparse(url)
            req = f"{method} {url} HTTP/1.1\r\nHost: {parsed.hostname}"
            if up.username:
                cred = base64.b64encode(f"{up.username}:{up.password or ''}".encode()).decode()
                req += f"\r\nProxy-Authorization: Basic {cred}"
            for k, v in self.headers.items():
                if k.lower() not in ('connection', 'proxy-connection'):
                    req += f"\r\n{k}: {v}"
            cl = self.headers.get('Content-Length')
            req += "\r\n\r\n"
            s.sendall(req.encode())
            if cl:
                s.sendall(self.rfile.read(int(cl)))
            self._read_forward(s)
        except Exception as e:
            log.warning(f"Upstream forward fail: {e}")
            self._error(502, str(e))

    # ── Helpers ─────────────────────────────────────────────────────────
    def _read_forward(self, sock):
        sock.settimeout(READ_TIMEOUT)
        buf = b""
        headers_done = False
        while True:
            try:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                if not headers_done and b"\r\n\r\n" in chunk:
                    buf += chunk
                    self.wfile.write(buf)
                    self.wfile.flush()
                    headers_done = True
                    buf = b""
                elif headers_done:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                else:
                    buf += chunk
            except socket.timeout:
                break
            except Exception:
                break
        if buf:
            self.wfile.write(buf)

    def _relay(self, target_sock):
        client_sock = self.connection
        def c2t():
            try:
                client_sock.settimeout(5)
                while True:
                    d = client_sock.recv(8192)
                    if not d:
                        break
                    target_sock.sendall(d)
            except (socket.timeout, Exception):
                pass
            finally:
                try:
                    target_sock.shutdown(socket.SHUT_WR)
                except Exception:
                    pass
        def t2c():
            try:
                target_sock.settimeout(5)
                while True:
                    d = target_sock.recv(8192)
                    if not d:
                        break
                    client_sock.sendall(d)
            except (socket.timeout, Exception):
                pass
            finally:
                try:
                    client_sock.shutdown(socket.SHUT_WR)
                except Exception:
                    pass
        threading.Thread(target=c2t, daemon=True).start()
        threading.Thread(target=t2c, daemon=True).start()

    def _error(self, code, msg):
        try:
            self.wfile.write(f"HTTP/1.1 {code} Error\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n{msg}".encode())
        except Exception:
            pass

    def log_message(self, fmt, *args):
        pass


class ThreadedServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 256


def main():
    parser = argparse.ArgumentParser(description='GeoProxy — HTTP/HTTPS proxy server')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--upstream', type=str, default=None,
                        help='US proxy: http://user:pass@host:port')
    args = parser.parse_args()

    global UPSTREAM_PROXY
    UPSTREAM_PROXY = args.upstream

    mode = "🌍 DIRECT" if not UPSTREAM_PROXY else f"🇺🇸 UPSTREAM → {urlparse(UPSTREAM_PROXY).hostname}"
    print(f"""
╔═══════════════════════════════════════════════════╗
║              🌐  GeoProxy v3.0                   ║
╠═══════════════════════════════════════════════════╣
║  Listen:     0.0.0.0:{str(args.port):<6}                     ║
║  Mode:       {mode:<36}║
║                                                   ║
║  ┌─ Browser Setup ─────────────────────────────┐  ║
║  │ Chrome: Settings → Proxy → localhost:{args.port} │  ║
║  │ Firefox: Settings → Network → localhost:{args.port} │  ║
║  │ Edge:   Settings → Proxy → localhost:{args.port} │  ║
║  └─────────────────────────────────────────────┘  ║
║                                                   ║
║  For geo-blocked sites, add a US proxy:           ║
║  --upstream http://user:pass@us-proxy.com:3128    ║
╚═══════════════════════════════════════════════════╝
    """)

    server = ThreadedServer(('0.0.0.0', args.port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹ Shutting down.")
        server.shutdown()


if __name__ == '__main__':
    main()
