#!/usr/bin/env python3
"""
Local corrections to the Divinum Officium data, kept where git cannot lose them.

repo/ is a copy of the GitHub project: a fresh download, `git checkout`, or
`git stash` puts back the project's own text, and every edit made in it goes
with it. So the corrections live in data-fixes.txt, beside this script, and
are written into the data from there:

    python datafixes.py            # apply them to repo/web and to the built app
    python datafixes.py --check    # only report; exit status 1 if any is missing
    python datafixes.py --pull     # update repo/ from GitHub, keeping the fixes

A full build (build-exe.py) and build-exe.py --quick also apply them to the
app's own copy of the data, whatever state repo/ is in.

Each fix replaces the text of one section of one file (paths from web/www/horas):

    == Latin/Sancti/04-23.txt [Lectio94]
    why: the Latin file had the English lesson
    replaces: 1f0e6f8a9c3b    (added by this script: which text of the project's it replaced)
    ---
    the section's new text, up to the next "== " line

When the project changes a section a fix replaces (perhaps fixing it itself),
--check and --pull say so, and the fix can be reviewed or dropped. A fix with
the note "adds:" may add its section to a file that lacks it (at the end); its
"replaces: none" records that the project had no such section.

Files the project does not have at all live in data-additions/, in the same
folders as under web/www/horas (data-additions/English-Coverdale/... is the
English-Coverdale language), and are copied in whole. git leaves them alone:
they are not the project's files.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXES = os.path.join(HERE, "data-fixes.txt")
ADDITIONS = os.path.join(HERE, "data-additions")
REPO = os.path.join(HERE, "repo")
APP_WEB = os.path.join(HERE, "dist", "DivinumOfficium", "web")
HORAS = ("www", "horas")  # the data's folder, under a web/ root

_ENTRY = re.compile(r"^== (.+?\.(?:txt|dialog)) (\[.*\S)\s*$")
_SECTION = re.compile(r"^\[[^\]\n]+\]")  # a section header, as the engine reads one


class Fix:
    def __init__(self, path, section):
        self.path, self.section, self.notes, self.body = path, section, {}, []

    def __repr__(self):
        return "%s %s" % (self.path, self.section)


def load(path=FIXES):
    fixes, fix, in_body = [], None, False
    if not os.path.isfile(path):
        return fixes
    with open(path, encoding="utf-8") as fh:
        for line in fh.read().splitlines():
            m = _ENTRY.match(line)
            if m:
                fix, in_body = Fix(m.group(1), m.group(2)), False
                fixes.append(fix)
            elif fix is None:
                continue  # the file's own comments
            elif in_body:
                fix.body.append(line)
            elif line.strip() == "---":
                in_body = True
            elif ":" in line:
                k, v = line.split(":", 1)
                fix.notes[k.strip()] = v.strip()
    for fix in fixes:
        while fix.body and not fix.body[-1].strip():
            fix.body.pop()
    return fixes


def _split(text, section):
    """(lines, header index, end index, trailing blank lines) of a section, or None."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == section), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if _SECTION.match(lines[j])), len(lines))
    blank = 0
    while end - blank - 1 > start and not lines[end - blank - 1].strip():
        blank += 1
    return lines, start, end, blank


def section_text(text, section):
    s = _split(text, section)
    if s is None:
        return None
    lines, start, end, blank = s
    return "\n".join(lines[start + 1:end - blank])


