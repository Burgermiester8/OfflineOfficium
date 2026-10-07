"""
Divinum Officium -- offline desktop launcher.

Starts the local CGI server; its buttons open the office (or the Mass) in the
default browser.
Everything it needs (a Perl interpreter and the breviary data) is either
bundled next to the executable or embedded in it, so it runs with no
network connection and nothing installed.

Frozen with PyInstaller; see build-exe.py.
"""

import os
import queue
import sys
import tempfile
import threading
import webbrowser

import docgi

APP_NAME = "Divinum Officium"
START_PAGE = "/cgi-bin/horas/officium.pl"
PREFERRED_PORT = 8000
# Held while the app runs: the installer (installer.iss, AppMutex) looks for it
# and asks for the app to be closed before it replaces or removes the files.
APP_MUTEX = "DivinumOfficiumApp"
_mutex = None


def hold_app_mutex():
    global _mutex
    if os.name != "nt":
        return
    try:
        import ctypes

        create = ctypes.windll.kernel32.CreateMutexW
        create.restype = ctypes.c_void_p
        _mutex = create(None, False, APP_MUTEX)
    except (OSError, AttributeError):
        pass


def release_app_mutex():
    """Before the app starts its own update: the Setup waits while it is held."""
    global _mutex
    if _mutex:
        import ctypes

        close = ctypes.windll.kernel32.CloseHandle
        close.argtypes = (ctypes.c_void_p,)
        close(_mutex)
        _mutex = None


# --------------------------------------------------------------------------
# diagnostics
#
# A windowed build has no stdout, so without this there is no way to find out
# why a launch failed. Everything logged also goes to a file on disk.

_logfile = None


def log_path():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    directory = os.path.join(base, APP_NAME.replace(" ", ""))
    try:
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "last-run.log")
    except OSError:
        return os.path.join(tempfile.gettempdir(), "divinum-officium.log")


