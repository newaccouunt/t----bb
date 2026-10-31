import asyncio
import os
import re
import time
from flask import Flask, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged
import threading

app = Flask(__name__)

# ================= CONFIGURATION =================
API_ID = int(os.environ.get("API_ID", 36879151))
API_HASH = os.environ.get("API_HASH", "45360a236343352099ffa29570f48700")
SESSION_STRING = os.environ.get("SESSION_STRING", "1BVtsOI4Bu2aqYwbxzr4l6E-jImpT8xpCD9XryD71Qq8bcPVb1H1ebSanXLS_PWl1OaJXsofxC5NgstzMBVhuyn66KA6e18xf_PinLp24alIuAFu-wWHkwsdyWyH6lS1UjSmBIdOlosAsksijT32BXL912p5_FOnssgSjTGamz1oJZtrye-1vzqE9v3DPh-Rrz4uva5eF_lR7vaZxiJCGuLjDz0eBrGMVZuJaoTpm1HXcqUgs4QQPUthAkiA6etphcONP4IwiVyEtzBcYzb8MSPN7TvlQazOihV5rx_hN9uMmST58fNTpAY5mDnEZ6GBkZGPTld-fXxCjP2YjMzsfL7BB15vqoLE=")
GROUP_ID = int(os.environ.get("GROUP_ID", -1003736621633))

# PROXY CONFIG (Render Environment Variables se aayega)
# Format: socks5://user:pass@host:port ya http://host:port
PROXY_URL = os.environ.get("PROXY_URL", None)

# =================================================

client = None
telethon_loop = None

def parse_telegram_report(text):
    data = {"tg_id": None, "country": "Unknown", "country_code": "Unknown", "number": None, "response_time": "1ms"}
    lines = text.split('\n')
    for line in lines:
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
    global telethon_loop, client
    telethon_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telethon_loop)
    
    # Proxy setup (agar PROXY_URL diya hai toh)
    proxy_config = None
    if PROXY_URL:
        # python-socks format: (proxy_type, addr, port, rdns, username, password)
        # Lekin Telethon dict format bhi accept karta hai
        # Hum simple dict use karenge jise Telethon parse kar sakta hai
        from urllib.parse import urlparse
        parsed = urlparse(PROXY_URL)
        proxy_config = {
            'proxy_type': parsed.scheme, # 'socks5' ya 'http'
            'addr': parsed.hostname,
            'port': parsed.port,
            'username': parsed.username,
            'password': parsed.password,
            'rdns': True
        }
        print(f"Proxy use ho raha hai: {parsed.hostname}:{parsed.port}")

    client = TelegramClient(
        StringSession(SESSION_STRING), 
        API_ID, 
        API_HASH,
        proxy=proxy_config, # Yahan proxy pass kiya
        connection=ConnectionTcpAbridged, # Proxy ke saath better compatibility
        auto_reconnect=True
    )
    
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
    # Render par port environment variable se aata hai
    port = int(os.environ.get("PORT", 5000))
    
    t = threading.Thread(target=start_telethon, daemon=True)
    t.start()
    time.sleep(3) 
    print(f"Flask running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
