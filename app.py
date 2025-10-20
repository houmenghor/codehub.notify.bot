from flask import Flask, request
import requests, json, os
from waitress import serve
from datetime import datetime

# ===============================================================
# 🔧 CONFIGURATION
# ===============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7985423327:AAGRGw6jM-ZK6GkGrExkUwcLQKMDF2nG2vM")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SUBSCRIBERS_FILE = "subscribers.json"
WAITING_FILE = "waiting.json"
GROUP_FILE = "group.json"
PAYLOAD_URL = "https://codehubnotify.dev/github"

app = Flask(__name__)

# ===============================================================
# 📂 Helper Functions
# ===============================================================
def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def send_message(chat_id, text):
    """Send HTML message."""
    requests.post(f"{BASE_URL}/sendMessage", data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    })

def get_group_id():
    if not os.path.exists(GROUP_FILE):
        return None
    with open(GROUP_FILE, "r") as f:
        try:
            data = json.load(f)
            return data.get("group_id")
        except json.JSONDecodeError:
            return None

def set_group_id(group_id):
    with open(GROUP_FILE, "w") as f:
        json.dump({"group_id": group_id}, f, indent=2)

def find_subscriber(chat_id, subs):
    for s in subs:
        if s["chat_id"] == chat_id:
            return s
    return None

# ===============================================================
# 🤖 TELEGRAM HANDLER
# ===============================================================
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    msg = data.get("message", {})
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type", "private")
    text = msg.get("text", "").strip()

    subs = load_json(SUBSCRIBERS_FILE)
    waiting = load_json(WAITING_FILE)

    # ❌ Ignore all group commands
    if chat_type in ["group", "supergroup"]:
        return "ok"

    # ✅ /subscribe
    if text == "/subscribe":
        if not find_subscriber(chat_id, subs):
            subs.append({"chat_id": chat_id, "repo": None, "active": False})
            save_json(SUBSCRIBERS_FILE, subs)
            send_message(chat_id, (
                "✅ You are now subscribed to CodeHub Notify!\n\n"
                "📩 To receive GitHub updates in a group:\n"
                "1️⃣ Add this bot to your Telegram group.\n"
                "2️⃣ Use /setgroup here (in private chat) to link your group.\n\n"
                "Then add this webhook to your GitHub repo:\n"
                f"<code>{PAYLOAD_URL}</code>\n\n"
                "GitHub → Settings → Webhooks → Add webhook\n"
                "• Payload URL: link above\n"
                "• Content type: application/json\n"
                "• Events: Send everything ✅\n\n"
                "After that, use /connect to link your repo."
            ))
        else:
            send_message(chat_id, "⚠️ You are already subscribed.")

    # ✅ /unsubscribe
    elif text == "/unsubscribe":
        subs = [s for s in subs if s["chat_id"] != chat_id]
        save_json(SUBSCRIBERS_FILE, subs)
        send_message(chat_id, "🛑 You have been unsubscribed.")

    # ✅ /setgroup (in private)
    elif text == "/setgroup":
        send_message(chat_id, (
            "📎 Please send your <b>group invite link</b> or <b>group ID</b>.\n\n"
            "Example:\n"
            "<code>-1001234567890</code>\n"
            "or\n"
            "<code>https://t.me/+AbCdEfGhIjk12345</code>\n\n"
            "➡️ The bot must already be added to that group."
        ))
        waiting.append({"chat_id": chat_id, "type": "setgroup"})
        save_json(WAITING_FILE, waiting)

    elif text.startswith("https://t.me/+") or text.startswith("-100"):
        task = next((w for w in waiting if w["chat_id"] == chat_id and w["type"] == "setgroup"), None)
        if task:
            waiting.remove(task)
            save_json(WAITING_FILE, waiting)

            group_id = text if text.startswith("-100") else text
            set_group_id(group_id)
            send_message(chat_id, "✅ Group registered for GitHub notifications!")
        else:
            send_message(chat_id, "❗ Use /setgroup first before sending a link or ID.")

    # ✅ /start
    elif text == "/start":
        sub = find_subscriber(chat_id, subs)
        if not sub:
            send_message(chat_id, "❗ Please /subscribe first.")
        else:
            sub["active"] = True
            save_json(SUBSCRIBERS_FILE, subs)
            send_message(chat_id, "▶️ Notifications started!")

    # ✅ /stop
    elif text == "/stop":
        sub = find_subscriber(chat_id, subs)
        if sub:
            sub["active"] = False
            save_json(SUBSCRIBERS_FILE, subs)
            send_message(chat_id, "⏸️ Notifications stopped.")

    # ✅ /connect
    elif text == "/connect":
        sub = find_subscriber(chat_id, subs)
        if not sub:
            send_message(chat_id, "❗ Please /subscribe first.")
        else:
            waiting.append({"chat_id": chat_id, "type": "connect"})
            save_json(WAITING_FILE, waiting)
            send_message(chat_id, "📎 Send your GitHub repo link (e.g. https://github.com/user/repo).")

    elif chat_id in [w["chat_id"] for w in waiting if w["type"] == "connect"]:
        repo = text.replace("https://github.com/", "").strip("/")
        sub = find_subscriber(chat_id, subs)
        if sub:
            sub["repo"] = repo
            save_json(SUBSCRIBERS_FILE, subs)
            waiting = [w for w in waiting if not (w["chat_id"] == chat_id and w["type"] == "connect")]
            save_json(WAITING_FILE, waiting)
            send_message(chat_id, f"🔗 Connected to <b>{repo}</b>.\nYour group will receive GitHub updates!")

    # ✅ /status
    elif text == "/status":
        sub = find_subscriber(chat_id, subs)
        repo = sub.get("repo", "Not connected") if sub else "Not subscribed"
        group = get_group_id() or "❌ No group registered"
        active = "🟢 Active" if sub and sub.get("active") else "🔴 Stopped"
        send_message(chat_id, f"📊 <b>Status</b>\n\n🔗 Repo: {repo}\n👥 Group: {group}\n🔔 Status: {active}")

    elif text == "/help":
        send_message(chat_id, (
            "🤖 <b>Available Commands</b>\n\n"
            "/subscribe - Subscribe and get webhook info\n"
            "/unsubscribe - Remove yourself completely\n"
            "/start - Start receiving notifications\n"
            "/stop - Stop notifications\n"
            "/connect - Link your GitHub repo\n"
            "/setgroup - Register a group (send ID or invite link)\n"
            "/status - Show your current setup\n"
            "/help - Show this list"
        ))

    else:
        send_message(chat_id, "❓ Unknown command. Use /help for available commands.")

    return "ok"


