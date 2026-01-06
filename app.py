import os
import time
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

AI_PROVIDER = (os.environ.get("AI_PROVIDER") or "").lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not BOT_TOKEN or not WEBHOOK_SECRET:
    raise RuntimeError("BOT_TOKEN or WEBHOOK_SECRET is missing")

# اگر خواستی بعداً کابل: ZoneInfo("Asia/Kabul")
TZ = ZoneInfo("Europe/Paris")

DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite3")

SYSTEM_STYLE = (
    "تو یک دستیار تلگرام هستی به اسم «محمود»؛ "
    "خیلی خودمونی، لاتی و مشتی حرف می‌زنی، ولی محترمانه و بدون توهین یا حرف زشت. "
    "پاسخ‌ها کوتاه، کاربردی، و با شوخی ملایم باشه. "
    "اگر کاربر چیزی خواست که نیاز به زمان/تاریخ/جزئیات داره، یک سوال کوتاه بپرس."
)

REMINDERS = [
    ("r3_sent",  timedelta(hours=3),    "⏳ داداش ۳ ساعت دیگه وقتشه: {title}"),
    ("r1_sent",  timedelta(hours=1),    "⚠️ مشتی ۱ ساعت دیگه می‌رسه: {title}"),
    ("r5_sent",  timedelta(minutes=5),  "🚨 ۵ دقیقه مونده‌ها! آماده شو: {title}"),
    ("due_sent", timedelta(seconds=0),  "⏰ وقتشه داداش! الان بزن بریم: {title}"),
]

# ===== DB =====
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

# ===== Telegram send =====
def tg_send(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # هیچ‌وقت اینجا کرش نکنیم
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print("TELEGRAM_SEND_ERROR:", repr(e))

# ===== Time parse =====
def parse_due(text: str) -> datetime:
    """
    Supported:
      - HH:MM  (today; if passed, auto to tomorrow)
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

    # HH:MM
    try:
        t = datetime.strptime(text, "%H:%M")
        dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        return dt
    except ValueError:
        raise ValueError("Bad time format")

# ===== AI (Groq) =====
def ai_reply(user_text: str) -> str:
    if AI_PROVIDER != "groq":
        return "داداش AI هنوز تنظیم نشده 😄"

    if not GROQ_API_KEY:
        return "داداش کلید Groq رو تو Render نذاشتی 😅"

    try:
        client = OpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": SYSTEM_STYLE},
                {"role": "user", "content": user_text},
            ],
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or "یه لحظه مغزم هنگ کرد 😄 دوباره بگو."
    except Exception as e:
        err = str(e).lower()
        # اگر rate limit یا quota یا هرچی خورد
        if "rate" in err or "quota" in err or "429" in err:
            return "داداش الان AI یه کم شلوغه 😅 چند ثانیه دیگه دوباره بگو."
        print("GROQ_ERROR:", repr(e))
        return "داداش AI یه گیر خورد 😅 ولی من هستم. چی می‌خوای؟"

# ===== Routes =====
@app.get("/")
def root():
    return {"status": "ok"}

@app.post(f"/webhook/{WEBHOOK_SECRET}")
def webhook():
    """
    نکته مهم: این endpoint نباید 500 بده.
    حتی اگر همه‌چی خراب شد، باید 200 بده تا تلگرام گیر نکنه.
    """
    try:
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
                "من محمودم، منشی مشتی‌ات ✅\n\n"
                "کارها:\n"
                "/add عنوان | ساعت\n"
                "مثال: /add باشگاه | 21:30\n"
                "یا با تاریخ: /add جلسه | 2026-01-07 14:00\n\n"
                "/list\n"
                "/done ID\n\n"
                "هر چی غیر از دستورها بگی، می‌دم AI جواب بده 😎"
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

                # اگر HH:MM امروز گذشته بود، بنداز فردا
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

        # /done
        if text.startswith("/done"):
            try:
                task_id = int(text.replace("/done", "", 1).strip())
                conn = db()
                conn.execute("UPDATE tasks SET done=1 WHERE chat_id=? AND id=?", (str(chat_id), task_id))
                conn.commit()
                conn.close()
                tg_send(str(chat_id), f"دمت گرم 😄 کار ID={task_id} انجام شد ✅")
            except Exception:
                tg_send(str(chat_id), "داداش اینجوری بزن: /done 3")
            return {"ok": True}

        # default: AI
        try:
            reply = ai_reply(text)
        except Exception as e:
            print("AI_REPLY_FATAL_ERROR:", repr(e))
            reply = "داداش یه مشکل ریز خورد 😅 ولی من هستم. بگو چی می‌خوای؟"

        tg_send(str(chat_id), reply)
        return {"ok": True}

    except Exception as e:
        print("WEBHOOK_FATAL_ERROR:", repr(e))
        # خیلی مهم: 200 بده
        return {"ok": True}

# UptimeRobot هر ۱ دقیقه اینو بزنه
@app.get(f"/tick/{WEBHOOK_SECRET}")
def tick():
    now_ts = int(time.time())

    try:
        conn = db()
        rows = conn.execute(
            "SELECT id, chat_id, title, due_ts, r3_sent, r1_sent, r5_sent, due_sent "
            "FROM tasks WHERE done=0 ORDER BY due_ts ASC LIMIT 200"
        ).fetchall()

        sent_count = 0

        for task_id, chat_id, title, due_ts, r3, r1, r5, due_sent in rows:
            due_dt = datetime.fromtimestamp(int(due_ts), TZ)
            now_dt = datetime.fromtimestamp(now_ts, TZ)

            state = {"r3_sent": r3, "r1_sent": r1, "r5_sent": r5, "due_sent": due_sent}

            for col, delta, template in REMINDERS:
                if state[col]:
                    continue

                fire_time = due_dt - delta
                if now_dt >= fire_time:
                    tg_send(str(chat_id), template.format(title=title))
                    conn.execute(f"UPDATE tasks SET {col}=1 WHERE id=?", (task_id,))
                    conn.commit()
                    sent_count += 1

        conn.close()
        return {"ok": True, "sent": sent_count}

    except Exception as e:
        print("TICK_ERROR:", repr(e))
        return {"ok": True, "sent": 0}