def digest(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def _read(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    crlf = "\r\n" in text
    return text.replace("\r\n", "\n"), bom, crlf


def _write(path, text, bom, crlf):
    data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
    tmp = path + ".fix"
    with open(tmp, "wb") as fh:
        fh.write((b"\xef\xbb\xbf" if bom else b"") + data)
    for attempt in range(20):  # OneDrive holds a file it has just seen written, for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def patched(text, fix):
    """The text with the fix in it: its section's text replaced, or (a fix that
    adds) the section added at the end; None if the file has no such section."""
    s = _split(text, fix.section)
    if s is None:
        if "adds" not in fix.notes:
            return None
        return text.rstrip("\n") + "\n\n" + "\n".join([fix.section] + fix.body) + "\n"
    lines, start, end, blank = s
    new = lines[:start + 1] + fix.body + lines[end - blank:]
    if end == len(lines) and not blank:
        new.append("")  # the file's last line keeps its newline
    return "\n".join(new)


def apply_one(web, fix, write=True):
    """'applied', 'in place', 'not applied' (write=False), 'no file' or 'no section'."""
    path = os.path.join(web, *HORAS, *fix.path.split("/"))
    if not os.path.isfile(path):
        # A fix that adds may start a file the project lacks: an English file
        # holding only that section, the rest coming from the Latin, as the
        # engine layers a language over the Latin section by section.
        latin = os.path.join(web, *HORAS, "Latin", *fix.path.split("/")[1:])
        if "adds" not in fix.notes or not os.path.isfile(latin):
            return "no file"
        if not write:
            return "not applied"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, "\n".join([fix.section] + fix.body) + "\n", False, False)
        return "applied"
    text, bom, crlf = _read(path)
    s = _split(text, fix.section)
    if s is not None:
        lines, start, end, blank = s
        if lines[start + 1:end - blank] == fix.body:
            return "in place"
    new = patched(text, fix)
    if new is None:
        return "no section"
    if not write:
        return "not applied"
    _write(path, new, bom, crlf)
    return "applied"


class Addition:
    """One folder of data-additions/: files copied whole into the data."""

    def __init__(self, folder):
        self.folder = folder
        root = os.path.join(ADDITIONS, folder)
        self.files = sorted(os.path.relpath(os.path.join(d, f), ADDITIONS)
                            for d, _dirs, names in os.walk(root) for f in names)

    def __repr__(self):
        return "%s/ (%d files)" % (self.folder, len(self.files))


def additions():
    if not os.path.isdir(ADDITIONS):
        return []
    return [Addition(f) for f in sorted(os.listdir(ADDITIONS))
            if os.path.isdir(os.path.join(ADDITIONS, f))]


def _same_file(a, b):
    if not os.path.isfile(b) or os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def add_one(web, addition, write=True):
    """'applied', 'in place' or 'not applied' (write=False)."""
    stale = [rel for rel in addition.files
             if not _same_file(os.path.join(ADDITIONS, rel), os.path.join(web, *HORAS, rel))]
    if not stale:
        return "in place"
    if not write:
        return "not applied"
    for rel in stale:
        dest = os.path.join(web, *HORAS, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(os.path.join(ADDITIONS, rel), dest + ".fix")
        os.replace(dest + ".fix", dest)
    return "applied"


def apply_all(web, write=True, fixes=None):
    """Apply every fix and addition to one web/ tree; a report line for each."""
    fixes = load() if fixes is None else fixes
    return [(fix, apply_one(web, fix, write)) for fix in fixes] + \
        [(a, add_one(web, a, write)) for a in additions()]


# -- the project's own text, from git ---------------------------------------


def _git(*args, check=True):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                          encoding="utf-8", check=check)


def upstream_file(fix):
    """The project's text of the fix's file (the checkout's last commit), or None."""
    rel = "/".join(("web",) + HORAS) + "/" + fix.path
    try:
        out = _git("show", "HEAD:" + rel, check=False)
    except OSError:
        return None
    return out.stdout.replace("\r\n", "\n") if out.returncode == 0 else None


def upstream(fix):
    """The project's text of the section (the checkout's last commit), or None."""
    text = upstream_file(fix)
    return section_text(text, fix.section) if text is not None else None


def changed_upstream(fix):
    """A note when the project's text is no longer the one the fix replaced."""
    up = upstream(fix)
    if fix.notes.get("replaces") == "none":  # a section the fix adds
        if up is None:
            return None
        if up == "\n".join(fix.body):
            return "the project now has this section itself, the same: the fix can be removed"
        return "the project now has this section itself: check it"
    if up is None or "replaces" not in fix.notes:
        return None
    if up == "\n".join(fix.body):
        return "the project now has this text itself: the fix can be removed"
    if digest(up) != fix.notes["replaces"]:
        return "the project has changed this section since the fix was written: check it"
    return None


def record_replaces(fixes):
    """Note in data-fixes.txt the project's text each new fix replaces."""
    with open(FIXES, encoding="utf-8") as fh:
        text = before = fh.read()
    for fix in fixes:
        up = upstream(fix)
        if "replaces" in fix.notes:
            continue
        if up is None and "adds" in fix.notes and upstream_file(fix) is not None:
            mark = "none"  # the project's file has no such section
        elif up is None or up == "\n".join(fix.body):
            continue
        else:
            mark = digest(up)
        fix.notes["replaces"] = mark
        head = "== %s %s\n" % (fix.path, fix.section)
        text = text.replace(head, head + "replaces: %s\n" % mark, 1)
    if text != before:
        with open(FIXES, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


# -- the commands ------------------------------------------------------------


def targets():
    out = [("repo", os.path.join(REPO, "web"))]
    if os.path.isdir(APP_WEB):
        out.append(("app", APP_WEB))
    return out


def report(write):
    fixes = load()
    adds = additions()
    if not fixes and not adds:
        print("no fixes in %s and nothing in %s" % (FIXES, ADDITIONS))
        return 0
    if write:
        record_replaces(fixes)
    missing = 0
    for fix in fixes + adds:
        states = []
        for name, web in targets():
            state = apply_one(web, fix, write) if isinstance(fix, Fix) else add_one(web, fix, write)
            missing += state in ("not applied", "no file", "no section")
            states.append("%s: %s" % (name, state))
        print("%-45s %s" % (fix, "; ".join(states)))
        note = changed_upstream(fix) if isinstance(fix, Fix) else None
        if note:
            print("    note: " + note)
    return 1 if missing else 0


def pull():
    """git pull in repo/, with the fixed files set aside first and the fixes re-applied."""
    fixes = load()
    record_replaces(fixes)  # before the pull: the text each fix was made against
    status = _git("status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    fixed_files = {"/".join(("web",) + HORAS) + "/" + f.path for f in fixes}
    others = [l[3:] for l in status if l[3:].strip('"') not in fixed_files]
    if others:
        print("not pulling: these files in repo/ have changes that are not in data-fixes.txt,")
        print("and a pull could lose them:")
        for o in others:
            print("    " + o)
        return 1
    # Set aside only files whose every change is a fix, so nothing else is lost.
    for rel in sorted(fixed_files):
        if not any(l[3:].strip('"') == rel for l in status):
            continue
        clean = _git("show", "HEAD:" + rel).stdout.replace("\r\n", "\n")
        for fix in fixes:
            if "/".join(("web",) + HORAS) + "/" + fix.path == rel:
                clean = patched(clean, fix) or clean
        current, _bom, _crlf = _read(os.path.join(REPO, *rel.split("/")))
        if current.rstrip("\n") != clean.rstrip("\n"):
            print("not pulling: repo/%s has changes besides its fixes" % rel)
            return 1
        _git("checkout", "--", rel)
    result = _git("pull", "--ff-only", check=False)
    print((result.stdout + result.stderr).strip())
    code = report(write=True)
    if result.returncode:
        print("the pull failed (above); the fixes are back in place")
        return 1
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--pull", action="store_true", help="update repo/ from GitHub, keeping the fixes")
    args = ap.parse_args()
    if args.pull:
        return pull()
    return report(write=not args.check)


if __name__ == "__main__":
    sys.exit(main())