# ===============================================================
# 🪝 GITHUB WEBHOOK HANDLER
# ===============================================================
@app.route("/github", methods=["POST"])
def github_webhook():
    payload = request.get_json()
    event = request.headers.get("X-GitHub-Event", "ping")
    repo = payload.get("repository", {}).get("full_name", "unknown/repo")
    repo_url = payload.get("repository", {}).get("html_url", "")
    group_id = get_group_id()

    if not group_id:
        print("⚠️ No group registered for notifications.")
        return "ok"

    if event == "push":
        pusher = payload.get("pusher", {}).get("name", "Unknown")
        branch = payload.get("ref", "").split("/")[-1]
        commits = payload.get("commits", [])
        commit_count = len(commits)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        commit_lines = ""
        for c in commits:
            message = c.get("message", "")
            author = c.get("author", {}).get("name", "")
            url = c.get("url", "")
            commit_lines += f"• <code>{message}</code> — <b>{author}</b>\n<a href=\"{url}\">🔗 View Commit</a>\n\n"

        msg = (
            f"📦 <b>Repo:</b> <a href=\"{repo_url}\">{repo}</a>\n"
            f"👤 <b>Pushed by:</b> {pusher}\n"
            f"🌿 <b>Branch:</b> {branch}\n"
            f"🕒 <b>Time:</b> {timestamp}\n\n"
            f"🚀 <b>{commit_count} commit(s) pushed:</b>\n{commit_lines}"
        )

    elif event == "create":
        ref_name = payload.get("ref", "")
        sender = payload.get("sender", {}).get("login", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            f"🌿 <b>New Branch Created!</b>\n\n"
            f"📦 Repo: <a href=\"{repo_url}\">{repo}</a>\n"
            f"🌱 Branch: {ref_name}\n"
            f"👤 By: {sender}\n"
            f"🕒 {timestamp}"
        )
    else:
        msg = f"🔔 GitHub event: {event} in {repo}"

    send_message(group_id, msg)
    return "ok"


# ===============================================================
# 🚀 RUN SERVER
# ===============================================================
if __name__ == "__main__":
    print("✅ CodeHub Notify Bot started on port 8080")
    serve(app, host="0.0.0.0", port=8080)
