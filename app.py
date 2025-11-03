from flask import Flask, request
import requests, json, os, datetime, html, hmac, hashlib
from waitress import serve
from zoneinfo import ZoneInfo  # Python 3.9+

# ===============================================================
# 🔧 CONFIG
# ===============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "REPLACE_ME")  # rotate!
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SUBSCRIBERS_FILE = "subscribers.json"
WAITING_FILE = "waiting.json"
PAYLOAD_URL = "https://codehubnotify.dev/github"  # your public webhook URL
WEBHOOK_SECRET = os.getenv("GH_WEBHOOK_SECRET", "")  # set same value in GitHub → Webhooks

app = Flask(__name__)

# ===============================================================
# 📂 Helpers
# ===============================================================
def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def esc(s):  # Escape for Telegram HTML
    return html.escape(str(s or ""))

def kh_now():
    return datetime.datetime.now(ZoneInfo("Asia/Phnom_Penh")).strftime("%Y-%m-%d %H:%M:%S")

def send_message(chat_id, text):
    # Telegram hard limit is 4096 chars. Chunk safely by paragraphs.
    if not text:
        return
    parts = []
    buf, limit = "", 3800  # a little margin
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = line
        else:
            buf += ("\n" if buf else "") + line
    if buf:
        parts.append(buf)
    for p in parts:
        requests.post(f"{BASE_URL}/sendMessage", data={
            "chat_id": chat_id,
            "text": p,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        })

def find_subscriber(chat_id, subscribers):
    for sub in subscribers:
        if sub["chat_id"] == chat_id:
            return sub
    return None

def send_to_repo_subs(repo_full_name, msg):
    subs = load_json(SUBSCRIBERS_FILE)
    for s in subs:
        if s.get("repo") == repo_full_name and s.get("active"):
            send_message(s["chat_id"], msg)

def verify_signature(req):
    if not WEBHOOK_SECRET:
        return True
    their = req.headers.get("X-Hub-Signature-256", "")
    mac = hmac.new(WEBHOOK_SECRET.encode(), req.data, hashlib.sha256).hexdigest()
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
            send_message(chat_id,
                "✅ Subscribed!\n\n"
                f"Add this webhook in your repo:\n<code>{PAYLOAD_URL}</code>\n\n"
                "GitHub → Settings → Webhooks → Add webhook\n"
                "• Content type: application/json\n"
                "• Secret: (set one and put in GH_WEBHOOK_SECRET)\n"
                "• Events: Send me everything ✅\n\n"
                "Then /connect to link your <b>owner/repo</b>.")
        else:
            send_message(chat_id, "⚠️ Already subscribed.")

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
            send_message(chat_id, "▶️ Notifications started.")

    elif command == "/stop":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ Not subscribed. Use /subscribe.")
        else:
            sub["active"] = False
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_message(chat_id, "⏸️ Notifications stopped.")

    elif command == "/connect":
        sub = find_subscriber(chat_id, subscribers)
        if not sub:
            send_message(chat_id, "❗ /subscribe first.")
        else:
            if chat_id not in waiting:
                waiting.append(chat_id)
                save_json(WAITING_FILE, waiting)
            send_message(chat_id, "📎 Send your GitHub repo link (e.g. https://github.com/user/repo).")

    elif chat_id in waiting:
        repo_input = text.strip()
        if not repo_input.startswith("https://github.com/"):
            send_message(chat_id, "❌ Send a valid GitHub repo link.")
            return "ok"
        repo_name = repo_input.replace("https://github.com/", "").strip("/")
        if "/" not in repo_name or len(repo_name.split("/")) != 2:
            send_message(chat_id, "⚠️ Invalid format. Example: https://github.com/user/repo")
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

    elif command == "/help":
        send_message(chat_id,
            "🤖 Commands:\n"
            "/subscribe, /unsubscribe, /start, /stop\n"
            "/connect, /status, /subscribers, /help")

    else:
        if chat_type == "private":
            send_message(chat_id, "❓ Unknown command. Use /help.")
    return "ok"

