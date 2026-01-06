import os
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "sina_secret")  # یه چیز رندوم بذار

def tg_send(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post(f"/webhook/{WEBHOOK_SECRET}")
def webhook():
    data = request.get_json(silent=True) or {}
    msg = data.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    if text == "/start":
        tg_send(chat_id, "سلام سینا 😄 من آنلاینم. فعلاً MVP هستم، بعداً با Gemini همه‌کاره میشم.")
    else:
        tg_send(chat_id, f"گرفتم داداش: {text} 😄")

    return {"ok": True}
