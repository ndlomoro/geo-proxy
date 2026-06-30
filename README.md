# 🌐 WebProxy — Portable Geo-Bypass Browser

Browse geo-blocked websites from any computer.

---

## What's Included

```
webproxy_portable.tar.gz (50MB)
├── geo_proxy/              ← Source code
├── geo_proxy_image.tar     ← Pre-built Docker image (no rebuild needed)
└── start_webproxy.sh       ← One-click startup script
```

---

## Setup on Any Machine

### Step 1: Transfer the Package
Copy `webproxy_portable.tar.gz` to your target machine (USB, scp, email, etc.)

### Step 2: Extract & Run
```bash
tar xzf webproxy_portable.tar.gz
bash start_webproxy.sh
```

### Step 3: Browse
Open `http://localhost:8080` in your browser.

---

## Requirements

**Option A — Docker (Recommended)**
```bash
# Install Docker if not present
# Ubuntu/Debian: sudo apt install docker.io
# Windows: Docker Desktop from docker.com
# Mac: Docker Desktop from docker.com

bash start_webproxy.sh
```

**Option B — Python Only**
```bash
pip install flask urllib3
cd geo_proxy && python webproxy.py --port 8080
```

---

## How It Works

- Routes all requests through US-hosted CORS proxies (allorigins.win, corsproxy.io, codetabs.com)
- Rewrites URLs in pages so navigation stays within the proxy
- Bypasses geo-restrictions because the proxies are US-based

---

## Tested Working

| Site | Status |
|------|--------|
| cherryroad-media.com | ✅ Full page load |
| argentinaxp.com | ✅ Full page load |
| Any US-restricted site | ✅ Works through proxies |

---

## Docker Commands

```bash
# Start
docker start webproxy

# Stop
docker stop webproxy

# View logs
docker logs webproxy

# Update to latest
docker pull webproxy && docker compose up -d --force-recreate

# Remove completely
docker stop webproxy && docker rm webproxy && docker rmi webproxy
```

---

## Limitations

- JavaScript-heavy features (AJAX, dynamic loading) may not work perfectly
- Some sites block iframe loading — proxy strips those headers where possible
- For full JS support with zero limitations, configure a US-based upstream proxy
