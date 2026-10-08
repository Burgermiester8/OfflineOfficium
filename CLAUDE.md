# OfflineOfficium

An offline Divinum Officium for Windows: a desktop app around the upstream
engine (`repo/`, a git submodule) that serves the Office and the Mass, makes
PDFs of any days, and typesets a breviary in volumes (Typst). `README.md` is
the full record of how everything works and why, with what was measured; it
is long, so search it rather than reading it whole.

## Where things run

- **The Windows PC** (the publisher's): builds the app (`build-exe.py`), its
  Setup, and the releases. The release signing key is there and nowhere else
  (`%USERPROFILE%\.offline-officium\`), so releases are made only there.
- **A cloud session** (Linux): changes the code and the data, runs the engine,
  and makes PDFs from the source. `tools/cloud-setup.sh` prepares it at every
  session start (a SessionStart hook in `.claude/settings.json`): the
  submodule, the data corrections, Typst, pypdf and PyMuPDF. It cannot build
  or open the Windows app, the Setup, or a release; push the work, and the PC
  pulls and releases it.

## The files

- `DivinumOfficium.py` the app's window; `doui.py` its look (light/dark,
  placement, scrolling); `pdfdialog.py` the PDF and breviary windows
- `docgi.py` the local CGI server; `dooffice.py` runs the engine for a day and
  parses it; `engine_dump.pl` reads whole parts of the breviary through it
- `dopdf.py` typesets days; `dobreviary.py` the breviary; `dopsalter.py` its
  Ordinary, Psalter, seasons and Calendar; `dovolumes.py` the volumes;
  `dotranslate.py` with `translations-en.json`, the English the data lacks
- `doupdate.py` updates from the GitHub releases (Ed25519-signed)
- `datafixes.py` with `data-fixes.txt`: corrections to the upstream texts;
  `data-additions/` whole files upstream lacks (the Coverdale psalter)
- `build-exe.py`, `launcher.py`, `installer.iss`: the app, its Setup, releases
- `tools/audit_references.py` checks every cross-reference in a breviary PDF

## Commands (from the source, on Linux or Windows)

```bash
python3 datafixes.py --check            # which corrections are in repo/
python3 dopdf.py 2026-10-07 --hours Vespera -o /tmp/vespers.pdf
python3 dobreviary.py --parts commune --version "Monastic - 1963" -o /tmp/commune.pdf
python3 tools/audit_references.py /tmp/commune.pdf
```

On a Perl other than the app's own, the tools pass `-I perl-lib` (CGI.pm and
URI) by themselves.

## Rules

- **Corrections to the breviary texts** go in `data-fixes.txt` (one section of
  one file each, with a note of why and from what source), never straight into
  `repo/`: a fresh checkout would lose them. See *Corrections to the data* in
  the README. Whole new files go in `data-additions/`.
- **Check PDF changes** with `tools/audit_references.py`, and by reading the
  pages. A cloud session has no Windows fonts (Cambria and the others), so its
  page counts and line breaks differ from the app's: measure those on Windows.
- **The README** records each change in its own plain style, with numbers that
  were measured, not estimated.
- **Never commit** `dist/`, PDFs, or anything from the signing-key folder.
- **On the Windows PC:**
  - **Python:** use `C:\Users\jacob\AppData\Local\Programs\Python\Python314\python.exe`
    (`python` in PowerShell is another 3.11, and `python3` hangs).
  - **Tests:** point `LOCALAPPDATA` at a scratch folder; never touch the user's
    `%LOCALAPPDATA%\DivinumOfficium`.
  - **Code updates:** `build-exe.py --quick` (the app may stay open).
  - **Full builds:** only with the app closed.
  - **Releases:** `python build-exe.py --quick --release --notes "…"`.
- Commits end with the `Co-Authored-By:` line for Claude.
