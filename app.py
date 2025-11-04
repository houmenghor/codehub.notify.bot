from flask import Flask, request
import requests, json, os, datetime, html, hmac, hashlib, secrets
from waitress import serve
from zoneinfo import ZoneInfo  # Python 3.9+

# ===============================================================
# 🔧 CONFIG (no disk required)
# ===============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "REPLACE_ME")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Public URL you’ll paste into GitHub (should end with /github)
PUBLIC_WEBHOOK_URL = os.getenv("PUBLIC_WEBHOOK_URL", "https://your-domain.example/github")

# Store files next to this script (ephemeral on Render redeploys)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", BASE_DIR)  # keep default: app folder
os.makedirs(DATA_DIR, exist_ok=True)

SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "subscribers.json")
WAITING_FILE     = os.path.join(DATA_DIR, "waiting.json")
SECRET_FILE      = os.path.join(DATA_DIR, "github_secret.txt")

# Secret priority: env > file > auto-generate (file is ephemeral across deploys)
_env_secret = os.getenv("GH_WEBHOOK_SECRET", "").strip()
if _env_secret:
    GH_SECRET = _env_secret
else:
    if os.path.exists(SECRET_FILE):
        GH_SECRET = open(SECRET_FILE, "r", encoding="utf-8").read().strip()
        if not GH_SECRET:
            GH_SECRET = secrets.token_hex(32)
            with open(SECRET_FILE, "w", encoding="utf-8") as f:
                f.write(GH_SECRET)
    else:
        GH_SECRET = secrets.token_hex(32)
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(GH_SECRET)

app = Flask(__name__)

# ===============================================================
# 📂 Helpers
# ===============================================================
def esc(s):  # Escape for Telegram HTML
    return html.escape(str(s or ""))

def kh_now():
    return datetime.datetime.now(ZoneInfo("Asia/Phnom_Penh")).strftime("%Y-%m-%d %H:%M")

def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # atomic write

def find_subscriber(chat_id, subscribers):
    for sub in subscribers:
        if sub["chat_id"] == chat_id:
            return sub
    return None

def send_message(chat_id, text):
    if not text:
        return
    limit = 3800  # Telegram ~4096
    buf = ""
    for ln in text.split("\n"):
        if len(buf) + len(ln) + 1 > limit:
            requests.post(f"{BASE_URL}/sendMessage", data={
                "chat_id": chat_id, "text": buf,
                "parse_mode": "HTML", "disable_web_page_preview": False
            })
            buf = ln
        else:
            buf += ("\n" if buf else "") + ln
    if buf:
        requests.post(f"{BASE_URL}/sendMessage", data={
            "chat_id": chat_id, "text": buf,
            "parse_mode": "HTML", "disable_web_page_preview": False
        })

def send_to_repo_subs(repo_full_name, msg):
    subs = load_json(SUBSCRIBERS_FILE)
    for s in subs:
        if s.get("repo") == repo_full_name and s.get("active"):
            send_message(s["chat_id"], msg)

