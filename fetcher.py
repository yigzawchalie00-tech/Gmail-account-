import os
import asyncio
import re
import threading
import httpx
import asyncpg
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

load_dotenv()

API_ID = int(os.environ.get("TG_API_ID", "39016712"))
API_HASH = os.environ.get("TG_API_HASH", "da2ca78041e980a04f557cb65945e5fb")
SESSION_STRING = os.environ.get("TG_SESSION_STRING", "1BJWap1sBu1AyQDgrKIrZXSIJ8jz0iRGuKDy_BoKPEVfqfVe0yHkBBzmMSCRGBLCeLCCcn85VVHxeBQGxcorrkO5QwMuezSC9GCS0D4l98hvRsLjrbmv8EIqtHPcvi3zBSXm5SSB5fWM7aq_rh_MLFcxutOcJWfDejs9SCU1EIFXr4W_Zg5Dj5OON-wDSbqepvTnK3rXh3lXHz3fAYtwQvSTZH1OFGaFQ1yh72jrfAjpmRciqVodfh1SDguQz7g72Qi742PxUHugRigZdNNUEZKePfkvnc6N9Xpn__tAoEiM4io1Y")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:npg_d7L4xXgESPUR@ep-frosty-water-axd2u33z-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8663005620:AAFK83aPI-bEyRot7dsTlHfmnwLWiXRyhNs")
ADMIN_ID = 8079356870
GMAIL_FARMER_BOT = "GmailFarmerBot"
FETCH_INTERVAL = 330  # 5.5 minutes

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
pending_task = False


def parse_task(text: str):
    try:
        first_name = re.search(r"First name[:\s]+(\S+)", text)
        last_name = re.search(r"Last name[:\s]+(\S+)", text)
        email = re.search(r"Email[:\s]+([\w.]+@gmail\.com)", text)
        password = re.search(r"Password[:\s]+(\S+)", text)
        birth_year = re.search(r"Year of birth[:\s]+(\d{4})", text)

        if not email:
            return None

        return {
            "first_name": first_name.group(1) if first_name else "",
            "last_name": last_name.group(1) if last_name else "",
            "email": email.group(1),
            "password": password.group(1) if password else "",
            "birth_year": birth_year.group(1) if birth_year else "",
        }
    except Exception as e:
        print(f"[parse] Error: {e}")
        return None


async def save_task(task: dict):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        result = await conn.execute("""
            INSERT INTO tasks (first_name, last_name, email, password, birth_year)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (email) DO NOTHING
        """, task["first_name"], task["last_name"], task["email"],
            task["password"], task["birth_year"])
        await conn.close()

        if result == "INSERT 0 1":
            print(f"[fetcher] ✅ Task saved: {task['email']}")
            async with httpx.AsyncClient() as http:
                await http.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": ADMIN_ID,
                        "text": f"📥 New task added to pool!\n📧 {task['email']}"
                    }
                )
        else:
            print(f"[fetcher] ⚠️ Duplicate email skipped: {task['email']}")

    except Exception as e:
        print(f"[fetcher] ❌ DB error: {e}")


@client.on(events.NewMessage(incoming=True, from_users=GMAIL_FARMER_BOT))
async def on_farmer_response(event):
    global pending_task
    text = event.raw_text or ""
    print(f"[fetcher] Received: {text[:80]}")

    if pending_task and "gmail.com" in text.lower():
        task = parse_task(text)
        if task:
            await save_task(task)
        else:
            print("[fetcher] Could not parse task.")
        pending_task = False


async def fetch_loop():
    global pending_task
    print("[fetcher] Started. Fetching every 5.5 minutes.")

    farmer = await client.get_entity(GMAIL_FARMER_BOT)

    while True:
        try:
            print("[fetcher] Requesting new task from source...")
            pending_task = True
            await client.send_message(farmer, "➕ Register a new Gmail")
            await asyncio.sleep(FETCH_INTERVAL)
        except Exception as e:
            print(f"[fetcher] Error: {e}")
            pending_task = False
            await asyncio.sleep(60)


# --- Keep-alive ping server ---

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - fetcher running")

    def log_message(self, format, *args):
        pass


def start_ping_server():
    port = int(os.environ.get("PORT", 10001))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    print(f"[keep-alive] Ping server on port {port}")
    server.serve_forever()


async def main():
    threading.Thread(target=start_ping_server, daemon=True).start()
    await client.start()
    print("[fetcher] Logged in.")
    await fetch_loop()


if __name__ == "__main__":
    asyncio.run(main())
