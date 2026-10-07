"""
Updates: the app asks GitHub for its newest release and installs it.

A release (build-exe.py --release) is two files on the project's GitHub
releases page: the Setup .exe, and latest.json, which names that Setup with
its size and SHA-256 and is signed (Ed25519) with a key that stays on the
publisher's PC, never in the repository. The app checks the signature against
PUBLIC_KEY before it believes anything latest.json says, and checks the Setup
it downloads against the signed size and hash. So neither a file changed on
GitHub nor someone who gets into the GitHub account can make the app run a
program the key did not sign.

Nothing here touches the network unless asked: the "Check for updates" button,
or the box to check when the app starts, which is off until it is ticked.

The update itself is the ordinary Setup, run with /SILENT: it shows its
progress, replaces the app whole, keeps the settings, and starts the app again
(/relaunch=1, see installer.iss). Only a copy the Setup installed is updated in
place; any other copy (a folder build, a development copy) is pointed to the
download page instead.
"""

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

APP_NAME = "Divinum Officium"
REPO = "Burgermiester8/OfflineOfficium"
MANIFEST = "latest.json"
MANIFEST_URL = "https://github.com/%s/releases/latest/download/%s" % (REPO, MANIFEST)
RELEASES_PAGE = "https://github.com/%s/releases/latest" % REPO
# Checks a release against another address (a test server); the signature is
# checked all the same, so this cannot bring in anything unsigned.
URL_OVERRIDE = "OFFLINE_OFFICIUM_UPDATE_URL"

# The public half of the release key (build-exe.py --new-signing-key made it;
# the private half is on the publisher's PC, see build-exe.py SIGNING_KEY).
PUBLIC_KEY = "a5a7e7d42d54cc15ed06f4a3b3f2ed5dec23d43cfac5753ae17d66192b9142c0"
# The fields of latest.json the signature covers, in this order.
SIGNED = ("version", "setup", "url", "size", "sha256", "notes")
SETUP_NAME = re.compile(r"^DivinumOfficium-Setup-[0-9][0-9.]*\.exe$")
VERSION = re.compile(r"^[0-9]+(\.[0-9]+)*$")
# The Setup's AppId (installer.iss), under which Windows records where it installed.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{250fda77-7847-4a66-818d-124c0c8be711}_is1"
TIMEOUT = 20
CHECK_EVERY = 20 * 3600  # the check at start: at most about once a day


class UpdateError(Exception):
    pass


# --------------------------------------------------------------------------
# Ed25519 (RFC 8032, section 6: its reference code). Python's standard library
# has no Ed25519, and this checks one signature per update, so plain integer
# arithmetic is fast enough.

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    p1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    p2 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    c, d = 2 * a[3] * b[3] * _D % _P, 2 * a[2] * b[2] % _P
    e, f, g, h = p2 - p1, d - c, d + c, p2 + p1
    return (e * f, g * h, f * g, e * h)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(pt):
    zinv = pow(pt[2], _P - 2, _P)
    x, y = pt[0] * zinv % _P, pt[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _hash_int(data):
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret):
    return _compress(_mul(_expand(secret)[0], _G))


def sign(secret, message):
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, _G))
    r = _hash_int(prefix + message)
    rs = _compress(_mul(r, _G))
    s = (r + _hash_int(rs + pub + message) * a) % _Q
    return rs + int.to_bytes(s, 32, "little")


def verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _hash_int(signature[:32] + public + message)
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


# --------------------------------------------------------------------------
# releases


def signed_bytes(manifest):
    """What the signature covers: the signed fields, in a fixed form."""
    return json.dumps([manifest[k] for k in SIGNED], ensure_ascii=True,
                      separators=(",", ":")).encode("ascii")


def parse_version(v):
    return tuple(int(x) for x in v.split("."))


def current_version():
    """This copy's version (version.txt beside the exe, written by the build that
    made its Setup), or None for a copy that was not made into one."""
    if not getattr(sys, "frozen", False):
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "version.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            v = fh.read().strip()
    except OSError:
        return None
    return v if VERSION.match(v) else None


def _open(url, timeout=TIMEOUT):
    ua = "OfflineOfficium/%s" % (current_version() or "dev")
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}),
                                  timeout=timeout)


def fetch_manifest():
    """The newest release's latest.json, once its signature checks out."""
    url = os.environ.get(URL_OVERRIDE) or MANIFEST_URL
    try:
        with _open(url) as resp:
            raw = resp.read(65536)
    except OSError as exc:  # no connection, no such release, a timeout
        raise UpdateError("Could not reach GitHub (%s). Checking for updates needs "
                          "an internet connection; everything else works without one."
                          % getattr(exc, "reason", exc))
    try:
        manifest = json.loads(raw.decode("utf-8"))
        signature = bytes.fromhex(manifest["signature"])
        fields_ok = (all(k in manifest for k in SIGNED)
                     and VERSION.match(manifest["version"])
                     and SETUP_NAME.match(manifest["setup"])
                     and manifest["url"].startswith(("https://", "http://127.0.0.1"))
                     and isinstance(manifest["size"], int)
                     and re.match(r"^[0-9a-f]{64}$", manifest["sha256"]))
    except (ValueError, KeyError, TypeError, AttributeError):
        fields_ok = False
    if not fields_ok or not verify(bytes.fromhex(PUBLIC_KEY), signed_bytes(manifest), signature):
        raise UpdateError("The update information on GitHub is not signed by this app's "
                          "publisher, so it was ignored.")
    return manifest