def open_log():
    global _logfile
    try:
        _logfile = open(log_path(), "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        _logfile = None


def write_log(msg):
    if _logfile:
        try:
            _logfile.write(msg + "\n")
        except (OSError, ValueError):
            pass


def attach_console():
    """Give a windowed build a usable stdout when run with --console."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    if not kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        kernel32.AllocConsole()
    for name in ("stdout", "stderr"):
        try:
            setattr(
                sys, name,
                open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace"),
            )
        except OSError:
            pass


# --------------------------------------------------------------------------
# locating the bundled pieces


def search_roots():
    """Directories that may hold the bundled perl/ and web/ trees, in order."""
    roots = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots += [exe_dir, os.path.join(exe_dir, "_internal")]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [os.path.join(here, "build", "app"), here]
    return roots


def find_resource(*relative):
    for root in search_roots():
        candidate = os.path.join(root, *relative)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return None


def locate_perl():
    """Bundled interpreter if present, otherwise one from PATH."""
    bundled = find_resource("perl", "bin", "perl.exe")
    if bundled:
        return bundled, True
    import shutil

    found = shutil.which("perl")
    return found, False


def locate_web():
    for rel in (("web",), ("repo", "web")):
        found = find_resource(*rel)
        if found and os.path.isdir(os.path.join(found, "cgi-bin")):
            return found
    return None


# --------------------------------------------------------------------------
# server lifecycle


class OfficeServer:
    def __init__(self, log):
        self.log = log
        self.server = None
        self.port = None
        self.thread = None
        # Kept for the PDF maker, which runs the same engine without HTTP.
        self.web_root = None
        self.perl = None
        self.libs = []

    def start(self):
        web_root = locate_web()
        if not web_root:
            raise RuntimeError(
                "Could not find the breviary data (a web/ directory containing "
                "cgi-bin). Looked in:\n  " + "\n  ".join(search_roots())
            )

        perl, bundled = locate_perl()
        if not perl:
            raise RuntimeError(
                "No Perl interpreter found. This build expects a bundled "
                "perl/bin/perl.exe, and none was located; install Strawberry "
                "Perl as a fallback."
            )

        # A vendored CGI.pm, for the case where we fall back to a system perl
        # that predates 5.22 removing it from core.
        libs = []
        perl_lib = find_resource("perl-lib")
        if perl_lib and not bundled:
            libs.append(perl_lib)

        self.web_root, self.perl, self.libs = web_root, perl, libs
        self.server, self.port = docgi.make_server(
            web_root, perl, perl_libs=libs, port=PREFERRED_PORT, log=self.log
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        self.log("web root : %s" % web_root)
        self.log("perl     : %s%s" % (perl, "" if bundled else "  (system)"))
        self.log("listening on %s" % self.url())
        return self.port

    def url(self, path=START_PAGE):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


# --------------------------------------------------------------------------
# windowed front end


def run_gui():
    import tkinter as tk
    from tkinter import messagebox, ttk

    import doui

    messages = queue.Queue()

    def log(msg):
        write_log(str(msg))
        messages.put(str(msg))

    root = tk.Tk()
    root.withdraw()  # built out of sight, then shown finished (doui.present)
    root.title(APP_NAME)
    root.minsize(760, 420)

    icon = find_resource("web", "favicon.ico") or find_resource(
        "repo", "web", "favicon.ico"
    )
    if icon:
        try:
            root.iconbitmap(icon)
        except tk.TclError:
            pass
    doui.install(root)

    outer = ttk.Frame(root, padding=(18, 14, 18, 16), style="Window.TFrame")
    outer.pack(fill="both", expand=True)
    header = ttk.Frame(outer, style="Window.TFrame")
    header.pack(fill="x")
    titles = ttk.Frame(header, style="Window.TFrame")
    titles.pack(side="left", fill="x", expand=True)
    ttk.Label(titles, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
    status = ttk.Label(titles, text="Starting…", style="Window.Muted.TLabel")
    status.pack(anchor="w")

    server = OfficeServer(log)

    # The four things the app does, as the Wii shows its channels: rounded tiles.
    tiles = ttk.Frame(outer, style="Card.TFrame", padding=(8, 6))
    tiles.pack(fill="x", pady=(14, 0))
    for col in range(4):
        tiles.columnconfigure(col, weight=1, uniform="tile")
    buttons = tiles
    def open_browser(path=START_PAGE):
        if server.port:
            webbrowser.open(server.url(path))

    def tile(col, icon, text, command):
        btn = ttk.Button(buttons, text=text, style="Tile.TButton", state="disabled", command=command)
        doui.set_icon(btn, icon)
        btn.grid(row=0, column=col, sticky="nsew", padx=6, pady=6)
        return btn

    open_btn = tile(0, "office", "Open the Office", lambda: open_browser(START_PAGE))
    mass_btn = tile(1, "mass", "Open the Mass", lambda: open_browser("/cgi-bin/missa/missa.pl"))

    pdf_window = {}

    def make_pdf():
        existing = pdf_window.get("dialog")
        if existing and existing.win.winfo_exists():
            existing.win.lift()
            return
        typst = find_resource("typst", "typst.exe")
        if not typst:
            messagebox.showerror(APP_NAME, "The PDF typesetter (typst/typst.exe) is missing "
                                 "from this installation.")
            return
        import pdfdialog

        pdf_window["dialog"] = pdfdialog.PdfDialog(
            root, server.web_root, server.perl, server.libs, typst)

    pdf_btn = tile(2, "pdf", "Make a PDF…", make_pdf)

    def make_breviary():
        existing = pdf_window.get("breviary")
        if existing and existing.win.winfo_exists():
            existing.win.lift()
            return
        typst, dumper = find_resource("typst", "typst.exe"), locate_dumper()
        if not (typst and dumper):
            messagebox.showerror(APP_NAME, "The breviary builder (typst/typst.exe and "
                                 "engine_dump.pl) is missing from this installation.")
            return
        import pdfdialog

        pdf_window["breviary"] = pdfdialog.BreviaryDialog(
            root, server.web_root, server.perl, server.libs, typst, dumper)

    brev_btn = tile(3, "breviary", "Make a breviary…", make_breviary)

    def quit_app():
        status.config(text="Stopping…")
        root.update_idletasks()
        server.stop()
        root.destroy()

    ttk.Button(header, text="Quit", command=quit_app, style="Window.TButton").pack(
        side="right", anchor="n", pady=(6, 0))
    doui.mode_button(header, style="Window.TButton").pack(side="right", anchor="n", padx=(0, 8), pady=(6, 0))

    import doupdate

    def before_update():  # the Setup replaces these files: let go of them first
        status.config(text="Updating…")
        server.stop()
        release_app_mutex()

    logrow = ttk.Frame(outer, style="Window.TFrame")
    logrow.pack(fill="x", padx=8, pady=(14, 4))
    ttk.Label(logrow, text="Log", style="Window.Muted.TLabel").pack(side="left", anchor="s")
    updates = doupdate.UpdatePanel(logrow, root, log, before_update)
    updates.pack(side="right")
    logcard = ttk.Frame(outer, style="Card.TFrame", padding=(10, 8))
    logcard.pack(fill="both", expand=True)
    logbox = tk.Text(logcard, height=9, font=("Consolas", 8), relief="flat", wrap="none",
                     borderwidth=0, highlightthickness=0, padx=6, pady=4)
    doui.role(logbox, "log")
    logscroll = ttk.Scrollbar(logcard, orient="vertical", command=logbox.yview)
    logbox.configure(yscrollcommand=logscroll.set)
    logscroll.pack(side="right", fill="y")
    logbox.pack(side="left", fill="both", expand=True)
    logbox.configure(state="disabled")

    def drain():
        try:
            while True:
                line = messages.get_nowait()
                logbox.configure(state="normal")
                logbox.insert("end", line + "\n")
                logbox.see("end")
                logbox.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(150, drain)

    def boot():
        log("log file : %s" % log_path())
        try:
            server.start()
        except Exception as exc:  # surfaced in a dialog; the app is unusable
            log("ERROR: %s" % exc)
            status.config(text="Failed to start", style="Window.Error.TLabel")
            messagebox.showerror(APP_NAME, str(exc))
            return
        status.config(text="Running at %s" % server.url(""), style="Window.Ok.TLabel")
        open_btn.config(state="normal")
        mass_btn.config(state="normal")
        pdf_btn.config(state="normal")
        brev_btn.config(state="normal")
        # The browser opens when asked ("Open the Office"), not at every start.
        updates.start()  # only if its box is ticked

    root.protocol("WM_DELETE_WINDOW", quit_app)
    doui.present(root)
    root.after(150, drain)
    root.after(200, boot)
    root.mainloop()


def run_console():
    attach_console()

    def log(msg):
        write_log(str(msg))
        print(msg)

    server = OfficeServer(log)
    try:
        server.start()
    except Exception as exc:
        log("error: %s" % exc)
        return 1
    print("%s running. Press Ctrl+C to stop." % APP_NAME)
    webbrowser.open(server.url())
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        print("\nstopping")
        server.stop()
    return 0


def bundled_paths():
    """(web root, perl, extra perl libs, typst) as found for this installation."""
    web = locate_web()
    perl, bundled = locate_perl()
    libs = []
    perl_lib = find_resource("perl-lib")
    if perl_lib and not bundled:
        libs.append(perl_lib)
    return web, perl, libs, find_resource("typst", "typst.exe")


def locate_dumper():
    """engine_dump.pl: reads the breviary's parts through the engine."""
    return find_resource("engine_dump.pl")


def code_source():
    """Where the app's own code was loaded from: the app/ folder beside the exe
    (what build-exe.py --quick updates), the copies frozen in the exe, or the
    source tree."""
    here = os.path.dirname(os.path.abspath(__file__))
    if not getattr(sys, "frozen", False):
        return "source tree (%s)" % here
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.normcase(here) != os.path.normcase(os.path.join(exe_dir, "app")):
        return "built into the exe"
    files = [os.path.join(here, f) for f in os.listdir(here) if f.endswith(".py")]
    import datetime as dt

    newest = dt.datetime.fromtimestamp(max(os.path.getmtime(f) for f in files))
    return "app folder, %d files, newest %s" % (len(files), newest.strftime("%Y-%m-%d %H:%M"))


def run_make_breviary(argv):
    """DivinumOfficium.exe --make-breviary --parts ordinarium,psalterium -o breviary.pdf"""
    attach_console()
    import dobreviary

    web, perl, libs, typst = bundled_paths()
    return dobreviary.main(argv, web=web, perl=perl, typst=typst, dumper=locate_dumper(),
                           perl_libs=libs)


def run_make_pdf(argv):
    """DivinumOfficium.exe --make-pdf 2026-09-23 [2026-09-30] --hours Vespera -o out.pdf"""
    attach_console()
    import dopdf

    web, perl, libs, typst = bundled_paths()
    return dopdf.main(argv, web=web, perl=perl, typst=typst, perl_libs=libs)


def run_selftest():
    """Check every bundled piece in this installation and report each one."""
    attach_console()
    import datetime as dt
    import tempfile

    results = []

    def check(name, fn):
        try:
            detail = fn()
            results.append(True)
            line = "ok    %-22s %s" % (name, detail or "")
        except Exception as exc:  # report and keep going: that is the point
            results.append(False)
            line = "FAIL  %-22s %s" % (name, str(exc).splitlines()[0][:200])
        print(line)
        write_log("selftest: " + line)

    def need(value, missing):
        if not value:
            raise RuntimeError(missing)
        return value

    web, perl, libs, typst = bundled_paths()
    check("app code", code_source)
    check("breviary data", lambda: need(web, "web/ not found"))
    check("perl", lambda: need(perl, "perl not found"))
    check("typst", lambda: need(typst, "typst/typst.exe not found"))

    import dooffice
    import dopdf

    today = dt.date.today()
    state = {}

    def engine():
        state["engine"] = dooffice.Engine(web, perl, libs)
        day = state["engine"].day(dooffice.Request(today, "Vespera"))
        return "%s: %s" % (today, day.title)

    def dialog():
        import tkinter as tk

        import pdfdialog

        root = tk.Tk()
        root.withdraw()
        try:
            dlg = pdfdialog.PdfDialog(root, web, perl, libs, typst)
            root.update()
            return "%d versions, %d languages; estimate: %s" % (
                len(dlg.versions), len(dlg.languages), dlg.estimate.cget("text"))
        finally:
            for pending in root.tk.call("after", "info"):  # the window's polling timer
                root.tk.call("after", "cancel", pending)
            root.destroy()

    def pdf():
        out = os.path.join(tempfile.gettempdir(), "divinum-officium-selftest.pdf")
        reqs = dopdf.requests_for(today, today, ["Vespera", "Completorium"])
        dopdf.export(state["engine"], reqs, dopdf.Layout(), out, typst)
        from pypdf import PdfReader

        return "%d pages -> %s" % (len(PdfReader(out).pages), out)

    def breviary_dialog():
        import tkinter as tk

        import pdfdialog

        root = tk.Tk()
        root.withdraw()
        try:  # constructing only reads the saved choices; nothing is written
            dlg = pdfdialog.BreviaryDialog(root, web, perl, libs, typst,
                                           need(locate_dumper(), "engine_dump.pl not found"))
            root.update()
            return "parts %s; estimate: %s" % (", ".join(dlg.parts()), dlg.estimate.cget("text"))
        finally:
            for pending in root.tk.call("after", "info"):  # the window's polling timer
                root.tk.call("after", "cancel", pending)
            root.destroy()

    def breviary():
        import dobreviary
        from pypdf import PdfReader

        dumper = need(locate_dumper(), "engine_dump.pl not found")
        out = os.path.join(tempfile.gettempdir(), "divinum-officium-selftest-breviary.pdf")
        offices = dobreviary.build(["orationes", "commune"], "Rubrics 1960 - 1960", "Latin",
                                   "English", dopdf.Layout(new_page_per_day=False), out, perl,
                                   dumper, web, typst, only=["C4"], perl_libs=libs)
        return "%d office(s), %d pages -> %s" % (len(offices), len(PdfReader(out).pages), out)

    def ordinary():
        # The parts read from the engine's own offices (Ordinary, Psalter, seasons).
        import dobreviary
        from pypdf import PdfReader

        dumper = need(locate_dumper(), "engine_dump.pl not found")
        out = os.path.join(tempfile.gettempdir(), "divinum-officium-selftest-ordinary.pdf")
        offices = dobreviary.build(["ordinarium"], "Rubrics 1960 - 1960", "Latin", "English",
                                   dopdf.Layout(new_page_per_day=False), out, perl, dumper, web,
                                   typst, perl_libs=libs)
        return "%d hours, %d pages -> %s" % (len(offices), len(PdfReader(out).pages), out)

    check("office engine", engine)
    check("PDF dialog", dialog)
    check("PDF export", pdf)
    check("breviary dialog", breviary_dialog)
    check("breviary", breviary)
    check("breviary Ordinary", ordinary)

    def updates():
        import doupdate

        return doupdate.selftest()

    check("updates", updates)
    passed = all(results)
    print("SELFTEST %s" % ("PASSED" if passed else "FAILED"))
    write_log("selftest: %s" % ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


def main():
    hold_app_mutex()
    open_log()
    write_log("%s starting (frozen=%s)" % (APP_NAME, bool(getattr(sys, "frozen", False))))
    try:
        write_log("app code: %s" % code_source())
    except OSError:
        pass
    if "--make-pdf" in sys.argv:
        return run_make_pdf(sys.argv[sys.argv.index("--make-pdf") + 1:])
    if "--make-breviary" in sys.argv:
        return run_make_breviary(sys.argv[sys.argv.index("--make-breviary") + 1:])
    if "--selftest" in sys.argv:
        return run_selftest()
    if "--check-update" in sys.argv or "--update" in sys.argv:
        attach_console()
        import doupdate

        return doupdate.run_cli("--update" in sys.argv, before_install=release_app_mutex)
    if "--console" in sys.argv:
        return run_console()
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return run_console()
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
