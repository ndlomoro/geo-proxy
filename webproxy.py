#!/usr/bin/env python3
"""
WebProxy — Browse any website through US-based CORS proxies.
Serves proxied pages directly (not in iframes) so JavaScript works.

Usage: python3 webproxy.py [--port 8080]
Open: http://localhost:8080
"""

import re
import time
import urllib.parse
from flask import Flask, request, Response, render_template_string, redirect, url_for
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

app = Flask(__name__)

PROXIES = [
    lambda url: f"https://api.allorigins.win/raw?url={urllib.parse.quote(url, safe='')}",
    lambda url: f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(url, safe='')}",
    lambda url: f"https://corsproxy.io/?{urllib.parse.quote(url, safe='')}",
]

LOCAL = "http://localhost:PORT"


# ── Frontend ────────────────────────────────────────────────────────────
INDEX = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WebProxy</title>
<style>
  :root { --bg: #0f0e17; --fg: #fffffe; --accent: #f00069; --muted: #a7a9be; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--fg); font-family: system-ui, sans-serif; min-height: 100vh; display: flex; flex-direction: column; }
  header { padding: 12px 20px; border-bottom: 1px solid #2a2942; display: flex; align-items: center; gap: 12px; background: #1a1930; }
  header h1 { font-size: 16px; font-weight: 700; }
  header .badge { font-size: 10px; background: var(--accent); padding: 2px 8px; border-radius: 4px; }
  .bar { display: flex; gap: 8px; padding: 10px 20px; border-bottom: 1px solid #2a2942; }
  .bar input { flex: 1; padding: 9px 14px; border-radius: 8px; border: 1px solid #2a2942; background: var(--bg); color: var(--fg); font-size: 14px; outline: none; font-family: 'SF Mono', monospace; }
  .bar input:focus { border-color: var(--accent); }
  .bar button { padding: 9px 16px; border-radius: 8px; border: none; background: var(--accent); color: var(--fg); font-weight: 600; cursor: pointer; }
  .content { flex: 1; overflow: auto; padding: 0; }
  .content iframe { width: 100%; height: 100%; border: none; }
  .status { padding: 5px 20px; font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 6px; border-top: 1px solid #2a2942; }
  .status .dot { width: 6px; height: 6px; border-radius: 50%; background: #4ade80; }
  .status.loading .dot { background: #facc15; animation: p .8s infinite; }
  @keyframes p { 0%,100%{opacity:1} 50%{opacity:.3} }
  .error { padding: 40px; text-align: center; color: var(--muted); }
  .error h2 { color: var(--accent); margin-bottom: 12px; }
</style>
</head>
<body>
<header>
  <h1>🌐 WebProxy</h1>
  <span class="badge">US RELAY</span>
  <span style="font-size:12px;color:var(--muted);margin-left:auto">Bypass geo-blocks via US proxies</span>
</header>
<div class="bar">
  <input id="url" type="text" placeholder="https://cherryroad-media.com/newspapers/" autofocus>
  <button onclick="go()">Go →</button>
</div>
<div class="status" id="status"><span class="dot"></span> Ready — type any URL</div>
<div class="content" id="content">
  <div class="error">
    <h2>Welcome to WebProxy</h2>
    <p>Type a URL above to browse through US-based proxies.</p>
    <p style="margin-top:8px;font-size:12px">All requests routed through US servers to bypass geo-restrictions.</p>
  </div>
</div>
<script>
  const input = document.getElementById('url');
  const status = document.getElementById('status');
  const content = document.getElementById('content');

  function go() {
    let url = input.value.trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
    status.innerHTML = '<span class="dot"></span> Loading...';
    status.className = 'status loading';
    content.innerHTML = '<iframe src="/fetch?url=' + encodeURIComponent(url) + '" onload="done()" onerror="fail()"></iframe>';
    input.value = url;
  }

  function done() { status.innerHTML = '<span class="dot"></span> Loaded'; status.className = 'status'; }
  function fail() { status.innerHTML = '<span class="dot"></span> Error loading page'; status.className = 'status'; }

  input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
</script>
</body>
</html>"""


@app.route('/')
def index():
    return render_template_string(INDEX)


def fetch_page(url, max_redirects=10):
    """Fetch a URL through CORS proxies with redirect following."""
    for proxy_fn in PROXIES:
        for attempt in range(max_redirects):
            proxy_url = proxy_fn(url)
            try:
                req = Request(proxy_url, headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                })
                resp = urlopen(req, timeout=25)
                ct = resp.headers.get('Content-Type', 'text/html')
                data = resp.read()
                return url, data, ct
            except HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get('Location', '')
                    if loc:
                        parsed = urllib.parse.urlparse(loc)
                        if not parsed.netloc:
                            loc = urllib.parse.urljoin(url, loc)
                        url = loc
                        continue
                break  # Try next proxy
            except Exception:
                break
    return None, b'All proxies failed.', 'text/plain'


def rewrite_html(html, target_url):
    """Rewrite URLs in HTML to route through our proxy."""
    parsed = urllib.parse.urlparse(target_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    local = LOCAL

    def fix_attr(m):
        attr = m.group(1)
        val = m.group(2) or m.group(3) or m.group(4)
        if not val or val.startswith(('javascript:', 'data:', 'mailto:', 'tel:', '#')):
            return m.group(0)
        if val.startswith('/'):
            val = base + val
        elif not val.startswith(('http://', 'https://')):
            val = base + '/' + val
        return f'{attr}="/fetch?url={urllib.parse.quote(val)}"'

    html = re.sub(
        r'(src|href|action)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
        fix_attr, html, flags=re.IGNORECASE
    )

    # Fix url() in inline styles
    def fix_css(m):
        inner = m.group(1)
        if inner.startswith(('data:',)):
            return m.group(0)
        if inner.startswith('/'):
            inner = base + inner
        elif not inner.startswith(('http://', 'https://')):
            inner = base + '/' + inner
        return f'url("/fetch?url={urllib.parse.quote(inner)}")'

    html = re.sub(r'url\(\s*["\']?([^"\')\s]+)\s*["\']?\s*\)', fix_css, html)

    # Remove X-Frame-Options and CSP headers that block iframes
    html = re.sub(r'<meta\s+http-equiv=["\']X-Frame-Options["\'][^>]*>', '', html, flags=re.IGNORECASE)

    return html


@app.route('/fetch')
def fetch():
    target_url = request.args.get('url', '')
    if not target_url:
        return 'Missing url parameter', 400

    final_url, data, ct = fetch_page(target_url)
    if final_url is None:
        return f'Failed to fetch: {target_url}', 502

    if 'text/html' in ct:
        try:
            html = data.decode('utf-8', errors='replace')
            html = rewrite_html(html, final_url)
            return Response(html, mimetype='text/html')
        except Exception:
            return Response(data, mimetype='text/html')
    else:
        return Response(data, mimetype=ct)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    global LOCAL
    LOCAL = f"http://localhost:{args.port}"
    global INDEX
    INDEX = INDEX.replace('localhost:PORT', f'localhost:{args.port}')

    print(f"""
{'='*48}
  WebProxy running on http://localhost:{args.port}
  All requests routed through US-based CORS proxies.
{'='*48}
    """)

    app.run(host='0.0.0.0', port=args.port, debug=False)


if __name__ == '__main__':
    main()
