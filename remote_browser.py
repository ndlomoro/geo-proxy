#!/usr/bin/env python3
"""
RemoteBrowser — Full browser control via WebSocket + screenshot streaming.
Uses Playwright (headless Chromium) + Flask + websockets.
User sees a live viewport in their browser and can click, type, scroll, navigate.
"""

import asyncio
import base64
import io
import json
import logging
import time
from pathlib import Path

from flask import Flask, render_template_string, Response
from flask_sock import Sock
from PIL import Image
from playwright.async_api import async_playwright

# ── Config ──────────────────────────────────────────────────────────────
PORT = 8080
VIEWPORT_W, VIEWPORT_H = 1280, 900
SCROLL_STEP = 200
MAX_QUALITY = 75          # JPEG quality for streaming
WS_HEARTBEAT = 5          # seconds between idle screenshots

# ── Flask app ───────────────────────────────────────────────────────────
app = Flask(__name__)
sock = Sock(app)

# Global state
browser_page = None
ws_clients = set()
page_lock = asyncio.Lock()

# ── Frontend HTML ───────────────────────────────────────────────────────
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RemoteBrowser</title>
<style>
  :root { --bg: #0f0e17; --fg: #fffffe; --accent: #f00069; --muted: #a7a9be; --card: #1a1930; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--fg); font-family: 'Inter', system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  header { padding: 10px 16px; border-bottom: 1px solid #2a2942; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
  header h1 { font-size: 15px; font-weight: 700; white-space: nowrap; }
  .urlbar { flex: 1; display: flex; gap: 6px; }
  .urlbar input { flex: 1; padding: 7px 12px; border-radius: 6px; border: 1px solid #2a2942; background: var(--bg); color: var(--fg); font-size: 13px; outline: none; font-family: 'SF Mono', monospace; }
  .urlbar input:focus { border-color: var(--accent); }
  .urlbar button { padding: 7px 14px; border-radius: 6px; border: none; background: var(--accent); color: var(--fg); font-weight: 600; cursor: pointer; font-size: 12px; white-space: nowrap; }
  .toolbar { display: flex; gap: 4px; }
  .toolbar button { padding: 6px 10px; border-radius: 6px; border: 1px solid #2a2942; background: var(--card); color: var(--muted); cursor: pointer; font-size: 12px; }
  .toolbar button:hover { border-color: var(--accent); color: var(--fg); }

  .viewport-wrap { flex: 1; display: flex; justify-content: center; align-items: flex-start; overflow: auto; padding: 12px; background: #000; }
  #viewport { cursor: crosshair; max-width: 100%; height: auto; display: block; border-radius: 4px; }

  .status { padding: 4px 16px; font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 6px; flex-shrink: 0; border-top: 1px solid #2a2942; }
  .status .dot { width: 6px; height: 6px; border-radius: 50%; background: #4ade80; }
  .status.connecting .dot { background: #facc15; }
  .status.error .dot { background: var(--accent); }

  .help { padding: 4px 16px; font-size: 10px; color: var(--muted); text-align: center; opacity: 0.6; flex-shrink: 0; }
</style>
</head>
<body>
<header>
  <h1>🖥️ RemoteBrowser</h1>
  <div class="urlbar">
    <input id="url" type="text" placeholder="https://example.com" value="about:blank">
    <button onclick="navigate()">Go →</button>
  </div>
  <div class="toolbar">
    <button onclick="sendCmd('back')" title="Back">◀</button>
    <button onclick="sendCmd('forward')" title="Forward">▶</button>
    <button onclick="sendCmd('reload')" title="Reload">⟳</button>
    <button onclick="sendCmd('scroll_up')" title="Scroll Up">▲</button>
    <button onclick="sendCmd('scroll_down')" title="Scroll Down">▼</button>
  </div>
</header>
<div class="viewport-wrap">
  <img id="viewport" alt="browser viewport">
</div>
<div class="status" id="status"><span class="dot"></span> Connecting...</div>
<div class="help">Click to navigate &middot; Right-click for context &middot; Type in URL bar &middot; Arrow keys to scroll &middot; Ctrl+Enter to navigate</div>

<script>
const ws = new WebSocket('ws://' + location.host + '/ws');
const viewport = document.getElementById('viewport');
const urlInput = document.getElementById('url');
const status = document.getElementById('status');

ws.onopen = () => { status.innerHTML = '<span class="dot"></span> Connected'; status.className = 'status'; };
ws.onclose = () => { status.innerHTML = '<span class="dot"></span> Disconnected — reconnecting...'; status.className = 'status connecting'; setTimeout(() => location.reload(), 3000); };
ws.onerror = () => { status.innerHTML = '<span class="dot"></span> Error'; status.className = 'status error'; };

ws.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.type === 'screenshot') {
    viewport.src = 'data:image/jpeg;base64,' + data.data;
  } else if (data.type === 'url') {
    urlInput.value = data.url;
  } else if (data.type === 'title') {
    document.title = data.title + ' — RemoteBrowser';
  } else if (data.type === 'focus') {
    // Selector clicked needs text input
    if (data.selector) {
      const text = prompt('Enter text for selected field:');
      if (text !== null) sendCmd('type', { text, selector: data.selector });
    }
  }
};

function sendCmd(type, payload = {}) {
  ws.send(JSON.stringify({ type, ...payload }));
}

function navigate() {
  let url = urlInput.value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  urlInput.value = url;
  sendCmd('navigate', { url });
}

urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') navigate();
  if (e.key === 'ArrowUp') { e.preventDefault(); sendCmd('scroll_up'); }
  if (e.key === 'ArrowDown') { e.preventDefault(); sendCmd('scroll_down'); }
});

// Click handling on viewport
viewport.addEventListener('click', (e) => {
  const rect = viewport.getBoundingClientRect();
  const img = viewport.naturalWidth;
  const x = ((e.clientX - rect.left) / rect.width) * VIEWPORT_W;
  const y = ((e.clientY - rect.top) / rect.height) * VIEWPORT_H;
  sendCmd('click', { x: Math.round(x), y: Math.round(y) });
});

viewport.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  const rect = viewport.getBoundingClientRect();
  const x = ((e.clientX - rect.left) / rect.width) * VIEWPORT_W;
  const y = ((e.clientY - rect.top) / rect.height) * VIEWPORT_H;
  sendCmd('right_click', { x: Math.round(x), y: Math.round(y) });
});

// Mouse wheel scrolling
viewport.addEventListener('wheel', (e) => {
  e.preventDefault();
  sendCmd(e.deltaY > 0 ? 'scroll_down' : 'scroll_up');
}, { passive: false });

const VIEWPORT_W = 1280, VIEWPORT_H = 900;
</script>
</body>
</html>"""


@app.route('/')
def index():
    return render_template_string(INDEX_HTML)


# ── Screenshot helper ───────────────────────────────────────────────────
async def capture_and_broadcast():
    """Take a screenshot and broadcast to all connected clients."""
    global browser_page
    if browser_page is None:
        return
    try:
        screenshot = await browser_page.screenshot(type='jpeg', quality=MAX_QUALITY)
        b64 = base64.b64encode(screenshot).decode('ascii')
        msg = json.dumps({'type': 'screenshot', 'data': b64})
        disconnected = set()
        for client in ws_clients:
            try:
                await client.send(msg)
            except Exception:
                disconnected.add(client)
        ws_clients -= disconnected
    except Exception as e:
        logging.warning(f"Screenshot error: {e}")


async def send_url_update():
    if browser_page and ws_clients:
        try:
            url = browser_page.url
            title = await browser_page.title()
            for client in list(ws_clients):
                try:
                    await client.send(json.dumps({'type': 'url', 'url': url}))
                    await client.send(json.dumps({'type': 'title', 'title': title}))
                except Exception:
                    ws_clients.discard(client)
        except Exception:
            pass


# ── WebSocket handler ───────────────────────────────────────────────────
@sock.route('/ws')
def ws_handler(ws):
    """Async WebSocket handler — runs in event loop."""
    async def handler():
        ws_clients.add(ws)
        try:
            async for msg in ws:
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                cmd = data.get('type', '')
                async with page_lock:
                    if cmd == 'navigate' and browser_page:
                        url = data.get('url', '')
                        if url:
                            try:
                                await browser_page.goto(url, wait_until='domcontentloaded', timeout=30000)
                                await capture_and_broadcast()
                                await send_url_update()
                            except Exception as e:
                                logging.warning(f"Navigation error: {e}")
                                await capture_and_broadcast()

                    elif cmd == 'click' and browser_page:
                        x, y = data.get('x', 0), data.get('y', 0)
                        try:
                            await browser_page.mouse.click(x, y)
                            await asyncio.sleep(0.3)
                            await capture_and_broadcast()
                            await send_url_update()
                        except Exception as e:
                            logging.warning(f"Click error: {e}")

                    elif cmd == 'right_click' and browser_page:
                        x, y = data.get('x', 0), data.get('y', 0)
                        try:
                            await browser_page.mouse.click(x, y, button='right')
                            await asyncio.sleep(0.3)
                            await capture_and_broadcast()
                        except Exception:
                            pass

                    elif cmd == 'scroll_up' and browser_page:
                        try:
                            await browser_page.mouse.wheel(0, -SCROLL_STEP)
                            await asyncio.sleep(0.1)
                            await capture_and_broadcast()
                        except Exception:
                            pass

                    elif cmd == 'scroll_down' and browser_page:
                        try:
                            await browser_page.mouse.wheel(0, SCROLL_STEP)
                            await asyncio.sleep(0.1)
                            await capture_and_broadcast()
                        except Exception:
                            pass

                    elif cmd == 'type' and browser_page:
                        text = data.get('text', '')
                        try:
                            await browser_page.keyboard.insert_text(text)
                            await asyncio.sleep(0.2)
                            await capture_and_broadcast()
                        except Exception:
                            pass

                    elif cmd == 'back' and browser_page:
                        try:
                            await browser_page.go_back(timeout=5000)
                            await capture_and_broadcast()
                            await send_url_update()
                        except Exception:
                            pass

                    elif cmd == 'forward' and browser_page:
                        try:
                            await browser_page.go_forward(timeout=5000)
                            await capture_and_broadcast()
                            await send_url_update()
                        except Exception:
                            pass

                    elif cmd == 'reload' and browser_page:
                        try:
                            await browser_page.reload(wait_until='domcontentloaded', timeout=30000)
                            await capture_and_broadcast()
                            await send_url_update()
                        except Exception:
                            pass

                    elif cmd == 'key' and browser_page:
                        key = data.get('key', '')
                        try:
                            await browser_page.keyboard.press(key)
                            await asyncio.sleep(0.1)
                            await capture_and_broadcast()
                        except Exception:
                            pass

        except Exception:
            pass
        finally:
            ws_clients.discard(ws)

    return handler()


# ── Playwright startup ──────────────────────────────────────────────────
async def init_browser():
    global browser_page
    logging.info("Launching Playwright browser...")
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=[
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
    ])
    context = await browser.new_context(
        viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        ignore_https_errors=True,
        java_script_enabled=True,
    )
    browser_page = await context.new_page()

    # Auto-screenshot on navigation
    async def on_load():
        await capture_and_broadcast()
        await send_url_update()

    browser_page.on('load', on_load)
    browser_page.on('framenavigated', on_load)

    logging.info("Browser ready.")
    return pw, browser


# ── Idle screenshot loop ────────────────────────────────────────────────
async def idle_screenshot_loop():
    """Periodically send screenshots when there are clients but no commands."""
    while True:
        await asyncio.sleep(WS_HEARTBEAT)
        if ws_clients:
            await capture_and_broadcast()


# ── Main ────────────────────────────────────────────────────────────────
def main():
    import threading

    # Start event loop in background thread
    loop = asyncio.new_event_loop()

    async def setup():
        pw, browser = await init_browser()
        # Start idle screenshot loop
        asyncio.create_task(idle_screenshot_loop())
        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await browser.close()
            await pw.stop()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(setup())

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()

    # Wait for browser to initialize
    time.sleep(3)

    print(f"\n{'='*50}")
    print(f"  RemoteBrowser running")
    print(f"  Open: http://localhost:{PORT}")
    print(f"{'='*50}\n")

    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)


if __name__ == '__main__':
    main()