# ===============================================================
# 🪝 GITHUB WEBHOOK HANDLER (covers most events)
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

    # --- ping (webhook test)
    if event == "ping":
        send_to_repo_subs(repo, f"✅ Webhook connected for <a href='{repo_url}'>{esc(repo)}</a> • {t}")
        return "pong"

    # --- push
    if event == "push":
        pusher = (payload.get("pusher") or {}).get("name", "Unknown")
        branch = (payload.get("ref") or "").split("/")[-1]
        commits = payload.get("commits", [])
        lines = []
        for c in commits[:10]:
            lines.append(f"• <code>{esc(c.get('message'))}</code> — <b>{esc((c.get('author') or {}).get('name'))}</b>\n"
                         f"<a href='{c.get('url','')}'>🔗 View commit</a>")
        more = f"\n…and {len(commits)-10} more." if len(commits) > 10 else ""
        msg = (f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"🌿 <b>{esc(branch)}</b> • 👤 {esc(pusher)} • 🕒 {t}\n\n"
               f"🚀 {len(commits)} commit(s):\n" + "\n\n".join(lines) + more)
        send_to_repo_subs(repo, msg)
        return "ok"

    # --- branches/tags
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

    # --- pull request lifecycle
    if event == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request") or {}
        number = pr.get("number", payload.get("number"))
        title = pr.get("title", "")
        url = pr.get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        state_text = "merged" if (action == "closed" and pr.get("merged")) else action
        if action == "synchronize":
            state_text = "updated (new commits)"
        msg = (f"🔀 PR <b>#{number}</b> {esc(state_text)}\n"
               f"📝 <code>{esc(title)}</code>\n"
               f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>Open PR</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    # --- PR reviews
    if event == "pull_request_review":
        review = payload.get("review") or {}
        pr = payload.get("pull_request") or {}
        state = (review.get("state") or "").lower()  # approved/changes_requested/commented
        url = review.get("html_url", pr.get("html_url", ""))
        sender = (payload.get("sender") or {}).get("login", "")
        number = pr.get("number", payload.get("number"))
        msg = (f"🧪 Review <b>{esc(state)}</b> on PR #{number}\n"
               f"📦 <a href='{repo_url}'>{esc(repo)}</a>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View review</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    # --- code comments on PR diffs
    if event == "pull_request_review_comment":
        comment = payload.get("comment") or {}
        pr = payload.get("pull_request") or {}
        body = comment.get("body", "")
        url = comment.get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        number = pr.get("number", payload.get("number"))
        msg = (f"💬 Code comment on PR #{number}\n"
               f"📝 <code>{esc(body[:300])}</code>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View comment</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    # --- issues (also labels/assigns)
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

    # --- comments (on issues and PRs)
    if event == "issue_comment":
        issue = payload.get("issue") or {}
        body = (payload.get("comment") or {}).get("body", "")
        url = (payload.get("comment") or {}).get("html_url", "")
        sender = (payload.get("sender") or {}).get("login", "")
        number = issue.get("number")
        kind = "PR" if "pull_request" in issue else "Issue"
        msg = (f"💬 Comment on {kind} #{number}\n"
               f"📝 <code>{esc(body[:300])}</code>\n"
               f"👤 {esc(sender)} • 🕒 {t}\n"
               f"<a href='{url}'>View comment</a>")
        send_to_repo_subs(repo, msg)
        return "ok"

    # --- commit comments
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

    # --- stars & forks
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

    # --- releases
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

    # --- workflows / checks
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

    # --- deployments
    if event in ("deployment", "deployment_status"):
        dep = payload.get("deployment") or {}
        env = dep.get("environment", "")
        state = (payload.get("deployment_status") or {}).get("state", payload.get("action", ""))
        url = (payload.get("deployment_status") or {}).get("target_url", repo_url)
        msg = (f"🚀 Deployment <b>{esc(env)}</b> • State: <b>{esc(state)}</b>\n"
               f"<a href='{url}'>Details</a> • 🕒 {t}")
        send_to_repo_subs(repo, msg)
        return "ok"

    # --- security alerts
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

    # --- fallback for everything else
    send_to_repo_subs(repo, f"🔔 Event <b>{esc(event)}</b> • 🕒 {t}\n<a href='{repo_url}'>Open repo</a>")
    return "ok"

# ===============================================================
# 🚀 Run
# ===============================================================
if __name__ == "__main__":
    print("✅ CodeHub Notify Bot started on port 8080")
    serve(app, host="0.0.0.0", port=8080)