def newer(manifest, current):
    return current is None or parse_version(manifest["version"]) > parse_version(current)


def installed_here():
    """Whether this copy is the one the Setup installed (so the Setup can update it)."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            where = winreg.QueryValueEx(k, "InstallLocation")[0]
    except OSError:
        return False
    here = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.normcase(os.path.normpath(where)) == os.path.normcase(os.path.normpath(here))


def clean_old_downloads():
    """Remove the Setups earlier updates downloaded (the Setup cannot delete itself)."""
    for name in os.listdir(tempfile.gettempdir()):
        path = os.path.join(tempfile.gettempdir(), name)
        if name.startswith("do-update-") and os.path.isdir(path):
            try:
                if time.time() - os.path.getmtime(path) > 3600:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass


def download(manifest, progress=None, cancelled=None):
    """Download the release's Setup; its path, once its size and SHA-256 match."""
    clean_old_downloads()
    folder = tempfile.mkdtemp(prefix="do-update-")
    path = os.path.join(folder, manifest["setup"])
    digest, done = hashlib.sha256(), 0
    try:
        with _open(manifest["url"], timeout=60) as resp, open(path, "wb") as out:
            while True:
                if cancelled and cancelled():
                    raise UpdateError("cancelled")
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if done > manifest["size"]:
                    break
                if progress:
                    progress(done, manifest["size"])
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("The download failed (%s)." % getattr(exc, "reason", exc))
    except UpdateError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if done != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("The downloaded file is not the one the release names "
                          "(its size or checksum differs), so it was not run.")
    return path


def launch_setup(path):
    """Start the Setup on its own: silent but for its progress window; it waits
    for the app to close (installer.iss, AppMutex) and starts it again after."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path, "/SILENT", "/NORESTART", "/relaunch=1"], creationflags=flags,
                     close_fds=True, cwd=os.path.dirname(path))


def selftest():
    """No network: the signature check against RFC 8032's first test, and the key."""
    secret = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    expected = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bac"
        "c61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    if public_key(secret) != public or sign(secret, b"") != expected:
        raise RuntimeError("Ed25519 signing gives the wrong result")
    if not verify(public, b"", expected) or verify(public, b"x", expected):
        raise RuntimeError("Ed25519 verification gives the wrong result")
    if _decompress(bytes.fromhex(PUBLIC_KEY)) is None:
        raise RuntimeError("PUBLIC_KEY is not a valid key")
    return "version %s, %s; signatures check; releases from github.com/%s" % (
        current_version() or "none (not made into a Setup)",
        "installed by the Setup" if installed_here() else "not installed by the Setup", REPO)


# --------------------------------------------------------------------------
# the main window's part: the version, the button, the box


