import os
import time
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

if not BOT_TOKEN or not WEBHOOK_SECRET:
    raise RuntimeError("BOT_TOKEN or WEBHOOK_SECRET is missing")

# Timezone (تو گفتی Europe/Paris هم اوکیه. اگر خواستی بعداً Kabul کنیم)
TZ = ZoneInfo("Europe/Paris")

DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite3")

REMINDERS = [
    ("r3_sent", timedelta(hours=3),  "⏳ داداش ۳ ساعت دیگه وقتشه: {title}"),
    ("r1_sent", timedelta(hours=1),  "⚠️ مشتی ۱ ساعت دیگه می‌رسه: {title}"),
    ("r5_sent", timedelta(minutes=5), "🚨 ۵ دقیقه مونده‌ها! آماده شو: {title}"),
    ("due_sent", timedelta(seconds=0), "⏰ وقتشه داداش! الان بزن بریم: {title}"),
]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            title TEXT NOT NULL,
            due_ts INTEGER NOT NULL,
            created_ts INTEGER NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            r3_sent INTEGER NOT NULL DEFAULT 0,
            r1_sent INTEGER NOT NULL DEFAULT 0,
            r5_sent INTEGER NOT NULL DEFAULT 0,
            due_sent INTEGER NOT NULL DEFAULT 0
        )
    """)
    return conn

def tg_send(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)

def parse_due(text: str) -> datetime:
    """
    Supported:
      - HH:MM  (today)
      - YYYY-MM-DD HH:MM
    """
    text = text.strip()
    now = datetime.now(TZ)

    # YYYY-MM-DD HH:MM
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return dt
    except ValueError:
        pass

    # HH:MM (today)
    try:
        t = datetime.strptime(text, "%H:%M")
        dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        return dt
    except ValueError:
        raise ValueError("Bad time format")

@app.get("/")
def root():
    return {"status": "ok"}

@app.post(f"/webhook/{WEBHOOK_SECRET}")
def webhook():
    data = request.get_json(silent=True) or {}
    msg = data.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    # /start
    if text == "/start":
        tg_send(
            str(chat_id),
            "سلام سینا 😄\n"
            "من آنلاینم مشتی ✅\n\n"
            "دستورها:\n"
            "/add عنوان | ساعت\n"
            "مثال: /add باشگاه | 21:30\n"
            "یا با تاریخ: /add جلسه | 2026-01-07 14:00\n\n"
            "/list\n"
            "/done ID"
        )
        return {"ok": True}

    # /add
    if text.startswith("/add"):
        try:
            payload = text.replace("/add", "", 1).strip()
            title, when = [x.strip() for x in payload.split("|", 1)]
            due_dt = parse_due(when)

            now_ts = int(time.time())
            due_ts = int(due_dt.timestamp())

            # اگر ساعتِ امروز گذشته بود، خودکار بنداز فردا
            if len(when) == 5 and due_ts < now_ts:
                due_dt = due_dt + timedelta(days=1)
                due_ts = int(due_dt.timestamp())

            conn = db()
            conn.execute(
                "INSERT INTO tasks(chat_id,title,due_ts,created_ts,done) VALUES(?,?,?,?,0)",
                (str(chat_id), title, due_ts, now_ts)
            )
            conn.commit()
            task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()

            pretty = due_dt.strftime("%Y-%m-%d %H:%M")
            tg_send(str(chat_id), f"ثبت شد مشتی ✅ (ID={task_id})\n⏱ زمان: {pretty}")
        except Exception:
            tg_send(
                str(chat_id),
                "داداش فرمتش اینه 😄\n"
                "/add عنوان | ساعت\n"
                "مثال: /add باشگاه | 21:30\n"
                "یا: /add جلسه | 2026-01-07 14:00"
            )
        return {"ok": True}

    # /list
    if text == "/list":
        conn = db()
        rows = conn.execute(
            "SELECT id, title, due_ts, done FROM tasks WHERE chat_id=? ORDER BY done ASC, due_ts ASC LIMIT 30",
            (str(chat_id),)
        ).fetchall()
        conn.close()

        if not rows:
            tg_send(str(chat_id), "هیچی ثبت نکردی هنوز داداش 😄 /add بزن")
            return {"ok": True}

        lines = []
        for task_id, title, due_ts, done in rows:
            dt = datetime.fromtimestamp(int(due_ts), TZ).strftime("%Y-%m-%d %H:%M")
            status = "✅" if done else "🕒"
            lines.append(f"{status} ID={task_id} — {title} — {dt}")

        tg_send(str(chat_id), "لیست کارات مشتی:\n" + "\n".join(lines))
        return {"ok": True}

    # /done ID
    if text.startswith("/done"):
        try:
            task_id = int(text.replace("/done", "", 1).strip())
            conn = db()
            conn.execute(
                "UPDATE tasks SET done=1 WHERE chat_id=? AND id=?",
                (str(chat_id), task_id)
            )
            conn.commit()
            conn.close()
            tg_send(str(chat_id), f"دمت گرم 😄 کار ID={task_id} انجام شد ✅")
        except Exception:
            tg_send(str(chat_id), "داداش اینجوری بزن: /done 3")
        return {"ok": True}

    # default
    tg_send(str(chat_id), "گرفتم مشتی 😄\nبرای کارها /add یا /list بزن.")
    return {"ok": True}


# Cron اینو هر 1 دقیقه صدا می‌زنه
@app.get(f"/tick/{WEBHOOK_SECRET}")
def tick():
    now_ts = int(time.time())

    conn = db()
    rows = conn.execute(
        "SELECT id, chat_id, title, due_ts, r3_sent, r1_sent, r5_sent, due_sent "
        "FROM tasks WHERE done=0 ORDER BY due_ts ASC LIMIT 200"
    ).fetchall()

    sent_count = 0

    for (task_id, chat_id, title, due_ts, r3, r1, r5, due_sent) in rows:
        due_dt = datetime.fromtimestamp(int(due_ts), TZ)
        now_dt = datetime.fromtimestamp(now_ts, TZ)

        state = {"r3_sent": r3, "r1_sent": r1, "r5_sent": r5, "due_sent": due_sent}

        for col, delta, template in REMINDERS:
            if state[col]:
                continue

            fire_time = due_dt - delta
            if now_dt >= fire_time:
                # پیام
                tg_send(str(chat_id), template.format(title=title))

                # آپدیت DB
                conn.execute(f"UPDATE tasks SET {col}=1 WHERE id=?", (task_id,))
                conn.commit()
                sent_count += 1

        # اگر due_sent شد، دیگه لازم نیست کاری کنیم (ولی done نیست تا خودت /done بزنی)

    conn.close()
    return {"ok": True, "sent": sent_count}