def verify_signature(req):
    if not GH_SECRET:  # should never happen here
        return True
    their = req.headers.get("X-Hub-Signature-256", "")
    mac = hmac.new(GH_SECRET.encode(), req.data, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={mac}", their)

# ===============================================================
# 🤖 TELEGRAM HANDLER
# ===============================================================
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.get_json() or {}
    msg = data.get("message", {})
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type", "private")
    text = (msg.get("text") or "").strip()
    command = text.split('@')[0]

    subscribers = load_json(SUBSCRIBERS_FILE)
    waiting = load_json(WAITING_FILE)

    if command == "/subscribe":
        if not find_subscriber(chat_id, subscribers):
            subscribers.append({"chat_id": chat_id, "repo": None, "active": False})
            save_json(SUBSCRIBERS_FILE, subscribers)

        payload_url = PUBLIC_WEBHOOK_URL or "https://<your-domain>/github"
        send_message(chat_id,
            "✅ You are now subscribed to CodeHub Notify!\n\n"
            "Add this webhook in your GitHub repo:\n"
            f"• <b>Payload URL:</b> <code>{esc(payload_url)}</code>\n"
            f"• <b>Content type:</b> application/json\n"
            f"• <b>Secret:</b> <code>{esc(GH_SECRET)}</code>\n"
            "• <b>Events:</b> Send me everything ✅\n\n"
            "Now run <b>/connect</b> and send your repo link (e.g., https://github.com/owner/repo).\n"
            "Then <b>/start</b> to begin notifications."
        )

    elif command == "/unsubscribe":
        before = len(subscribers)
        subscribers = [s for s in subscribers if s["chat_id"] != chat_id]
        save_json(SUBSCRIBERS_FILE, subscribers)
        send_message(chat_id, "🛑 Unsubscribed." if len(subscribers) < before else "❗ Not subscribed.")

    elif command == "/start":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ Use /subscribe first.")
        else:
            sub["active"] = True
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, "▶️ Notifications started! You’ll now receive GitHub updates.")

    elif command == "/stop":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ Not subscribed. Use /subscribe.")
        else:
            sub["active"] = False
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, "⏸️ Notifications stopped. Use /start to resume.")

    elif command == "/connect":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ /subscribe first.")
        else:
            if chat_id not in waiting:
                waiting.append(chat_id)
                save_json(WAITING_FILE, waiting)
            send_message(chat_id, "📎 Send your GitHub repository link (e.g., https://github.com/owner/repo).")

    elif chat_id in waiting:
        repo_input = text.strip()
        if not repo_input.startswith("https://github.com/"):
            send_message(chat_id, "❌ Send a valid GitHub repository link (e.g., https://github.com/owner/repo).")
            return "ok"
        repo_name = repo_input.replace("https://github.com/", "").strip("/")
        if "/" not in repo_name or len(repo_name.split("/")) != 2:
            send_message(chat_id, "⚠️ Invalid format. Example: https://github.com/owner/repo")
            return "ok"

        sub = find_subscriber(chat_id, subscribers)
        if sub:
            old = sub.get("repo")
            sub["repo"] = repo_name
            save_json(SUBSCRIBERS_FILE, subscribers)
            waiting.remove(chat_id)
            save_json(WAITING_FILE, waiting)
            if old and old != repo_name:
                send_message(chat_id, f"🔁 Updated repo: <b>{esc(old)}</b> → <b>{esc(repo_name)}</b>")
            else:
                send_message(chat_id, f"🔗 Connected to <b>{esc(repo_name)}</b>.")
        else:
            send_message(chat_id, "❗ /subscribe first.")

    elif command == "/status":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ Not subscribed.")
        else:
            repo = sub.get("repo", "Not connected")
            active = "🟢 Active" if sub.get("active") else "🔴 Stopped"
            send_message(chat_id, f"📊 <b>Status</b>\n🔗 Repo: {esc(repo)}\n🔔 {active}")

    elif command == "/subscribers":
        total = len(subscribers)
        active_users = sum(1 for s in subscribers if s.get("active"))
        send_message(chat_id, f"👥 Total: {total}\n🔔 Active: {active_users}")

    elif command == "/secret":
        send_message(chat_id,
            "🔐 Your current GitHub Webhook Secret is:\n"
            f"<code>{esc(GH_SECRET)}</code>\n\n"
            "Paste this into GitHub → Settings → Webhooks → Secret."
        )

    elif command == "/help":
        send_message(chat_id,
            "🤖 Commands:\n"
            "/subscribe, /unsubscribe, /start, /stop\n"
            "/connect, /status, /subscribers, /secret, /help"
        )
    else:
        if chat_type == "private":
            send_message(chat_id, "❓ Unknown command. Use /help.")
    return "ok"

