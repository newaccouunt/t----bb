import asyncio
import os
import re
import time
import random
import threading      # <-- YE LINE ADD KAREIN
import aiohttp
from flask import Flask, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from urllib.parse import urlparse

app = Flask(__name__)

API_ID = int(os.environ.get("API_ID", 36879151))
API_HASH = os.environ.get("API_HASH", "45360a236343352099ffa29570f48700")
SESSION_STRING = os.environ.get("SESSION_STRING", "1BVtsOI4Bu2aqYwbxzr4l6E-jImpT8xpCD9XryD71Qq8bcPVb1H1ebSanXLS_PWl1OaJXsofxC5NgstzMBVhuyn66KA6e18xf_PinLp24alIuAFu-wWHkwsdyWyH6lS1UjSmBIdOlosAsksijT32BXL912p5_FOnssgSjTGamz1oJZtrye-1vzqE9v3DPh-Rrz4uva5eF_lR7vaZxiJCGuLjDz0eBrGMVZuJaoTpm1HXcqUgs4QQPUthAkiA6etphcONP4IwiVyEtzBcYzb8MSPN7TvlQazOihV5rx_hN9uMmST58fNTpAY5mDnEZ6GBkZGPTld-fXxCjP2YjMzsfL7BB15vqoLE=")
GROUP_ID = int(os.environ.get("GROUP_ID", -1003736621633))

client = None
telethon_loop = None
active_proxy = None

# ========== PROXY FETCHER (PHP logic ka Python version) ==========
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
    """Proxies fetch karo aur fast test karo. Best working proxy return karo."""
    global active_proxy
    print("🔍 Fetching live proxies...")
    
    # 1. Fetch all sources parallel
    raw_proxies = set()
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in PROXY_SOURCES:
            tasks.append(fetch_source(session, url, raw_proxies))
        await asyncio.gather(*tasks, return_exceptions=True)
    
    # 2. Filter valid format
    valid = []
    for p in raw_proxies:
        p = p.strip()
        match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$', p)
        if match:
            valid.append(f"{match.group(1)}:{match.group(2)}")
    
    valid = list(set(valid))
    print(f"📦 Total unique proxies: {len(valid)}")
    
    if not valid:
        return None
    
    # 3. Fast test (parallel, 3 sec timeout)
    working = await test_proxies_batch(valid[:100])  # Top 100 test karo fast ke liye
    
    if working:
        active_proxy = random.choice(working)
        print(f"✅ Working proxy found: {active_proxy}")
        return active_proxy
    else:
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
            
            # HTML se IP:Port extract karo
            if '<html' in text.lower() or '<body' in text.lower():
                matches = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b', text)
                for m in matches:
                    result_set.add(m)
            else:
                # Plain text ya JSON
                if text.strip().startswith('{') or text.strip().startswith('['):
                    import json
                    try:
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
    """Proxy list ko fast test karo (parallel)."""
    async with aiohttp.ClientSession() as session:
        tasks = [test_single_proxy(session, p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return [p for p, ok in zip(proxies, results) if ok is True]

async def test_single_proxy(session, proxy):
    """Ek proxy ko test karo (Telegram ke liye)."""
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        connector = aiohttp.TCPConnector(ssl=False)
        proxy_url = f"http://{proxy}"
        async with session.get(
            "https://api.telegram.org",
            proxy=proxy_url,
            timeout=timeout,
            connector=connector
        ) as resp:
            return resp.status < 500  # 4xx bhi chalega (proxy working hai)
    except:
        return False

# ========== TELETHON SETUP ==========
def build_proxy_connector(proxy_str):
    """Telethon ke liye custom connector banayo (python-socks use karke)."""
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

def start_telethon():
    global telethon_loop, client, active_proxy
    telethon_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telethon_loop)
    
    # 1. Proxies fetch aur test karo
    proxy_str = telethon_loop.run_until_complete(fetch_and_test_proxies())
    
    # 2. Client banao (proxy ke saath ya bina)
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
        if not telethon_loop.run_until_complete(client.is_user_authorized()):
             print("ERROR: Session invalid hai!")
             return
        print("Telethon Connected to Group!")
        telethon_loop.run_forever()
    except Exception as e:
        print(f"Telethon Start Error: {e}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    t = threading.Thread(target=start_telethon, daemon=True)
    t.start()
    time.sleep(5) 
    print(f"Flask running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
