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
PAYLOAD_URL = "https://codehubnotify.dev/github"  # your domain webhook endpoint

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

    # normalize command (remove @BotName)
    command = text.split('@')[0]

    subscribers = load_json(SUBSCRIBERS_FILE)
    waiting = load_json(WAITING_FILE)

    # ---------------------------------------------------------------
    # /subscribe -> Add user + show payload info
    # ---------------------------------------------------------------
    if command == "/subscribe":
        if not find_subscriber(chat_id, subscribers):
            subscribers.append({"chat_id": chat_id, "repo": None, "active": False})
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, (
                "✅ You are now subscribed to CodeHub Notify!\n\n"
                "📩 To receive GitHub updates, make sure this bot is added to the group "
                "where you want notifications to appear.\n\n"
                f"Then add this webhook URL to your repository:\n"
                f"<code>{PAYLOAD_URL}</code>\n\n"
                "In GitHub, go to:\n"
                "<b>Settings → Webhooks → Add webhook</b>\n\n"
                "• <b>Payload URL:</b> paste the link above\n"
                "• <b>Content type:</b> application/json\n"
                "• <b>Events:</b> Send me everything ✅\n\n"
                "After that, use /connect to link your repository name."
            ))
        else:
            send_message(chat_id, "⚠️ You are already subscribed.")

    # ---------------------------------------------------------------
    # /unsubscribe -> Remove user completely
    # ---------------------------------------------------------------
    elif command == "/unsubscribe":
        before = len(subscribers)
        subscribers = [s for s in subscribers if s["chat_id"] != chat_id]
        save_json(SUBSCRIBERS_FILE, subscribers)
        if len(subscribers) < before:
            send_message(chat_id, "🛑 You have been unsubscribed and removed from the system.")
        else:
            send_message(chat_id, "❗ You are not subscribed yet.")

    # ---------------------------------------------------------------
    # /start -> Start receiving notifications
    # ---------------------------------------------------------------
    elif command == "/start":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ Please /subscribe first before starting notifications.")
        else:
            sub["active"] = True
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, "▶️ Notifications started! You’ll now receive GitHub updates.")

    # ---------------------------------------------------------------
    # /stop -> Stop receiving notifications
    # ---------------------------------------------------------------
    elif command == "/stop":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ You are not subscribed yet. Use /subscribe first.")
        else:
            sub["active"] = False
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, "⏸️ Notifications stopped. You can start again anytime with /start.")

    # ---------------------------------------------------------------
    # /connect -> Link repository
    # ---------------------------------------------------------------
    elif command == "/connect":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ Please /subscribe first before connecting a repo.")
        else:
            if chat_id not in waiting:
                waiting.append(chat_id)
                save_json(WAITING_FILE, waiting)
            send_message(chat_id, (
                "📎 Please send your GitHub repository link (example: https://github.com/user/repo)\n\n"
                "If you already connected before, sending a new link will update your repository."
            ))

    # ---------------------------------------------------------------
    # Handle repo input after /connect
    # ---------------------------------------------------------------
    elif chat_id in waiting:
        repo_input = text.strip()

        # ✅ Only accept GitHub repo links
        if not repo_input.startswith("https://github.com/"):
            send_message(chat_id, "❌ Please send a valid GitHub repository link (e.g., https://github.com/user/repo)")
            return "ok"

        # Extract the "user/repo" part
        repo_name = repo_input.replace("https://github.com/", "").strip("/")

        # ✅ Validate format "user/repo"
        if "/" not in repo_name or len(repo_name.split("/")) != 2:
            send_message(chat_id, "⚠️ Invalid repository format. Example: https://github.com/user/repo")
            return "ok"

        # ✅ Save or update repo connection
        sub = find_subscriber(chat_id, subscribers)
        if sub:
            old_repo = sub.get("repo")
            sub["repo"] = repo_name
            save_json(SUBSCRIBERS_FILE, subscribers)
            waiting.remove(chat_id)
            save_json(WAITING_FILE, waiting)

            if old_repo and old_repo != repo_name:
                send_message(chat_id,
                    f"🔁 Repository updated from <b>{old_repo}</b> to <b>{repo_name}</b>\n\n"
                    f"You’ll now receive notifications from the new repo."
                )
            else:
                send_message(chat_id,
                    f"🔗 Connected to <b>{repo_name}</b>\n\n"
                    f"You’ll now receive GitHub push notifications from this repo."
                )
        else:
            send_message(chat_id, "⚠️ You must /subscribe first before linking a repo.")

    # ---------------------------------------------------------------
    # /status
    # ---------------------------------------------------------------
    elif command == "/status":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ You are not subscribed yet.")
        else:
            repo = sub.get("repo", "Not connected")
            active = "🟢 Active" if sub.get("active") else "🔴 Stopped"
            send_message(chat_id, f"📊 <b>Status</b>\n\n🔗 Repo: {repo}\n🔔 Notifications: {active}")

    # ---------------------------------------------------------------
    # /subscribers
    # ---------------------------------------------------------------
    elif command == "/subscribers":
        total = len(subscribers)
        active_users = sum(1 for s in subscribers if s.get("active"))
        send_message(chat_id, (
            f"👥 <b>Total Subscribers:</b> {total}\n"
            f"🔔 <b>Active Notifications:</b> {active_users}\n\n"
            "Thank you for using CodeHub Notify Bot 🚀"
        ))

    # ---------------------------------------------------------------
    # /help
    # ---------------------------------------------------------------
    elif command == "/help":
        help_msg = (
            "🤖 <b>Available Commands</b>\n\n"
            "/subscribe - Subscribe and get webhook info\n"
            "/unsubscribe - Unsubscribe completely\n"
            "/start - Start receiving notifications\n"
            "/stop - Stop receiving notifications\n"
            "/connect - Link your GitHub repository\n"
            "/status - Show your connection and status\n"
            "/subscribers - Show total number of subscribers\n"
            "/help - Show all available commands"
        )
        send_message(chat_id, help_msg)

    else:
        if chat_type == "private":
            send_message(chat_id, "❓ Unknown command. Use /help to see available commands.")

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

    if event == "push":
        pusher = payload.get("pusher", {}).get("name", "Unknown")
        branch = payload.get("ref", "").split("/")[-1]
        commits = payload.get("commits", [])
        commit_count = len(commits)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        commit_lines = ""
        for commit in commits:
            message = commit.get("message", "")
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

    elif event == "create":
        ref_type = payload.get("ref_type", "")
        ref_name = payload.get("ref", "")
        sender = payload.get("sender", {}).get("login", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        msg = (
            f"🌿 <b>New Branch Created!</b>\n\n"
            f"📦 <b>Repo:</b> <a href=\"{repo_url}\">{repo}</a>\n"
            f"🌱 <b>Branch:</b> {ref_name}\n"
            f"👤 <b>By:</b> {sender}\n"
            f"🕒 <b>DateTime:</b> {timestamp}"
        )
    else:
        msg = f"🔔 GitHub event: {event} in {repo}"

    for sub in subscribers:
        if sub.get("repo") == repo and sub.get("active", False):
            send_message(sub["chat_id"], msg)

    return "ok"


# ===============================================================
# 🚀 RUN SERVER
# ===============================================================
if __name__ == "__main__":
    print("✅ CodeHub Notify Bot started on port 8080")
    serve(app, host="0.0.0.0", port=8080)