# ===============================================================
# 🪝 GITHUB WEBHOOK HANDLER
# ===============================================================
@app.route("/github", methods=["POST"])
def github_webhook():
    if not verify_signature(request):
        return "invalid signature", 403

    payload = request.get_json(silent=True) or {}
    event = request.headers.get("X-GitHub-Event", "ping")
    repo = (payload.get("repository") or {}).get("full_name", "unknown/repo")
    repo_url = (payload.get("repository") or {}).get("html_url", "")
    t = kh_now()

    if event == "ping":
        send_to_repo_subs(repo, f"✅ Webhook connected for <a href='{repo_url}'>{esc(repo)}</a> • {t}")
        return "pong"

    if event == "push":
        pusher = (payload.get("pusher") or {}).get("name", "Unknown")
        branch = (payload.get("ref") or "").split("/")[-1]
        commits = payload.get("commits", [])
        lines = []
        for c in commits[:10]:
            lines.append(
                f"• <code>{esc(c.get('message'))}</code> — <b>{esc((c.get('author') or {}).get('name'))}</b>\n"
                f"<a href='{c.get('url','')}'>🔗 View commit</a>"
            )
        more = f"\n…and {len(commits)-10} more." if len(commits) > 10 else ""
        msg = (f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"🌿 <b>{esc(branch)}</b> • 👤 {esc(pusher)} • 🕒 {t}\n\n"
               f"🚀 {len(commits)} commit(s):\n" + "\n\n".join(lines) + more)
        send_to_repo_subs(repo, msg)
        return "ok"

    if event in ("create", "delete"):
        ref = payload.get("ref", "")
        ref_type = payload.get("ref_type", "")
        sender = (payload.get("sender") or {}).get("login", "")
        icon = "🆕" if event == "create" else "🗑️"
        msg = (f"{icon} {esc(ref_type)} <b>{esc(ref)}</b> {event}d\n"
               f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"👤 {esc(sender)} • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request") or {}
        number = pr.get("number", payload.get("number"))
        title  = pr.get("title", "")
        url    = pr.get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        head   = (pr.get("head") or {}).get("ref", "")  # FROM
        base   = (pr.get("base") or {}).get("ref", "")  # TO

        if action == "synchronize":
            state_text = "updated (new commits)"
        elif action == "closed" and pr.get("merged"):
            state_text = "merged"
        elif action == "ready_for_review":
            state_text = "marked ready for review"
        elif action == "converted_to_draft":
            state_text = "converted to draft"
        else:
            state_text = action

        msg = (
            f"🔀 <b>Pull request #{number} {esc(state_text)}</b>\n"
            f"🧭 from <b>{esc(head)}</b> ➜ to <b>{esc(base)}</b>\n"
            f"📝 <code>{esc(title)}</code>\n"
            f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
            f"👤 {esc(sender)} • 🕒 {t}\n"
            f"<a href='{url}'>Open pull request</a>"
        )
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "pull_request_review":
        review = payload.get("review") or {}
        pr = payload.get("pull_request") or {}
        state = (review.get("state") or "").lower()
        url = review.get("html_url", pr.get("html_url", ""))
        sender = (payload.get("sender") or {}).get("login", "")
        number = pr.get("number", payload.get("number"))
        msg = (f"🧪 Review <b>{esc(state)}</b> on <b>pull request #{number}</b>\n"
               f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View review</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "pull_request_review_comment":
        comment = payload.get("comment") or {}
        pr = payload.get("pull_request") or {}
        body = comment.get("body", "")
        url = comment.get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        number = pr.get("number", payload.get("number"))
        msg = (f"💬 Code comment on <b>pull request #{number}</b>\n"
               f"📝 <code>{esc(body[:300])}</code>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View comment</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "issues":
        action = payload.get("action", "")
        issue = payload.get("issue") or {}
        number = issue.get("number")
        title = issue.get("title", "")
        url = issue.get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        msg = (f"🐞 Issue <b>#{number}</b> {esc(action)}\n"
               f"📝 <code>{esc(title)}</code>\n"
               f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>Open issue</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "issue_comment":
        issue = payload.get("issue") or {}
        body = (payload.get("comment") or {}).get("body", "")
        url = (payload.get("comment") or {}).get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        number = issue.get("number")
        kind = "Pull request" if "pull_request" in issue else "Issue"
        msg = (f"💬 Comment on <b>{kind} #{number}</b>\n"
               f"📝 <code>{esc(body[:300])}</code>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View comment</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "commit_comment":
        comment = payload.get("comment") or {}
        body = comment.get("body", "")
        url = comment.get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        msg = (f"💬 Comment on commit\n"
               f"📝 <code>{esc(body[:300])}</code>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View comment</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "star":
        action = payload.get("action", "")
        sender = (payload.get("sender") or {}).get("login", "")
        msg = f"⭐ Repo <a href='{repo_url}'>{esc(repo)}</a> {esc(action)} by {esc(sender)} • 🕒 {t}"
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "fork":
        forkee = (payload.get("forkee") or {}).get("full_name", "")
        sender = (payload.get("sender") or {}).get("login", "")
        msg = (f"🍴 Forked by {esc(sender)} → <b>{esc(forkee)}</b>\n"
               f"📦 <a href='{repo_url}'>{esc(repo)}</a> • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "release":
        action = payload.get("action", "")
        rel = payload.get("release") or {}
        tag = rel.get("tag_name", "")
        url = rel.get("html_url", repo_url)
        sender = (payload.get("sender") or {}).get("login", "")
        msg = (f"🏷️ Release <b>{esc(tag)}</b> {esc(action)}\n"
               f"📦 <a href='{url}'>Open release</a>\n"
               f"👤 {esc(sender)} • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "workflow_run":
        wr = payload.get("workflow_run") or {}
        name = wr.get("name", "")
        status = wr.get("status", "")
        conclusion = wr.get("conclusion", "")
        url = wr.get("html_url", repo_url)
        msg = (f"🛠️ Workflow <b>{esc(name)}</b>\n"
               f"Status: <b>{esc(status)}</b> • Result: <b>{esc(conclusion)}</b>\n"
               f"<a href='{url}'>Open run</a> • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event in ("check_suite", "check_run"):
        obj = payload.get("check_suite") or payload.get("check_run") or {}
        name = obj.get("name", payload.get("action", ""))
        status = obj.get("status", "")
        conclusion = obj.get("conclusion", "")
        url = obj.get("html_url", repo_url)
        msg = (f"✅ {esc(event)} <b>{esc(name)}</b>\n"
               f"Status: <b>{esc(status)}</b> • Result: <b>{esc(conclusion)}</b>\n"
               f"<a href='{url}'>Open</a> • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event in ("deployment", "deployment_status"):
        dep = payload.get("deployment") or {}
        env = dep.get("environment", "")
        state = (payload.get("deployment_status") or {}).get("state", payload.get("action", ""))
        url = (payload.get("deployment_status") or {}).get("target_url", repo_url)
        msg = (f"🚀 Deployment <b>{esc(env)}</b> • State: <b>{esc(state)}</b>\n"
               f"<a href='{url}'>Details</a> • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    if event == "repository_vulnerability_alert":
        action = payload.get("action", "")
        alert = payload.get("alert") or {}
        pkg = (alert.get("affected_package_name") or "")
        adv = ((alert.get("advisory") or {}).get("summary") or "")
        msg = (f"🛡️ Vulnerability {esc(action)}\n"
               f"📦 Package: <b>{esc(pkg)}</b>\n"
               f"📝 <code>{esc(adv[:300])}</code>\n"
               f"🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    send_to_repo_subs(repo, f"🔔 Event <b>{esc(event)}</b> • 🕒 {t}\n<a href='{repo_url}'>Open repo</a>")
    return "ok"

# ===============================================================
# 🚀 Run
# ===============================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))  # Render provides PORT
    print(f"✅ CodeHub Notify Bot started on port {port}")
    serve(app, host="0.0.0.0", port=port)