class UpdatePanel:
    """Built into the main window. `before_install` is called just before the
    Setup starts: it must let go of the app's files (the server, the mutex)."""

    def __init__(self, parent, root, log, before_install):
        import tkinter as tk
        from tkinter import ttk

        import doui

        self.root, self.log, self.before_install = root, log, before_install
        self.results = queue.Queue()
        self.busy = False
        self.current = current_version()
        frame = ttk.Frame(parent, style="Window.TFrame")
        self.frame = frame
        ttk.Label(frame, text=("Version %s" % self.current) if self.current else "Development copy",
                  style="Window.Muted.TLabel").pack(side="left", padx=(0, 10))
        self.auto = tk.BooleanVar(value=bool(doui.setting("check_updates", False)))
        ttk.Checkbutton(frame, text="Check when the app starts", variable=self.auto,
                        style="Window.TCheckbutton",
                        command=lambda: doui.save_setting("check_updates", self.auto.get())
                        ).pack(side="left", padx=(0, 10))
        self.button = ttk.Button(frame, text="Check for updates…", style="Window.TButton",
                                 command=lambda: self.check(manual=True))
        self.button.pack(side="left")

    def pack(self, **kw):
        self.frame.pack(**kw)

    def start(self):
        """The check at start, if the box is ticked and the last was a day ago."""
        import doui

        if self.auto.get() and time.time() - float(doui.setting("last_update_check", 0) or 0) > CHECK_EVERY:
            self.check(manual=False)

    def check(self, manual):
        if self.busy:
            return
        import doui

        self.busy = True
        self.button.config(state="disabled", text="Checking…")
        doui.save_setting("last_update_check", time.time())

        def work():
            try:
                self.results.put(("ok", fetch_manifest()))
            except UpdateError as exc:
                self.results.put(("error", str(exc)))
            except Exception as exc:  # never take the app down over this
                self.results.put(("error", "Checking for updates failed: %s" % exc))

        threading.Thread(target=work, daemon=True).start()
        self._wait(lambda kind, value: self._checked(kind, value, manual))

    def _wait(self, then):
        try:
            kind, value = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self._wait, then)
            return
        then(kind, value)

    def _done(self):
        self.busy = False
        self.button.config(state="normal", text="Check for updates…")

    def _checked(self, kind, value, manual):
        from tkinter import messagebox

        self._done()
        if kind == "error":
            self.log("update check: %s" % value)
            if manual:
                messagebox.showwarning(APP_NAME, value, parent=self.root)
            return
        m = value
        if not newer(m, self.current):
            self.log("update check: version %s is the newest" % self.current)
            if manual:
                messagebox.showinfo(APP_NAME, "You have the newest version (%s)." % self.current,
                                    parent=self.root)
            return
        self.log("update check: version %s is available" % m["version"])
        notes = ("\n\n" + m["notes"].strip()) if m["notes"].strip() else ""
        have = ("you have %s" % self.current) if self.current else "this is a development copy"
        if not installed_here():
            if messagebox.askyesno(APP_NAME, "Version %s is available (%s).%s\n\nThis copy was not "
                                   "installed by the Setup, so it cannot update itself. Open the "
                                   "download page?" % (m["version"], have, notes), parent=self.root):
                import webbrowser

                webbrowser.open(RELEASES_PAGE)
            return
        if messagebox.askyesno(APP_NAME, "Version %s is available (%s).%s\n\nDownload it (%d MB) and "
                               "install it now? Divinum Officium closes while it installs and opens "
                               "again when it is done." % (m["version"], have, notes,
                                                           round(m["size"] / 1048576)),
                               parent=self.root):
            self._download(m)

    def _download(self, m):
        import tkinter as tk
        from tkinter import messagebox, ttk

        import doui

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Updating %s" % APP_NAME)
        win.resizable(False, False)
        win.transient(self.root)
        doui.install(win)
        body = ttk.Frame(win, style="Card.TFrame", padding=(18, 14))
        body.pack(fill="both", expand=True)
        label = ttk.Label(body, text="Downloading version %s…" % m["version"])
        label.pack(anchor="w")
        bar = ttk.Progressbar(body, length=360, mode="determinate", maximum=m["size"])
        bar.pack(fill="x", pady=(10, 12))
        stop = threading.Event()
        cancel = ttk.Button(body, text="Cancel", command=stop.set)
        cancel.pack(anchor="e")
        win.protocol("WM_DELETE_WINDOW", stop.set)
        doui.install(win)
        doui.present(win, over=self.root)
        self.busy = True
        self.button.config(state="disabled")
        progress = queue.Queue()

        def work():
            try:
                path = download(m, progress=lambda done, total: progress.put(done),
                                cancelled=stop.is_set)
                self.results.put(("ok", path))
            except UpdateError as exc:
                self.results.put(("error", str(exc)))
            except Exception as exc:
                self.results.put(("error", "The download failed: %s" % exc))

        def tick():
            done = None
            while not progress.empty():
                done = progress.get_nowait()
            if done is not None:
                bar.config(value=done)
                label.config(text="Downloading version %s… %d of %d MB" % (
                    m["version"], done // 1048576, round(m["size"] / 1048576)))
            if self.busy:
                win.after(100, tick)

        def finished(kind, value):
            self._done()
            if kind == "error":
                win.destroy()
                if not stop.is_set():
                    self.log("update: %s" % value)
                    messagebox.showerror(APP_NAME, value, parent=self.root)
                return
            label.config(text="Starting the installer…")
            cancel.config(state="disabled")
            win.update_idletasks()
            self.log("update: installing %s from %s" % (m["version"], value))
            self.before_install()
            launch_setup(value)
            self.root.destroy()

        threading.Thread(target=work, daemon=True).start()
        win.after(100, tick)
        self._wait(finished)


# --------------------------------------------------------------------------
# without the window: DivinumOfficium.exe --check-update / --update


def run_cli(install, before_install=lambda: None):
    current = current_version()
    print("this copy: %s" % (current or "development copy"))
    try:
        m = fetch_manifest()
    except UpdateError as exc:
        print("error: %s" % exc)
        return 1
    print("newest release: %s (%s, %d bytes; signature checks)" % (m["version"], m["setup"], m["size"]))
    if not newer(m, current):
        print("up to date")
        return 0
    if not install:
        print("an update is available: DivinumOfficium.exe --update installs it")
        return 0
    if not installed_here():
        print("error: this copy was not installed by the Setup; download it from %s" % RELEASES_PAGE)
        return 1
    last = [0]

    def progress(done, total):
        if done - last[0] >= 8 << 20 or done == total:
            last[0] = done
            print("  %d of %d MB" % (done // 1048576, round(total / 1048576)), flush=True)

    try:
        path = download(m, progress)
    except UpdateError as exc:
        print("error: %s" % exc)
        return 1
    print("verified %s; starting it" % path)
    before_install()
    launch_setup(path)
    return 0
