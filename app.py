import asyncio
import os
import re
import time
import random
import threading
import aiohttp
from flask import Flask, jsonify, render_template_string
from telethon import TelegramClient, events
from telethon.sessions import StringSession

app = Flask(__name__)

API_ID = int(os.environ.get("API_ID", 36879151))
API_HASH = os.environ.get("API_HASH", "45360a236343352099ffa29570f48700")
SESSION_STRING = os.environ.get("SESSION_STRING", "1BVtsOMABu03AGNyJxnpBCoVUDhVEhRLQ7S57ksSt4hAxYye7fEvkOmz24B-DGoeeZIgopZwAxZfdUjAihkSNhkX2xxPg46DYirOYShfvsUMwM02Y4p3Et8T-kiAcjFyKawUjS4EneH3DC_IwESVfyNH6N9emt2qZ1lhvbAx3zlyEIfU0ZiodJcHClK9PjwwfHX0kp3JFt11R4-a_LyYP7UCB9ExezKt9YTAwFvuc4ZEUitRnyGtKND5nKB1UQcZtaNSrz5g0lf1Pzw08LNi1XEShktawnd9gPJRILMDCr-mfIfLtFLw0fSGO-rrqtwh2HplhqiJtPIyhvwpYryYvE430wA-oUjg=")
GROUP_ID = int(os.environ.get("GROUP_ID", -1002423502338))

client = None
telethon_loop = None
active_proxy = None

# ========== RUNTIME STATUS (dashboard ke liye) ==========
START_TIME = time.time()
STATUS = {
    "telethon_connected": False,
    "telethon_authorized": False,
    "proxy_active": None,
    "proxy_count": 0,
    "last_error": None,
    "bot_username": "N/A",
    "group_id": GROUP_ID,
}
# =======================================================

# ========== PROXY FETCHER ==========
PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000",
    "https://api.proxyscrape.com/?request=displayproxies&proxytype=http",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "http://pubproxy.com/api/proxy?limit=50&format=txt&http=true",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/opsxcq/proxy-list/master/list.txt",
    "https://raw.githubusercontent.com/almighty-uncle/Proxy-List/main/HTTP.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://free-proxy-list.net/",
    "https://www.sslproxies.org/",
    "https://www.us-proxy.org/",
    "https://www.socks-proxy.net/",
]

async def fetch_and_test_proxies():
    global active_proxy
    print("🔍 Fetching live proxies...")
    raw_proxies = set()
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_source(session, url, raw_proxies) for url in PROXY_SOURCES]
        await asyncio.gather(*tasks, return_exceptions=True)

    valid = []
    for p in raw_proxies:
        p = p.strip()
        match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$', p)
        if match:
            valid.append(f"{match.group(1)}:{match.group(2)}")
    valid = list(set(valid))
    STATUS["proxy_count"] = len(valid)
    print(f"📦 Total unique proxies: {len(valid)}")

    if not valid:
        return None

    working = await test_proxies_batch(valid[:100])
    if working:
        active_proxy = random.choice(working)
        STATUS["proxy_active"] = active_proxy
        print(f"✅ Working proxy found: {active_proxy}")
        return active_proxy
    print("❌ No working proxy found")
    return None

async def fetch_source(session, url, result_set):
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with session.get(url, timeout=timeout, ssl=False,
                               headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status != 200:
                return
            text = await resp.text(errors='ignore')
            if '<html' in text.lower() or '<body' in text.lower():
                matches = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b', text)
                for m in matches:
                    result_set.add(m)
            else:
                if text.strip().startswith('{') or text.strip().startswith('['):
                    try:
                        import json
                        data = json.loads(text)
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, str):
                                    result_set.add(item)
                        elif isinstance(data, dict):
                            for key in ['proxy', 'proxies', 'data']:
                                if key in data:
                                    if isinstance(data[key], str):
                                        result_set.add(data[key])
                                    elif isinstance(data[key], list):
                                        for x in data[key]:
                                            if isinstance(x, str):
                                                result_set.add(x)
                    except:
                        pass
                else:
                    for line in text.split('\n'):
                        line = line.strip()
                        if ':' in line:
                            result_set.add(line)
    except:
        pass

