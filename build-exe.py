#!/usr/bin/env python3
"""
Build a self-contained Divinum Officium .exe.

Two layouts:

  folder    DivinumOfficium/ -- a small exe beside perl/ and web/. Launches
            instantly. This is the one to use.
  onefile   A single .exe with everything embedded. One file to move around,
            but PyInstaller re-extracts the whole payload to %TEMP% on every
            launch, which costs real time at this payload size.

Usage:
    python build-exe.py                 # downloads Perl once, then caches it
    python build-exe.py --layout both
    python build-exe.py --refetch       # re-download and re-prune Perl
    python build-exe.py --quick         # only the changed app code, in seconds
    python build-exe.py --rollback      # undo the last --quick
    python build-exe.py --installer     # a Setup .exe of the folder build, to give away
    python build-exe.py --release --notes "What changed"
                                        # a Setup, signed, published on GitHub: installed
                                        # copies find it with "Check for updates"

The folder build runs the app's own code (DivinumOfficium.py, dooffice.py ...)
from DivinumOfficium/app/, through launcher.py, so --quick only copies the
files that changed there (and engine_dump.pl beside the exe). The app may stay
open; the update takes effect the next time it starts. A full build is needed
only when Perl, Typst, the breviary data, Python, launcher.py or a newly used
library changes -- --quick warns about the last. The single-file exe is not
touched by --quick.

Both write the corrections in data-fixes.txt into the app's copy of the
breviary data (see datafixes.py), so a fresh or updated repo/ cannot drop them.

The work directory defaults to a temp path rather than somewhere inside the
project, because staging is ~150 MB and this project tree sits in OneDrive.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

import datafixes
import doupdate

HERE = os.path.dirname(os.path.abspath(__file__))

# Strawberry Perl is used because it is relocatable: its @INC is computed
# relative to perl.exe, so the interpreter works from any directory.
PERL_URL = (
    "https://github.com/StrawberryPerl/Perl-Dist-Strawberry/releases/download/"
    "SP_54051_64bit/strawberry-perl-5.40.5.1-64bit-portable.zip"
)

# Of the 291 MB archive we want the interpreter and its core library. The
# `c/` tree (814 MB of MinGW toolchain) and everything else is dropped.
PERL_KEEP_PREFIXES = ("perl/bin/", "perl/lib/")
PERL_KEEP_VENDOR = (
    "perl/vendor/lib/CGI.pm",
    "perl/vendor/lib/CGI/",
    "perl/vendor/lib/URI.pm",
    "perl/vendor/lib/URI/",
    "perl/vendor/lib/HTML/Entities.pm",
)
# perl/bin is 21 MB of CPAN tooling; only these are needed to run a script.
PERL_BIN_KEEP = (
    "perl.exe",
    "wperl.exe",
    "perlglob.exe",
    "perl540.dll",
    "libgcc_s_seh-1.dll",
    "libstdc++-6.dll",
    "libwinpthread-1.dll",
)

APP_SOURCES = ("DivinumOfficium.py", "docgi.py", "dooffice.py", "dopdf.py", "pdfdialog.py",
               "dobreviary.py", "dopsalter.py", "dotranslate.py", "dovolumes.py", "doui.py",
               "doupdate.py")
# Plain files that travel beside the exe (not Python: PyInstaller would not find them).
APP_FILES = ("engine_dump.pl", "translations-en.json")
# The frozen entry point: runs APP_SOURCES from app/ beside the exe (see launcher.py).
LAUNCHER = "launcher.py"
# What the full build's app/ folder records: the modules the sources imported
# then, which PyInstaller therefore bundled -- a quick update importing anything
# else may need a full build.
BUNDLED_IMPORTS = "bundled-imports.txt"

# Typst typesets the PDFs. A single self-contained executable (Apache-2.0);
# its LICENSE and NOTICE ship beside it, as that licence asks.
TYPST_URL = (
    "https://github.com/typst/typst/releases/download/v0.15.1/"
    "typst-x86_64-pc-windows-msvc.zip"
)
TYPST_DIR = "typst-x86_64-pc-windows-msvc"
# Typst is built against the Visual C++ runtime, which Windows itself does not
# ship: a PC that never installed the VC++ Redistributable could not run it.
# A DLL beside an exe is the first place Windows looks, so a copy goes there.
VC_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll")

# The Setup .exe (--installer): Inno Setup 6 compiles installer.iss.
INSTALLER_SCRIPT = "installer.iss"
LICENSES = "licenses"
ISCC_PATHS = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
)

# Releases (--release): the Setup and a signed latest.json on GitHub (see doupdate.py).
# The private half of the signing key lives here, outside the project folder so
# it never reaches the repository. Without it no update can be published that
# installed copies accept, so keep a copy of it somewhere safe.
SIGNING_KEY = os.path.join(os.path.expanduser("~"), ".offline-officium", "release-signing-key.txt")
GH_PATHS = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
                 "GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe", "bin", "gh.exe"),
    r"C:\Program Files\GitHub CLI\gh.exe",
)


def log(msg):
    print("==> %s" % msg, flush=True)


def rmtree(path, attempts=6):
    """Delete a tree, working around Windows/OneDrive transient locks.

    Replacing a previous build inside a synced folder hits WinError 5 when the
    sync client is still touching a directory, so clear read-only attributes
    and retry with a short backoff rather than failing the build.
    """
    if not os.path.isdir(path):
        return

    import stat
    import time

    def onexc(func, target, exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            raise exc

    last = None
    for attempt in range(attempts):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=onexc)
            else:  # onexc is 3.12's; an older Python on PATH has only onerror
                shutil.rmtree(path, onerror=lambda func, target, info: onexc(func, target, info[1]))
            return
        except (OSError, PermissionError) as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise SystemExit(
        "could not remove %s after %d attempts: %s\n"
        "Something is holding it open -- close the app, or let OneDrive finish "
        "syncing, and run the build again." % (path, attempts, last)
    )


def set_aside(path, attempts=6):
    """Rename a folder out of the way, or stop with nothing changed.

    Windows will not rename a folder while any file inside it is open, so this
    fails whole -- where deleting in place would get halfway, leave a broken
    app behind, and only then find the file the running app holds.
    """
    import time

    aside = path + ".old"
    rmtree(aside)
    last = None
    for attempt in range(attempts):
        try:
            os.rename(path, aside)
            return aside
        except OSError as exc:  # the app is open, or OneDrive is still at it
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise SystemExit(
        "%s is in use (%s).\nClose the app (or let OneDrive finish syncing) and run the "
        "build again. Nothing was changed." % (path, last))


def check_not_in_use(path):
    """Fail before the long PyInstaller run if the app folder cannot be replaced."""
    if os.path.isdir(path):
        os.rename(set_aside(path), path)


def du(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def mb(n):
    return "%.1f MB" % (n / 1048576.0)


# --------------------------------------------------------------------------


def fetch_perl(cache_dir, force=False):
    """Download and prune Strawberry Perl into <cache_dir>/perl."""
    os.makedirs(cache_dir, exist_ok=True)
    target = os.path.join(cache_dir, "perl")
    if os.path.isdir(target) and not force:
        log("reusing cached Perl at %s (%s)" % (target, mb(du(target))))
        return target

    archive = os.path.join(cache_dir, "strawberry-portable.zip")
    if not os.path.isfile(archive) or force:
        log("downloading Strawberry Perl (~291 MB)")
        with urllib.request.urlopen(PERL_URL) as src, open(archive, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 512)
    log("archive: %s" % mb(os.path.getsize(archive)))

    if os.path.isdir(target):
        shutil.rmtree(target)
    extract_root = os.path.join(cache_dir, "_extract")
    if os.path.isdir(extract_root):
        shutil.rmtree(extract_root)

    def wanted(name):
        if name.startswith(PERL_KEEP_PREFIXES):
            return True
        return any(name == k or name.startswith(k) for k in PERL_KEEP_VENDOR)

    log("extracting interpreter + core library only")
    with zipfile.ZipFile(archive) as zf:
        members = [i for i in zf.infolist() if wanted(i.filename)]
        zf.extractall(extract_root, members=members)

    shutil.move(os.path.join(extract_root, "perl"), target)
    shutil.rmtree(extract_root, ignore_errors=True)

    # Prune: CPAN tooling and 9.8 MB of pod documentation.
    bin_dir = os.path.join(target, "bin")
    for name in os.listdir(bin_dir):
        if name not in PERL_BIN_KEEP:
            path = os.path.join(bin_dir, name)
            shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
    shutil.rmtree(os.path.join(target, "lib", "pods"), ignore_errors=True)

    log("pruned Perl runtime: %s" % mb(du(target)))
    verify_perl(target)
    return target


def fetch_typst(cache_dir, force=False):
    """Download Typst once into <cache_dir>; return the folder holding typst.exe."""
    os.makedirs(cache_dir, exist_ok=True)
    target = os.path.join(cache_dir, TYPST_DIR)
    exe = os.path.join(target, "typst.exe")
    if os.path.isfile(exe) and not force:
        log("reusing cached Typst at %s" % target)
    else:
        archive = os.path.join(cache_dir, "typst.zip")
        log("downloading Typst (~21 MB)")
        with urllib.request.urlopen(TYPST_URL) as src, open(archive, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 512)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(cache_dir)
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit("bundled typst failed its self-test:\n%s" % out.stderr)
    log("typst self-test: %s" % out.stdout.strip())
    return target


def stage_typst(typst_dir, dest):
    rmtree(dest)
    os.makedirs(dest)
    for name in ("typst.exe", "LICENSE", "NOTICE"):
        src = os.path.join(typst_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))
    copy_vc_runtime(sys.base_prefix, dest)  # the copies PyInstaller bundles with Python


def copy_vc_runtime(src_dir, dest):
    for name in VC_RUNTIME:
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            raise SystemExit("error: %s is not in %s, and Typst needs it" % (name, src_dir))
        shutil.copy2(src, os.path.join(dest, name))


def verify_perl(perl_root):
    exe = os.path.join(perl_root, "bin", "perl.exe")
    probe = (
        "use CGI; use CGI::Cookie; use CGI::Carp; use Storable; use JSON::PP; "
        "use POSIX; use Time::Local; use File::Basename; "
        'print "perl $] + CGI $CGI::VERSION ok\\n";'
    )
    out = subprocess.run(
        [exe, "-e", probe], capture_output=True, text=True, timeout=120
    )
    if out.returncode != 0:
        raise SystemExit("bundled perl failed its self-test:\n%s" % out.stderr)
    log("perl self-test: %s" % out.stdout.strip())


# --------------------------------------------------------------------------


def internalize_urls(web_dir):
    """Point absolute divinumofficium.com URLs at this local instance.

    The project's Dockerfile does the same thing. Nothing needed to render a
    page is loaded over the network, so this only affects links (the Help
    pages, and the absolute URLs ical.pl writes into exported calendars) --
    but an offline copy should not send you to the public site. Idempotent.
    """
    import re

    pattern = re.compile(r"https?://(?:www\.)?divinumofficium\.com/?")
    changed = 0
    for root, _dirs, files in os.walk(web_dir):
        for name in files:
            if not name.lower().endswith((".html", ".htm", ".txt", ".pl", ".pm", ".js", ".json")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
                    text = fh.read()
            except OSError:
                continue
            if "divinumofficium.com" not in text:
                continue
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.write(pattern.sub("/", text))
            changed += 1
    if changed:
        log("internalized divinumofficium.com URLs in %d file(s)" % changed)


# Hosts that would be contacted at page-render time if their markup is left in.
TRACKER_HOSTS = (
    "googletagmanager.com",
    "google-analytics.com",
    "googletagservices.com",
    "googlesyndication.com",
    "doubleclick.net",
)


def strip_trackers(web_dir):
    """Remove third-party tracking markup from the bundled pages.

    Two of the Help > Rubrics documents (SacrosanctumConcilium.html and
    SacramLiturgiam.html) carry a live Google Tag Manager container --
    inherited from wherever those Vatican texts were originally saved from.
    Nothing in the office, Mass or Ordo loads anything off-box, but these two
    pages would try to reach googletagmanager.com. Offline that fails
    harmlessly; online it is a privacy leak in a copy that is supposed to be
    self-contained. Note the URLs are protocol-relative (`//host/...`), so a
    search for `https://` does not find them. Idempotent.
    """
    import re

    # Comment-delimited container first, then any surviving script/noscript
    # element that mentions a tracker host.
    block = re.compile(
        r"<!--\s*Google Tag Manager.*?<!--\s*End Google Tag Manager[^>]*-->",
        re.DOTALL | re.IGNORECASE,
    )
    element = re.compile(
        r"<(script|noscript)\b[^>]*>(?:(?!</\1>).)*?(?:%s).*?</\1>"
        % "|".join(h.replace(".", r"\.") for h in TRACKER_HOSTS),
        re.DOTALL | re.IGNORECASE,
    )

    cleaned = []
    for root, _dirs, files in os.walk(web_dir):
        for name in files:
            if not name.lower().endswith((".html", ".htm")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
                    text = fh.read()
            except OSError:
                continue
            if not any(h in text for h in TRACKER_HOSTS):
                continue
            new = element.sub("", block.sub("", text))
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.write(new)
            cleaned.append(os.path.relpath(path, web_dir))
            if any(h in new for h in TRACKER_HOSTS):
                raise SystemExit(
                    "error: tracker markup survived stripping in %s" % path
                )
    if cleaned:
        log("stripped tracking markup from: %s" % ", ".join(cleaned))


def stage_web(repo_web, dest, keep_pdfs=False):
    """Copy the breviary data, skipping the 92.8 MB of reference PDFs."""
    rmtree(dest)

    skipped = [0]

    def ignore(directory, names):
        drop = set()
        for name in names:
            if not keep_pdfs and name.lower().endswith(".pdf"):
                try:
                    skipped[0] += os.path.getsize(os.path.join(directory, name))
                except OSError:
                    pass
                drop.add(name)
        return drop

    log("staging breviary data")
    shutil.copytree(repo_web, dest, ignore=ignore)
    if skipped[0]:
        log("omitted %s of reference PDFs (Help > Rubrics links)" % mb(skipped[0]))
    internalize_urls(dest)
    strip_trackers(dest)
    log("web payload: %s" % mb(du(dest)))
    return dest


def apply_data_fixes(web):
    """Write the corrections in data-fixes.txt into a web/ tree (see datafixes.py),
    so the app has them whatever state repo/ is in; how many files changed."""
    n = 0
    for fix, state in datafixes.apply_all(web):
        if state == "applied":
            n += 1
            log("data fix: %s" % fix)
        elif state != "in place":
            log("warning: data fix %s not applied (%s)" % (fix, state))
    return n


# Trees that travel beside the exe: the interpreter, the breviary, the typesetter.
BUNDLED = ("perl", "web", "typst")


def run_pyinstaller(work, stage, layout, icon):
    dist = os.path.join(work, "dist-%s" % layout)
    build = os.path.join(work, "pyi-%s" % layout)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", "DivinumOfficium",
        "--distpath", dist,
        "--workpath", build,
        "--specpath", work,
        "--paths", stage,
    ]
    if icon and os.path.isfile(icon):
        cmd += ["--icon", icon]
    if layout == "onefile":
        cmd += ["--onefile"]
        for name in BUNDLED + APP_FILES:
            cmd += ["--add-data", "%s%s%s" % (os.path.join(stage, name), os.pathsep,
                                              name if name in BUNDLED else ".")]
    else:
        cmd += ["--onedir"]
    cmd.append(os.path.join(stage, LAUNCHER))

    log("running PyInstaller (%s)" % layout)
    proc = subprocess.run(cmd, cwd=stage)
    if proc.returncode != 0:
        raise SystemExit("PyInstaller failed (%s)" % layout)
    return dist


def assemble(dist, stage, layout, out_dir):
    """Move the build result to out_dir, adding data beside the exe for folder mode."""
    if layout == "onefile":
        target = os.path.join(out_dir, "DivinumOfficium.exe")
        if os.path.exists(target):
            os.remove(target)
        os.makedirs(out_dir, exist_ok=True)
        shutil.move(os.path.join(dist, "DivinumOfficium.exe"), target)
        log("onefile exe: %s (%s)" % (target, mb(os.path.getsize(target))))
        return target

    target = os.path.join(out_dir, "DivinumOfficium")
    old = set_aside(target) if os.path.isdir(target) else None
    if old:
        rmtree(old)
    os.makedirs(out_dir, exist_ok=True)
    shutil.move(os.path.join(dist, "DivinumOfficium"), target)
    # Data lives next to the exe rather than inside _internal: nothing has to
    # be unpacked at launch, and the breviary text stays browsable on disk.
    for name in BUNDLED:
        shutil.copytree(os.path.join(stage, name), os.path.join(target, name))
    for name in APP_FILES:
        shutil.copy2(os.path.join(stage, name), os.path.join(target, name))
    # The app's code, run from here by the launcher: what --quick updates.
    app = os.path.join(target, "app")
    os.makedirs(app)
    for name in APP_SOURCES:
        shutil.copy2(os.path.join(stage, name), os.path.join(app, name))
    with open(os.path.join(app, BUNDLED_IMPORTS), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(imports_of(os.path.join(stage, n) for n in APP_SOURCES))) + "\n")
    log("app folder: %s (%s)" % (target, mb(du(target))))
    return target


# --------------------------------------------------------------------------
# quick updates: the app's code only, into an existing folder build


def imports_of(paths):
    """The top-level modules the given Python files import."""
    import ast

    found = set()
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module.split(".")[0])
    return found


def same_bytes(a, b):
    if not os.path.isfile(b) or os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def app_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DivinumOfficium.exe"],
                             capture_output=True, text=True).stdout
    except OSError:
        return False
    return "DivinumOfficium.exe" in out


def quick_target(out_dir):
    target = os.path.join(out_dir, "DivinumOfficium")
    app = os.path.join(target, "app")
    if not os.path.isdir(app):
        raise SystemExit("error: %s has no app/ folder -- it was built before quick updates "
                         "existed. Run a full build once: python build-exe.py" % target)
    return target, app


def quick_update(out_dir):
    """Copy the app sources that changed (and engine_dump.pl) into the folder build.

    Nothing is copied unless every source compiles. The files replaced are kept
    in app/previous/, for --rollback. The app may be open: Windows does not lock
    these files, and the update takes effect the next time the app starts.
    The corrections in data-fixes.txt are written into the app's data as well
    (--rollback does not take those back out).
    """
    target, app = quick_target(out_dir)
    fixed = apply_data_fixes(os.path.join(target, "web"))
    for name in APP_SOURCES:
        path = os.path.join(HERE, name)
        with open(path, encoding="utf-8") as fh:
            try:
                compile(fh.read(), path, "exec")
            except SyntaxError as exc:
                raise SystemExit("error: nothing updated -- %s does not compile: %s" % (name, exc))
    pairs = [(n, os.path.join(app, n)) for n in APP_SOURCES] + \
            [(n, os.path.join(target, n)) for n in APP_FILES]
    changed = [(n, dest) for n, dest in pairs if not same_bytes(os.path.join(HERE, n), dest)]
    if not changed:
        if fixed:
            log("done -- %d data fix(es); the app's code was already up to date" % fixed)
        else:
            log("the app is already up to date (%s)" % app)
        return

    bundled = os.path.join(app, BUNDLED_IMPORTS)
    if os.path.isfile(bundled):
        with open(bundled, encoding="utf-8") as fh:
            known = set(fh.read().split())
        new = imports_of(os.path.join(HERE, n) for n in APP_SOURCES) - known \
            - {os.path.splitext(n)[0] for n in APP_SOURCES}
        if new:
            log("warning: the code now imports %s, which the full build did not bundle; "
                "if the app fails to start, run a full build" % ", ".join(sorted(new)))

    previous = os.path.join(app, "previous")
    rmtree(previous)
    os.makedirs(previous)
    for name, dest in changed:
        if os.path.isfile(dest):
            shutil.copy2(dest, os.path.join(previous, name))
        tmp = dest + ".new"
        shutil.copy2(os.path.join(HERE, name), tmp)
        os.replace(tmp, dest)
        log("updated %s" % os.path.relpath(dest, out_dir))
    rmtree(os.path.join(app, "__pycache__"))
    if app_running():
        log("the app is open: the update takes effect the next time it starts")
    log("done -- %d file(s); python build-exe.py --rollback puts the previous ones back"
        % len(changed))


def rollback(out_dir):
    """Swap the files the last --quick replaced back in (running it again re-applies them)."""
    target, app = quick_target(out_dir)
    previous = os.path.join(app, "previous")
    names = sorted(os.listdir(previous)) if os.path.isdir(previous) else []
    if not names:
        raise SystemExit("error: nothing to roll back (%s is empty)" % previous)
    for name in names:
        dest = os.path.join(target if name in APP_FILES else app, name)
        saved = os.path.join(previous, name)
        tmp = saved + ".swap"
        if os.path.isfile(dest):
            shutil.copy2(dest, tmp)
        shutil.copy2(saved, dest)
        if os.path.isfile(tmp):
            os.replace(tmp, saved)
        log("rolled back %s" % os.path.relpath(dest, out_dir))
    rmtree(os.path.join(app, "__pycache__"))
    log("done -- run --rollback again to return to the newer files")


# --------------------------------------------------------------------------
# the Setup .exe: the folder build, packed to install on another PC


def find_iscc():
    return shutil.which("ISCC") or next((p for p in ISCC_PATHS if os.path.isfile(p)), None)


def wizard_images(target, work):
    """The app's icon for the Setup wizard's two pictures, from the favicon, at
    the sizes for 100, 125, 150 and 200% scaling: the small one in each page's
    corner and the large one beside the last page, on a clear ground. Returns
    the ISCC defines, none without Pillow (Setup then keeps its own pictures)."""
    try:
        from PIL import Image
    except ImportError:
        return []
    icon = Image.open(os.path.join(target, "web", "favicon.ico")).convert("RGBA")
    os.makedirs(work, exist_ok=True)
    small, large = [], []
    for scale in (1, 1.25, 1.5, 2):
        side = round(55 * scale)
        path = os.path.join(work, "wizard-small-%d.png" % side)
        icon.resize((side, side), Image.LANCZOS).save(path)
        small.append(path)
        w, h, side = round(164 * scale), round(314 * scale), round(132 * scale)
        page = Image.new("RGBA", (w, h))
        page.alpha_composite(icon.resize((side, side), Image.LANCZOS), ((w - side) // 2, (h - side) // 2))
        path = os.path.join(work, "wizard-large-%d.png" % w)
        page.save(path)
        large.append(path)
    return ["/DWizardSmall=%s" % ",".join(small), "/DWizardLarge=%s" % ",".join(large)]


def next_version(out_dir):
    """Today's date, YYYY.MM.DD; a second release the same day adds .2, .3 ...
    (an update must compare higher than the copy it replaces)."""
    import datetime as dt

    version = dt.date.today().strftime("%Y.%m.%d")
    try:
        with open(os.path.join(out_dir, doupdate.MANIFEST), encoding="utf-8") as fh:
            last = json.load(fh)["version"]
    except (OSError, ValueError, KeyError):
        return version
    if last == version or last.startswith(version + "."):
        return "%s.%d" % (version, (int(last.split(".")[3]) if last.count(".") == 3 else 1) + 1)
    return version


def make_installer(out_dir):
    """Pack the folder build as it stands into DivinumOfficium-Setup-<version>.exe
    (see installer.iss), so run a full build or --quick first. A build made
    before Typst's runtime travelled with it gets it here. Returns the Setup's
    path and its version, which the app finds in version.txt beside its exe."""
    target, _app = quick_target(out_dir)
    iscc = find_iscc()
    if not iscc:
        raise SystemExit("error: making the installer needs Inno Setup 6 "
                         "(free, from https://jrsoftware.org/isdl.php)")
    typst = os.path.join(target, "typst")
    if not all(os.path.isfile(os.path.join(typst, n)) for n in VC_RUNTIME):
        copy_vc_runtime(os.path.join(target, "_internal"), typst)
        log("added the Visual C++ runtime beside typst.exe")
    version = next_version(out_dir)
    with open(os.path.join(target, "version.txt"), "w", encoding="utf-8") as fh:
        fh.write(version + "\n")
    numeric = (version.split(".") + ["0"])[:4]
    cmd = [iscc, "/Q",
           "/DSource=%s" % target,
           "/DOutputDir=%s" % out_dir,
           "/DLicenses=%s" % os.path.join(HERE, LICENSES),
           "/DAppVersion=%s" % version,
           "/DNumericVersion=%s" % ".".join(str(int(n)) for n in numeric)] \
        + wizard_images(target, os.path.join(tempfile.gettempdir(), "do-build", "installer")) \
        + [os.path.join(HERE, INSTALLER_SCRIPT)]
    log("packing %s into a Setup, version %s (a few minutes)" % (mb(du(target)), version))
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("Inno Setup failed")
    setup = os.path.join(out_dir, "DivinumOfficium-Setup-%s.exe" % version)
    log("installer: %s (%s)" % (setup, mb(os.path.getsize(setup))))
    return setup, version


# --------------------------------------------------------------------------
# releases: the Setup and a signed latest.json, published on GitHub


def new_signing_key():
    """Make the release key, once: the private half in SIGNING_KEY, the public
    half written into doupdate.py's PUBLIC_KEY (which the app then checks
    releases against, from its next build on)."""
    import re
    import secrets

    if os.path.exists(SIGNING_KEY):
        raise SystemExit("error: %s exists already. A new key would make every installed copy "
                         "refuse the next update; delete that file first if you mean it." % SIGNING_KEY)
    secret = secrets.token_bytes(32)
    public = doupdate.public_key(secret).hex()
    os.makedirs(os.path.dirname(SIGNING_KEY), exist_ok=True)
    with open(SIGNING_KEY, "w", encoding="utf-8") as fh:
        fh.write(secret.hex() + "\n")
    path = os.path.join(HERE, "doupdate.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    source = re.sub(r'^PUBLIC_KEY = "[0-9a-f]*"', 'PUBLIC_KEY = "%s"' % public, source, count=1, flags=re.M)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    log("signing key: %s -- keep a copy of it somewhere safe" % SIGNING_KEY)
    log("public key %s written into doupdate.py" % public)


def signing_key():
    try:
        with open(SIGNING_KEY, encoding="utf-8") as fh:
            secret = bytes.fromhex(fh.read().strip())
    except (OSError, ValueError):
        raise SystemExit("error: no release signing key at %s (python build-exe.py "
                         "--new-signing-key makes one, for a new project only)" % SIGNING_KEY)
    if doupdate.public_key(secret).hex() != doupdate.PUBLIC_KEY:
        raise SystemExit("error: the signing key at %s does not match doupdate.PUBLIC_KEY, so "
                         "installed copies would refuse what it signs" % SIGNING_KEY)
    return secret


def find_gh():
    gh = shutil.which("gh") or next((p for p in GH_PATHS if os.path.isfile(p)), None)
    if not gh:
        raise SystemExit("error: publishing a release needs the GitHub CLI: winget install GitHub.cli")
    if subprocess.run([gh, "auth", "status"], capture_output=True).returncode != 0:
        raise SystemExit("error: the GitHub CLI is not signed in: gh auth login --web")
    return gh


def release(out_dir, notes):
    """Make the Setup, sign latest.json for it, and publish both as a GitHub
    release, tagged v<version>, which installed copies then find."""
    import hashlib

    secret, gh = signing_key(), find_gh()  # before the minutes of packing, not after
    git = subprocess.run(["git", "status", "--porcelain", "--ignore-submodules"], cwd=HERE,
                         capture_output=True, text=True)
    if git.returncode == 0 and git.stdout.strip():
        log("warning: there are changes not committed to git; the release is made from "
            "the app as it is built here, but GitHub's code will not show them")
    setup, version = make_installer(out_dir)
    name = os.path.basename(setup)
    digest = hashlib.sha256()
    with open(setup, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    manifest = {
        "version": version,
        "setup": name,
        "url": "https://github.com/%s/releases/download/v%s/%s" % (doupdate.REPO, version, name),
        "size": os.path.getsize(setup),
        "sha256": digest.hexdigest(),
        "notes": notes or "",
    }
    manifest["signature"] = doupdate.sign(secret, doupdate.signed_bytes(manifest)).hex()
    assert doupdate.verify(bytes.fromhex(doupdate.PUBLIC_KEY), doupdate.signed_bytes(manifest),
                           bytes.fromhex(manifest["signature"]))
    # gh names an upload after its file, so latest.json is written as such in a
    # folder of its own, and kept in out_dir (next_version reads it) once published.
    upload = os.path.join(tempfile.mkdtemp(prefix="do-release-"), doupdate.MANIFEST)
    with open(upload, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    log("publishing v%s on github.com/%s" % (version, doupdate.REPO))
    if subprocess.run([gh, "release", "create", "v%s" % version, setup, upload,
                       "--repo", doupdate.REPO, "--title", "Divinum Officium %s" % version,
                       "--notes", notes or "Version %s." % version]).returncode != 0:
        raise SystemExit("error: GitHub did not take the release (the Setup is at %s)" % setup)
    shutil.copy2(upload, os.path.join(out_dir, doupdate.MANIFEST))
    log("released %s: %s" % (version, doupdate.RELEASES_PAGE))


# --------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", choices=("folder", "onefile", "both"), default="folder")
    ap.add_argument("--repo", default=os.path.join(HERE, "repo"),
                    help="the divinum-officium checkout")
    ap.add_argument("--out", default=os.path.join(HERE, "dist"),
                    help="where to put the finished build")
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "do-build"),
                    help="staging/scratch directory (kept out of the project tree)")
    ap.add_argument("--perl-src", default=None,
                    help="an already-pruned perl/ tree to use instead of downloading")
    ap.add_argument("--refetch", action="store_true",
                    help="re-download Perl and Typst, ignoring the cache")
    ap.add_argument("--keep-pdfs", action="store_true",
                    help="include the 92.8 MB of Help > Rubrics PDFs")
    ap.add_argument("--skip-stage", action="store_true",
                    help="reuse the existing staged perl/ and web/ and only refresh "
                         "the app sources (much faster when iterating on the launcher)")
    ap.add_argument("--quick", action="store_true",
                    help="copy only the changed app code into the existing folder build "
                         "(seconds; the app may stay open)")
    ap.add_argument("--rollback", action="store_true",
                    help="put back the files the last --quick replaced")
    ap.add_argument("--installer", action="store_true",
                    help="pack the folder build into a Setup .exe (Inno Setup 6); "
                         "with --quick, after the update")
    ap.add_argument("--release", action="store_true",
                    help="make the Setup, sign it and publish it on GitHub, where installed "
                         "copies find it; with --quick, after the update")
    ap.add_argument("--notes", default="",
                    help="with --release: what changed, shown in the app when it offers the update")
    ap.add_argument("--new-signing-key", action="store_true",
                    help="make the key releases are signed with (once, for a new project)")
    args = ap.parse_args()

    if args.rollback:
        return rollback(args.out)
    if args.new_signing_key:
        return new_signing_key()
    if args.quick or args.installer or args.release:
        if args.quick:
            quick_update(args.out)
        if args.release:
            release(args.out, args.notes)
        elif args.installer:
            make_installer(args.out)
        return

    repo_web = os.path.join(args.repo, "web")
    if not os.path.isdir(os.path.join(repo_web, "cgi-bin")):
        raise SystemExit("error: %s is not a divinum-officium checkout" % args.repo)

    os.makedirs(args.work, exist_ok=True)
    stage = os.path.join(args.work, "stage")
    os.makedirs(stage, exist_ok=True)

    staged_perl = os.path.join(stage, "perl")
    staged_web = os.path.join(stage, "web")

    reuse = args.skip_stage and os.path.isdir(staged_perl) and os.path.isdir(staged_web)
    if args.skip_stage and not reuse:
        raise SystemExit("error: --skip-stage given but %s has no staged build yet" % stage)

    if reuse:
        log("reusing staged payload at %s (%s)" % (stage, mb(du(stage))))
        verify_perl(staged_perl)
        internalize_urls(staged_web)
        strip_trackers(staged_web)
    else:
        # 1. Perl
        if args.perl_src:
            perl_root = os.path.abspath(args.perl_src)
            verify_perl(perl_root)
        else:
            perl_root = fetch_perl(os.path.join(args.work, "perl-cache"),
                                   force=args.refetch)
        if os.path.abspath(perl_root) != os.path.abspath(staged_perl):
            rmtree(staged_perl)
            log("staging perl runtime")
            shutil.copytree(perl_root, staged_perl)

        # 2. Breviary data
        stage_web(repo_web, staged_web, keep_pdfs=args.keep_pdfs)
    apply_data_fixes(staged_web)

    # 3. The typesetter (small enough to refresh every time)
    stage_typst(fetch_typst(os.path.join(args.work, "typst-cache"), force=args.refetch),
                os.path.join(stage, "typst"))

    # 4. App sources, the launcher, and the Perl dumper the breviary builder runs
    for name in APP_SOURCES + APP_FILES + (LAUNCHER,):
        shutil.copy2(os.path.join(HERE, name), os.path.join(stage, name))

    icon = os.path.join(stage, "web", "favicon.ico")
    log("staged total: %s" % mb(du(stage)))

    layouts = ("folder", "onefile") if args.layout == "both" else (args.layout,)
    if "folder" in layouts:
        check_not_in_use(os.path.join(args.out, "DivinumOfficium"))
    results = []
    for layout in layouts:
        dist = run_pyinstaller(args.work, stage, layout, icon)
        results.append(assemble(dist, stage, layout, args.out))

    print()
    log("done")
    for r in results:
        print("    %s" % r)


if __name__ == "__main__":
    main()
