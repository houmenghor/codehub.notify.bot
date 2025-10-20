from flask import Flask, request
import requests, json, os
from waitress import serve
from datetime import datetime

# ===============================================================
# 🔧 CONFIGURATION
# ===============================================================
TELEGRAM_TOKEN = "7985423327:AAGRGw6jM-ZK6GkGrExkUwcLQKMDF2nG2vM"
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SUBSCRIBERS_FILE = "subscribers.json"
WAITING_FILE = "waiting.json"

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
    """Send formatted HTML message to Telegram."""
    requests.post(f"{BASE_URL}/sendMessage", data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    })

def find_subscriber(chat_id, subscribers):
    for sub in subscribers:
        if sub["chat_id"] == chat_id:
            return sub
    return None

# ===============================================================
# 🤖 TELEGRAM COMMAND HANDLER
# ===============================================================
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    msg = data.get("message", {})
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type", "private")
    text = msg.get("text", "").strip()

    subscribers = load_json(SUBSCRIBERS_FILE)
    waiting = load_json(WAITING_FILE)

    # -----------------------------------------------------------
    # /start (Tutorial Guide)
    # -----------------------------------------------------------
    if text.startswith("/start"):
        tutorial = (
            "👋 <b>Welcome to CodeHub Notify Bot!</b>\n\n"
            "I’ll send GitHub push & branch notifications directly to your group 🚀\n\n"
            "📘 <b>How to use this bot:</b>\n"
            "1️⃣ Type <b>/subscribe</b> — subscribe your group to notifications.\n"
            "2️⃣ Type <b>/connect</b> — link your GitHub repository.\n"
            "3️⃣ Go to your GitHub repo → ⚙️ <b>Settings → Webhooks → Add Webhook</b>\n"
            " • Payload URL → <code>https://your-bot-url/github</code>\n"
            " • Content type → <code>application/json</code>\n"
            " • Select → <b>Send me everything</b>\n"
            " • Save ✅\n\n"
            "Now when you push or create a branch, I’ll notify this group instantly 🎉\n\n"
            "💡 <b>Available Commands:</b>\n"
            "/subscribe — Start receiving notifications\n"
            "/unsubscribe — Stop receiving notifications\n"
            "/connect — Link your GitHub repo\n"
            "/status — Show connected repos\n"
            "/help — Show this help message"
        )
        send_message(chat_id, tutorial)
        return "ok"

    # -----------------------------------------------------------
    # /subscribe
    # -----------------------------------------------------------
    if text.startswith("/subscribe"):
        if not find_subscriber(chat_id, subscribers):
            subscribers.append({"chat_id": chat_id, "repo": None})
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, "✅ Subscribed to GitHub notifications!\nUse /connect to link your repository.")
        else:
            send_message(chat_id, "⚠️ Already subscribed.")

    # -----------------------------------------------------------
    # /unsubscribe
    # -----------------------------------------------------------
    elif text.startswith("/unsubscribe") or text.startswith("/stop"):
        before = len(subscribers)
        subscribers = [s for s in subscribers if s["chat_id"] != chat_id]
        save_json(SUBSCRIBERS_FILE, subscribers)
        if len(subscribers) < before:
            send_message(chat_id, "🛑 Unsubscribed successfully.")
        else:
            send_message(chat_id, "❗ You were not subscribed.")

    # -----------------------------------------------------------
    # /connect
    # -----------------------------------------------------------
    elif text.startswith("/connect"):
        if not find_subscriber(chat_id, subscribers):
            subscribers.append({"chat_id": chat_id, "repo": None})
            save_json(SUBSCRIBERS_FILE, subscribers)
        if chat_id not in waiting:
            waiting.append(chat_id)
            save_json(WAITING_FILE, waiting)
        send_message(chat_id, "📎 Please send your GitHub repository link (example: https://github.com/user/repo)")

    # -----------------------------------------------------------
    # /status
    # -----------------------------------------------------------
    elif text.startswith("/status"):
        count = len(subscribers)
        linked = sum(1 for s in subscribers if s.get("repo"))
        send_message(chat_id, f"📊 Total subscribers: <b>{count}</b>\n🔗 Linked repos: <b>{linked}</b>")

    # -----------------------------------------------------------
    # /help
    # -----------------------------------------------------------
    elif text.startswith("/help"):
        help_msg = (
            "🤖 <b>Available Commands</b>\n\n"
            "/subscribe - Start receiving notifications\n"
            "/unsubscribe - Stop receiving notifications\n"
            "/connect - Link your GitHub repo (interactive)\n"
            "/status - Show subscribers count\n"
            "/help - Show this help message"
        )
        send_message(chat_id, help_msg)

    # -----------------------------------------------------------
    # handle repo after /connect
    # -----------------------------------------------------------
    elif chat_id in waiting:
        repo_input = text
        if repo_input.startswith("https://github.com/"):
            repo_name = repo_input.replace("https://github.com/", "").strip("/")
        else:
            repo_name = repo_input

        sub = find_subscriber(chat_id, subscribers)
        if sub:
            sub["repo"] = repo_name
        else:
            subscribers.append({"chat_id": chat_id, "repo": repo_name})

        save_json(SUBSCRIBERS_FILE, subscribers)
        waiting.remove(chat_id)
        save_json(WAITING_FILE, waiting)

        send_message(chat_id, f"🔗 Connected to <b>{repo_name}</b>\nNow your group will receive GitHub push notifications!")

    else:
        if chat_type == "private":
            send_message(chat_id, "Use /help to see available commands.")
        else:
            send_message(chat_id, "❓ Unknown command. Use /help for assistance.")

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
    subscribers = load_json(SUBSCRIBERS_FILE)

    # ----------------------------------------
    # PUSH EVENT
    # ----------------------------------------
    if event == "push":
        pusher = payload.get("pusher", {}).get("name", "Unknown")
        branch = payload.get("ref", "").split("/")[-1]
        commits = payload.get("commits", [])
        commit_count = len(commits)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        commit_lines = ""
        for commit in commits:
            message = commit.get("message", "").strip()
            author = commit.get("author", {}).get("name", "")
            url = commit.get("url", "")
            commit_lines += f"• <code>{message}</code> — <b>{author}</b>\n<a href=\"{url}\">🔗 View Commit</a>\n\n"

        msg = (
            f"📦 <b>Repo:</b> <a href=\"{repo_url}\">{repo}</a>\n"
            f"👤 <b>Pushed by:</b> {pusher}\n"
            f"🌿 <b>Branch:</b> {branch}\n"
            f"🕒 <b>Time:</b> {timestamp}\n\n"
            f"🚀 <b>{commit_count} commit(s) pushed:</b>\n{commit_lines}"
        )

    # ----------------------------------------
    # CREATE EVENT (Branch creation)
    # ----------------------------------------
    elif event == "create":
        ref_type = payload.get("ref_type", "")
        ref_name = payload.get("ref", "")
        sender = payload.get("sender", {}).get("login", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if ref_type == "branch":
            msg = (
                f"🌿 <b>New Branch Created!</b>\n\n"
                f"📦 <b>Repo:</b> <a href=\"{repo_url}\">{repo}</a>\n"
                f"🌱 <b>Branch:</b> {ref_name}\n"
                f"👤 <b>By:</b> {sender}\n"
                f"🕒 <b>DateTime:</b> {timestamp}"
            )
        else:
            msg = f"🆕 {ref_type.capitalize()} created in {repo}"

    else:
        msg = f"🔔 GitHub event: {event} in {repo}"

    # Send message to all groups linked to this repo
    for sub in subscribers:
        if sub.get("repo") == repo:
            send_message(sub["chat_id"], msg)

    return "ok"


# ===============================================================
# 🚀 RUN SERVER
# ===============================================================
if __name__ == "__main__":
    print("✅ CodeHub Notify Bot started on port 8080")
    serve(app, host="0.0.0.0", port=8080)