async def test_proxies_batch(proxies):
    async with aiohttp.ClientSession() as session:
        tasks = [test_single_proxy(session, p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return [p for p, ok in zip(proxies, results) if ok is True]

async def test_single_proxy(session, proxy):
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        connector = aiohttp.TCPConnector(ssl=False)
        async with session.get(
            "https://api.telegram.org",
            proxy=f"http://{proxy}",
            timeout=timeout,
            connector=connector
        ) as resp:
            return resp.status < 500
    except:
        return False

# ========== TELETHON ==========
def build_proxy_connector(proxy_str):
    from python_socks.async_.asyncio import Proxy
    from functools import partial

    async def connector(ip, port, *, proxy_host, proxy_port):
        proxy = Proxy.from_url(f"http://{proxy_host}:{proxy_port}")
        sock = await proxy.connect(dest_host=ip, dest_port=port)
        return await asyncio.open_connection(sock=sock)

    host, port = proxy_str.split(':')
    return partial(connector, proxy_host=host, proxy_port=int(port))

async def fetch_data(target):
    try:
        if not client.is_connected():
            await client.connect()

        user_id = target
        if target.startswith('@') or not target.lstrip('-').isdigit():
            try:
                entity = await client.get_entity(target)
                user_id = str(entity.id)
            except Exception:
                return {"error": f"'{target}' ka ID nahi mila."}

        await client.send_message(GROUP_ID, user_id)
        result_text = None
        start_time = time.time()

        @client.on(events.NewMessage(chats=GROUP_ID))
        async def handler(event):
            nonlocal result_text
            if result_text: return
            text = event.message.text or ""
            if "ᴘʜᴏɴᴇ ɴᴜᴍʙᴇr" in text or "ᴛᴇʟᴇɢraᴍ ɪᴅ" in text or "TG2NUM" in text:
                result_text = text

        while time.time() - start_time < 20:
            if result_text: break
            await asyncio.sleep(1)

        client.remove_event_handler(handler)
        if result_text:
            return parse_telegram_report(result_text)
        else:
            return {"error": "Group mein result nahi aaya (Timeout)."}
    except Exception as e:
        return {"error": str(e)}

def parse_telegram_report(text):
    data = {"tg_id": None, "country": "Unknown", "country_code": "Unknown", "number": None, "response_time": "1ms"}
    for line in text.split('\n'):
        if "ɪᴅ" in line or "ID" in line:
            nums = re.findall(r'\d+', line)
            if nums: data["tg_id"] = nums[0]
        if "+" in line:
            match = re.search(r'(\+\d+)\s*(\d+)', line)
            if match:
                data["country_code"] = match.group(1)
                data["number"] = match.group(2)
        if "sᴛaᴛᴇ" in line or "STATE" in line:
            if ":" in line:
                country_text = line.split(":")[-1].strip().replace('`', '').strip()
                if country_text: data["country"] = country_text
    return data

# ========== DASHBOARD HTML ==========
DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TG Info API — Status</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0;min-height:100vh;padding:24px;display:flex;align-items:center;justify-content:center}
.container{max-width:800px;width:100%}
header{text-align:center;margin-bottom:32px}
header h1{font-size:2.2rem;background:linear-gradient(90deg,#38bdf8,#818cf8,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}
header p{color:#94a3b8;font-size:0.95rem}
.status-badge{display:inline-flex;align-items:center;gap:8px;margin-top:16px;padding:10px 22px;border-radius:30px;font-weight:600;font-size:0.9rem;transition:all .3s}
.online{background:rgba(16,185,129,0.12);color:#6ee7b7;border:1px solid #10b981}
.offline{background:rgba(239,68,68,0.12);color:#fca5a5;border:1px solid #ef4444}
.dot{width:10px;height:10px;border-radius:50%;animation:pulse 1.8s infinite}
.online .dot{background:#10b981;box-shadow:0 0 10px #10b981}
.offline .dot{background:#ef4444}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:24px}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:18px;transition:all .3s}
.card:hover{border-color:#38bdf8;transform:translateY(-2px)}
.card .label{font-size:0.75rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px}
.card .value{font-size:1.1rem;font-weight:600;color:#f1f5f9;word-break:break-all}
.value.good{color:#6ee7b7}
.value.bad{color:#fca5a5}
.value.warn{color:#fbbf24}
.api-box{background:#0f172a;border:1px solid #334155;border-radius:12px;padding:20px;margin-top:8px}
.api-box h3{color:#38bdf8;font-size:1rem;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.api-box code{display:block;background:#1e293b;padding:10px 14px;border-radius:8px;font-family:'Courier New',monospace;font-size:0.85rem;color:#6ee7b7;margin-bottom:8px;overflow-x:auto}
.api-box p{color:#94a3b8;font-size:0.8rem;margin-top:8px}
footer{text-align:center;margin-top:28px;color:#64748b;font-size:0.8rem}
.refresh{margin-top:20px;text-align:center}
.refresh button{background:linear-gradient(90deg,#38bdf8,#818cf8);border:none;color:white;padding:10px 24px;border-radius:8px;font-weight:600;cursor:pointer;font-size:0.9rem;transition:all .3s}
.refresh button:hover{transform:scale(1.05);box-shadow:0 4px 20px rgba(56,189,248,0.4)}
</style>
</head>
<body>
<div class="container">
<header>
<h1>⚡ TG Info API</h1>
<p>Telegram OSINT Lookup Server</p>
<div id="statusBadge" class="status-badge online">
<span class="dot"></span>
<span id="statusText">Checking...</span>
</div>
</header>

<div class="grid">
<div class="card">
<div class="label">Server Uptime</div>
<div class="value good" id="uptime">—</div>
</div>
<div class="card">
<div class="label">Telethon Client</div>
<div class="value" id="telethon">—</div>
</div>
<div class="card">
<div class="label">Session Auth</div>
<div class="value" id="auth">—</div>
</div>
<div class="card">
<div class="label">Active Proxy</div>
<div class="value" id="proxy">—</div>
</div>
<div class="card">
<div class="label">Proxies Found</div>
<div class="value" id="proxyCount">—</div>
</div>
<div class="card">
<div class="label">Target Group</div>
<div class="value" id="group">—</div>
</div>
</div>

<div class="api-box">
<h3>🔗 API Endpoint</h3>
<code id="apiUrl">/tg=&lt;username&gt;</code>
<p>Example: <code style="display:inline;background:none;padding:0;color:#c084fc" id="apiExample"></code></p>
</div>

<div class="refresh">
<button onclick="loadStatus()">🔄 Refresh Status</button>
</div>

<footer>Powered by Flask + Telethon • Auto-refresh every 30s</footer>
</div>

<script>
function fmtUptime(s){const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=Math.floor(s%60);return (h?h+'h ':'')+(m?m+'m ':'')+sec+'s'}

async function loadStatus(){
  try{
    const r = await fetch('/health');
    const d = await r.json();
    
    // Status badge
    const badge = document.getElementById('statusBadge');
    const st = document.getElementById('statusText');
    const isUp = d.telethon_connected && d.telethon_authorized;
    badge.className = 'status-badge ' + (isUp ? 'online' : 'offline');
    st.textContent = isUp ? 'Server Online · Health Good' : 'Server Degraded';
    
    // Cards
    document.getElementById('uptime').textContent = fmtUptime(d.uptime_seconds);
    
    const tel = document.getElementById('telethon');
    tel.textContent = d.telethon_connected ? 'Connected ✓' : 'Disconnected ✗';
    tel.className = 'value ' + (d.telethon_connected ? 'good' : 'bad');
    
    const auth = document.getElementById('auth');
    auth.textContent = d.telethon_authorized ? 'Valid ✓' : 'Invalid ✗';
    auth.className = 'value ' + (d.telethon_authorized ? 'good' : 'bad');
    
    const px = document.getElementById('proxy');
    px.textContent = d.proxy_active || 'Direct (no proxy)';
    px.className = 'value ' + (d.proxy_active ? 'good' : 'warn');
    
    document.getElementById('proxyCount').textContent = d.proxy_count || '0';
    document.getElementById('group').textContent = d.group_id;
    
    // API examples
    const base = window.location.origin;
    document.getElementById('apiUrl').textContent = base + '/tg=<username>';
    document.getElementById('apiExample').textContent = base + '/tg=@your_username';
  }catch(e){
    document.getElementById('statusText').textContent = 'Server Offline';
    document.getElementById('statusBadge').className = 'status-badge offline';
  }
}
loadStatus();
setInterval(loadStatus, 30000);
</script>
</body>
</html>
"""

# ========== FLASK ROUTES ==========
@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD)

@app.route('/health')
def health():
    uptime = time.time() - START_TIME
    is_healthy = STATUS["telethon_connected"] and STATUS["telethon_authorized"]
    return jsonify({
        "status": "ok" if is_healthy else "degraded",
        "server_online": True,
        "health": "good" if is_healthy else "partial",
        "uptime_seconds": int(uptime),
        "uptime_human": f"{int(uptime//3600)}h {int((uptime%3600)//60)}m {int(uptime%60)}s",
        "telethon_connected": STATUS["telethon_connected"],
        "telethon_authorized": STATUS["telethon_authorized"],
        "proxy_active": STATUS["proxy_active"],
        "proxy_count": STATUS["proxy_count"],
        "group_id": STATUS["group_id"],
        "last_error": STATUS["last_error"],
        "timestamp": int(time.time()),
    })

@app.route('/tg=<path:target>')
def telegram_lookup(target):
    if not target:
        return jsonify({"error": "No username or ID provided"}), 400
    if not target.startswith('@') and not target.isdigit():
        target = '@' + target
    if telethon_loop is None or not telethon_loop.is_running():
        return jsonify({"error": "Telethon client not ready"}), 500

    future = asyncio.run_coroutine_threadsafe(fetch_data(target), telethon_loop)
    try:
        result_data = future.result(timeout=30)
    except TimeoutError:
        return jsonify({"error": "Request Timeout."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if "error" in result_data:
        return jsonify(result_data), 400
    return jsonify(result_data)

# ========== TELETHON BOOT ==========
def start_telethon():
    global telethon_loop, client, active_proxy
    telethon_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telethon_loop)

    proxy_str = telethon_loop.run_until_complete(fetch_and_test_proxies())

    if proxy_str:
        connector = build_proxy_connector(proxy_str)
        client = TelegramClient(
            StringSession(SESSION_STRING), API_ID, API_HASH,
            connector=connector,
            auto_reconnect=True
        )
        print(f"🔗 Using proxy: {proxy_str}")
    else:
        print("⚠️ No proxy found, connecting directly...")
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    try:
        print("Telethon connect ho raha hai...")
        telethon_loop.run_until_complete(client.connect())
        STATUS["telethon_connected"] = client.is_connected()
        if not telethon_loop.run_until_complete(client.is_user_authorized()):
            print("ERROR: Session invalid hai!")
            STATUS["last_error"] = "Session invalid"
            return
        STATUS["telethon_authorized"] = True
        print("✅ Telethon Connected to Group!")
        telethon_loop.run_forever()
    except Exception as e:
        print(f"Telethon Start Error: {e}")
        STATUS["last_error"] = str(e)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    t = threading.Thread(target=start_telethon, daemon=True)
    t.start()
    time.sleep(5)
    print(f"🚀 Flask running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
