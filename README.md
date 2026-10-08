# Hosting Divinum Officium locally — lightweight

Notes, a working setup, and a self-contained offline app, from a hands-on dive
into the [divinum-officium](https://github.com/divinumofficium/divinum-officium)
repo. Everything below was measured on this machine (Windows 11), not inferred
from the docs.

**Just want to pray the office?** Double-click
`dist/DivinumOfficium/DivinumOfficium.exe`. Nothing to install, no network.
**Want it on paper or a tablet?** Click *Make a PDF…* in that window — anything
from one hour of one day to the whole liturgical year, in any version — or
*Make a breviary…* for the Office arranged as a book: Calendar, Ordinary,
Psalter, the Propers and Commons of the Seasons and of the Saints, and the
prayers before and after, in the order you choose.

## What the thing actually is

This matters, because it determines how light you can get:

| Part | Size | What it is |
|---|---|---|
| `web/cgi-bin/` | **0.6 MB**, 46 files | The whole program. Plain Perl CGI scripts. |
| `web/www/` | **190 MB**, 35,830 files | The breviary: 32,389 `.txt` files, 3,341 `.gabc` chant files, 7 reference PDFs, images. |

There is no database, no build step, no framework, no JS bundle. Each request
runs a Perl script that reads flat text files and prints HTML. That is why this
can be hosted so cheaply — and why "lightweight" is almost entirely a question
of *how you invoke Perl*.

## The real dependency set

The `cpanfile` is misleading if you only want to *serve* the site. Every `use`
and `require` across `web/cgi-bin/` resolves to:

- **Core Perl** — `POSIX`, `FindBin`, `File::Basename`, `Time::Local`, `Storable`,
  `JSON::PP`, `locale`, `utf8`, `Carp`, `Exporter`, `Attribute::Handlers`
- **`CGI.pm`** (+ `CGI::Cookie`, `CGI::Carp`) — not core since Perl 5.22
- **`URI`** — a hard `use` at the top of `CGI.pm`

That's it. The other cpanfile entries are not used by the web app:

- `Date::Calc`, `Date::Format`, `URL::Encode`, `Algorithm::Diff` → only
  `admin/divinum-get.pl` and `admin/divinum-replay.pl` (dev scraping/regression tools)
- `CGI::Session` → not referenced anywhere in the repo
- `Plack`, `Starman` → only needed for the persistent-server deployment (below)
- `HTML::Entities` → `CGI.pm` `require`s it lazily inside `escapeHTML`/`unescapeHTML`,
  which Divinum Officium never calls, so it never loads

Both `CGI.pm` and `URI` are **pure Perl** — no compiler needed.

## Options, by actual footprint

| Approach | New install | Notes |
|---|---|---|
| **Bundled offline app (`dist/DivinumOfficium/`)** | 242 MB, nothing installed | Everything packaged, double-click, works with no Perl/Python/Git present. |
| **Perl + CGI.pm + a small CGI server** | ~0–350 MB | Lightest that works from source. ~0.9 s/page. |
| Strawberry Perl + `plackup test_env.psgi` | ~350 MB + ~30 CPAN dists | The project's own dev path. Persistent workers, faster. |
| WSL2 + `python3 -m http.server --cgi` | ~1–2 GB | The officially documented route. Works, but it's a VM. |
| Docker (`ghcr.io/divinumofficium/divinum-officium:master`) | ~3–5 GB | Zero config, but Docker Desktop on Windows means WSL2 + a VM + a Debian image. |
| Apache + `mod_cgi` | ~50 MB + config | Works; the bundled `httpd.conf` is a stock macOS file, so you're writing config yourself. |

The docs recommend Docker first. On Windows that is the *heaviest* option by a
wide margin — it is the easiest, not the lightest.

## Three Windows gotchas

**1. `python -m http.server --cgi` cannot run `.pl` on Windows.** This is the
route the official download page recommends, and it fails here. Python's
`run_cgi` only prepends an interpreter for `.py` files; for anything else it
calls `CreateProcess` on the script directly, which Windows rejects:

```
OSError: [WinError 193] %1 is not a valid Win32 application
```

It works on macOS/Linux/Termux because those fork and `exec` the script via its
`#!/usr/bin/perl` shebang — a mechanism Windows does not have.

**2. `CGIHTTPRequestHandler` is going away.** It and the `--cgi` flag have been
deprecated since Python 3.13 and are
[pending removal in 3.15](https://docs.python.org/3/deprecations/pending-removal-in-3.15.html).
Don't build on it. `do-serve.py` here doesn't.

**3. `app.psgi` hardcodes `/var/www`.** It is written for the container, not for
local use. Use `test_env.psgi` instead — that one derives its paths from
`FindBin`, so it runs from anywhere.

## The setup in this folder

```
Divine Office/
  dist/
    DivinumOfficium/        the offline app -- double-click the .exe inside
    DivinumOfficium-Setup-<date>.exe   the installer, to give to others
    DivinumOfficium.exe     single-file variant (works, but slow to start)
  DivinumOfficium.py        windowed launcher (the app's entry point)
  docgi.py                  the CGI server, shared by the app and the CLI
  dooffice.py               gets the office text from the engine and parses it
  dopdf.py                  typesets that text into a PDF (Typst), also a CLI
  pdfdialog.py              the "Make a PDF" and "Make a breviary" windows
  dobreviary.py             arranges the Office as a book and typesets it
  dopsalter.py              the Ordinary, Psalter, seasons, Calendar and prayers
  engine_dump.pl            reads a part of the breviary through the engine
  do-serve.py               command-line server
  build-exe.py              rebuilds the app from the repo; --installer makes the Setup
  installer.iss             the Setup's script (Inno Setup 6)
  doupdate.py               updates: checks GitHub's newest release, verifies, installs
  licenses/                 the licences of the parts, installed with the app
  perl-lib/                 vendored pure-Perl CGI.pm 4.68 + URI 5.31 (0.6 MB)
  tools/engine-probe.pl     breviary-builder research: the engine's loader, standalone
  repo/                     the shallow clone
```

### The offline app

`dist/DivinumOfficium/DivinumOfficium.exe` is a self-contained desktop build.
Double-click it: a small window appears and the server starts; *Open the
Office* (or *Open the Mass*) then opens it in your default browser -- the
app no longer opens the browser by itself at every start. This folder build
installs nothing (the Setup, below, installs a copy). No network connection is
used, except to check for updates when asked.

It carries its own Perl, so it does not depend on Git for Windows, Python, or
anything else being present. Verified by launching it with `PATH` cut down to
`C:\Windows\system32;C:\Windows` — the office, the Mass and the Ordo all
still served.

| | Folder build | Single-file build |
|---|---|---|
| Path | `dist/DivinumOfficium/` | `dist/DivinumOfficium.exe` |
| On disk | 242 MB (exe itself 2.3 MB; Typst is 50 MB of it) | 111 MB |
| Time to ready | **~2 s** | **125–149 s, every launch** |
| First page | ~0.5 s | ~0.5 s once up |

**Use the folder build.** The single-file exe is a genuine one-file
executable and it does work, but PyInstaller's onefile mode re-extracts the
entire 171 MB / 38,000-file payload to `%TEMP%` on *every* start, which costs
over two minutes each time. It is there if you need to move one file around;
it is not something to pray the office with. (It also leaves ~171 MB in
`%TEMP%\_MEI*` if killed rather than closed cleanly.)

The folder is portable — copy `dist/DivinumOfficium/` anywhere, including a
USB stick, and run the exe from there.

**Its look.** The windows are drawn in the manner of the Wii's menus: white
rounded cards on a pale ground, pill-shaped buttons that light up blue under
the pointer, the main window's four tasks as rounded tiles with line icons
(the Office, the Mass, a PDF, the breviary). There is a **light and a dark
mode**: the *☾ Dark* / *☀ Light* button in each window switches every open
window at once, title bar included, and the choice is kept
(`%LOCALAPPDATA%\DivinumOfficium\ui-settings.json`); until one is made the
app follows Windows' own setting. Tk has no rounded widgets, so `doui.py`
draws the shapes itself, pixel by pixel with soft edges, into small PNG
images (with `zlib` and `binascii` alone, as the app's bundle has no Pillow
drawing), and gives the themed widgets layouts made of them: buttons,
fields, check boxes, option buttons, cards, the progress bar and the scroll
bars. The plain Tk parts (the log, the page preview's backdrop) are coloured
by a walk over the windows. The samples of the printed page (the font
preview, the pages) stay white in either mode.

Each window is built out of sight and shown finished: placed in the middle
of the screen (a dialog over the main window), its title bar already in the
mode, then faded in over about a seventh of a second, while the main window
shows the waiting cursor. Drawn in view, the breviary window used to appear
empty in the screen's corner and fill in a widget at a time for about a
second (seen in a screen recording, October 2026); applying the theme also
made a hidden drop-down list for every combo box, which it no longer does.
The breviary window is now ready in about a quarter of a second.

**Windows stay above the taskbar.** A window is placed wholly inside the
screen's work area (the screen less the taskbar, on whichever monitor it
opens), title bar and border included. Placed by its inside alone, with a guess
of 48 px for the taskbar, the breviary window hung about 30 px below the
taskbar on a 1920×1080 screen (October 2026). On a smaller screen, such as a
1366×768 laptop or 1080p at 125% scaling, the PDF and breviary windows are too
tall to fit at all. Their choices then scroll, with the wheel or a scroll bar
that appears only when needed, and the row with *Make PDF…* stays in view
below them (`doui.ScrollArea`). Measured from the source:

| Window, screen | Result |
|---|---|
| Breviary, 1920×1080 (taskbar from 1,032 px) | 1314×922, bottom edge at 1,007 px; no scroll bars |
| Breviary, 1366×768 | 1326×708, inside; a vertical bar; the buttons in view |
| Breviary, 1280×720 | inside; both bars; the buttons in view |
| PDF, 1920×1080 and 1366×768 | inside; on the smaller, a vertical bar |

The wheel scrolls the choices, but not over the parts list, which scrolls by
itself, nor over the buttons below. The app does not declare itself DPI-aware,
so with scaling Windows gives it a smaller screen in scaled units. The calls
used for the placement (`GetMonitorInfo`, `GetWindowRect`) answer in those
same units.

If a launch misbehaves, the app writes a log to
`%LOCALAPPDATA%\DivinumOfficium\last-run.log` (a windowed app has no console,
so without that there is nothing to look at). `DivinumOfficium.exe --console`
attaches a console and prints the same lines.

### Giving it to others

```bash
python build-exe.py --installer
```

packs the folder build into one file, `dist/DivinumOfficium-Setup-<date>.exe`
(69 MB), with Inno Setup 6 (installed here; free from jrsoftware.org). It packs
the build as it stands, so update it first: `--quick --installer` does both.
It takes about a minute and a half. `app/previous/` (what `--rollback` swaps
back) is left out.

What the person who runs it gets:

- **Two clicks:** *Next* (with a box for a desktop shortcut) and *Finish*
  (with *Launch Divinum Officium*). No folder to choose and no administrator
  prompt: it installs for that Windows user, in
  `%LOCALAPPDATA%\Programs\Divinum Officium`.
- **The usual places:** a Start menu shortcut, and an entry in *Settings >
  Apps* to uninstall it. The wizard follows Windows' light or dark mode and
  shows the app's icon.
- **Updates:** a newer Setup run over an older one replaces the app whole. If
  the app is open, Setup asks for it to be closed first: the app holds a named
  mutex while it runs (`DivinumOfficiumApp`), which Setup and the uninstaller
  look for.
- **Uninstalling** removes everything Setup put there. The settings and log in
  `%LOCALAPPDATA%\DivinumOfficium` stay, as most programs leave them.
- **The licences** of the parts (the Divinum Officium texts, Perl, Typst,
  Python, Tcl/Tk) in `licenses/`.

Testing it the way someone else's PC would meet it found two faults, both
fixed:

- **Typst needed a DLL that Windows does not include.** `typst.exe` uses the
  Visual C++ runtime (`VCRUNTIME140.dll`). Most PCs have it from some other
  program (this one has the VC++ Redistributable), so it never showed here. On
  a PC without it, every PDF would have failed. A copy now goes beside
  `typst.exe`: a full build copies Python's, and `--installer` adds it to an
  older build. The installed Typst loads that copy, from its own folder.
- **An accent in the folder's path stopped the engine.** Installed under
  `...\Thérèse test\...`, the self-test failed with *officium.pl: Can't open
  kalendar C:/Users/.../Th�r�se test/...*. Perl opens files through Windows'
  ANSI functions, and the engine joins its paths from pieces of more than one
  encoding. The install folder sits under the Windows user name, so someone
  called Thérèse or José would have had an app that showed no office. The app
  now gives Perl such a folder by its short name (`THRSET~1`), which is plain
  ASCII (`docgi.perl_path`, used by the server, the engine and the breviary
  builder). Before the fix, the Office page from that folder was 5 bytes and the
  Mass an error page. After it, the Office, Mass, Ordo and front page all
  serve. Windows makes short names on its own drive by default. On a drive
  where they are turned off, such a folder would still fail.

| Check | Result |
|---|---|
| Install, silent, then again over it | 88 s and 90 s; nothing left of `app/previous/` |
| Its self-test, with `PATH` cut to `C:\Windows\system32;C:\Windows` | PASSED, 38 s |
| The same, installed under an accented folder, with the temp and settings folders accented too | PASSED |
| Setup while the app runs | stops: *Setup has detected that Divinum Officium is currently running* |
| The wizard, clicked through | *Next*, 38,717 files, *Finish* |
| Uninstall | 12 s: the folder, the shortcut and the *Apps* entry gone; the app's settings untouched |
| Windows Defender scan of the Setup | no threats |

On the other person's side:

- **SmartScreen:** the Setup is not code-signed, so Windows says *Windows
  protected your PC* the first time; *More info*, then *Run anyway*. A browser
  may also call it an uncommon download, which *Keep* answers. Only a
  code-signing certificate (paid) removes these. Smart App Control, which is
  on in some freshly installed Windows 11 PCs, blocks unsigned programs with no
  *Run anyway* at all.
- **Windows:** 64-bit Windows 10 or 11. Setup also allows Windows on Arm
  (which runs x64 programs by emulation), untested. There is no Mac version.
- **Sending it:** 69 MB is too large to attach to an email (Gmail takes 25 MB),
  so share a link instead. This folder is in OneDrive: right-click the Setup,
  *Share*. Or send the releases page (below), which always has the newest.

#### Updates

Installed copies update themselves from the project's GitHub releases,
<https://github.com/Burgermiester8/OfflineOfficium/releases>. To publish one:

```bash
python build-exe.py --quick --release --notes "The hymns in two columns."
```

This updates the folder build and makes the Setup. Its version is the date,
plus `.2` for a second release the same day. It then signs the Setup and
publishes it as the release `v<version>`, with two files: the Setup and
`latest.json`. The notes are what the app shows when it offers the update.
Commit and push the code first: GitHub tags the release on its newest commit,
and `--release` warns about changes not yet committed.

In the app, under the four tiles, there is the version, *Check for updates…*,
and *Check when the app starts*. That box is off until ticked; when ticked, the
check runs at most once a day. The check reads the newest release's
`latest.json` (`releases/latest/download/latest.json`). If that release is
newer, the app asks. On *Yes* it:

1. downloads the Setup, with a progress bar and *Cancel*;
2. checks it against the signed size and SHA-256;
3. closes itself;
4. runs the Setup with `/SILENT`, which shows only its progress, replaces the
   app whole and keeps the settings;
5. reopens the app (`/relaunch=1`, in `installer.iss`).

A copy that the Setup did not install (this folder's build, a development
copy) cannot replace itself, so it is offered the download page instead.
Without a connection the check says so; nothing else in the app needs one.
From a command line, `DivinumOfficium.exe --check-update` checks and `--update`
installs, writing what they do to the log as well.

**Signed releases.** `latest.json` names the Setup with its size and SHA-256,
and carries an Ed25519 signature over those and the notes. The private key is
`%USERPROFILE%\.offline-officium\release-signing-key.txt`, outside the project
and outside OneDrive, so it never reaches git. The app has the public half
(`doupdate.PUBLIC_KEY`). It ignores a `latest.json` that key did not sign, and
it never runs a download whose size or hash differs. So someone who got into
the GitHub account still could not send a program to the installed copies.
Python's standard library has no Ed25519, so `doupdate.py` carries RFC 8032's
reference code: checked against that RFC's test vectors, about 4 ms per check.

**Keep a copy of that key file somewhere safe** (a USB stick, a password
manager). Without it no update can be published that installed copies
accept, and everyone would have to install a new Setup by hand once.
`--new-signing-key` made it and refuses to make another while it exists.

| Check | Result |
|---|---|
| RFC 8032 test vectors 1–3 | key, signature and verification right; an altered message or signature refused |
| A release signed with the key (a local test server) | accepted: *Version 2026.10.08 is available (you have 2026.10.07)* |
| The same `latest.json` with its notes changed after signing | refused: *not signed by this app's publisher* |
| Signed, but naming the wrong hash | downloaded (69 MB), refused to run, deleted |
| The whole update, in an installed 2026.10.07 | *Yes*, then the download; the app closed 3 s later; the Setup ran and the app reopened 85 s after; the files were replaced |
| The manual check, in a copy the Setup did not install | offers the download page |
| The real release, v2026.10.07, in an installed copy marked 2026.10.06 | `--update`: downloaded from GitHub, verified, installed; the Setup reopened the app, now 2026.10.07 |

The installed copy was driven through *Check when the app starts*: Tk ignores
simulated clicks, because it reads where the real pointer is. The button runs
the same code with `manual=True`, which was tested from the source.

### The code on GitHub

<https://github.com/Burgermiester8/OfflineOfficium> is public. It holds
everything in this folder except `dist/` (the releases carry the built app)
and the signing key. `repo/` is a submodule: the upstream project, at the
commit this is built against. Its local changes are the ones `datafixes.py`
writes from `data-fixes.txt`, so git ignores them there. `.gitattributes`
keeps every file byte for byte, line endings included.

To set up a working copy somewhere else, such as another PC or a cloud
session:

```bash
git clone --recurse-submodules https://github.com/Burgermiester8/OfflineOfficium.git
cd OfflineOfficium
python datafixes.py
```

A cloud session (Claude Code on the web) runs on Linux. It needs no setting
up by hand:

- **The setup.** `tools/cloud-setup.sh` runs at every session start (a
  SessionStart hook in `.claude/settings.json`; it does nothing on Windows).
  It fetches `repo/`, writes the corrections into it, installs the Linux build
  of Typst 0.15.1 and pypdf with PyMuPDF, then prints one line that the
  session reads.
- **The project's rules.** `CLAUDE.md` gives the session the rules and the
  commands.
- **Perl.** The command-line tools give a Perl other than the app's own
  `perl-lib/` (CGI.pm, which Perl has not carried since 5.22). Tested here with
  Git's Perl, which has no CGI.pm: a Vespers PDF in 2 s, the Common of the
  Saints in 7 s.

A cloud session can change the code and the data, run the engine, and make
PDFs, though with Linux's fonts, not Cambria. It cannot build the Windows app
or the Setup, and the signing key stays on this PC, so releases are made here:
pull the session's changes, then run `python build-exe.py --quick --release
--notes "…"`.

### Is it fully offline?

Yes — and one thing had to be fixed to make that true. The one exception is
the update check (see *Updates*), which reaches github.com only when
*Check for updates…* is clicked, or at start if its box is ticked.

Measured with the browser's network inspector against the running app: loading
the index, Matins, Lauds, Vespers, Compline, the Mass and the Ordo produces
requests to `127.0.0.1` **and nothing else**. Even the Venmo and PayPal
graphics on the front page are local files; only their link targets are
external. The CGI code contains no networking module at all (no `LWP`, no
`Net::*`, no `IO::Socket`), so the server side cannot phone home either.

The exception, found by auditing the shipped tree: two of the Help > Rubrics
documents — `SacrosanctumConcilium.html` and `SacramLiturgiam.html` — carried
a **live Google Tag Manager container**, inherited from wherever those Vatican
texts were originally saved from. A `<script>` block there builds
`//www.googletagmanager.com/gtm.js?id=GTM-NR4ZZL` and injects it.

Worth knowing how that hid: the URLs are *protocol-relative* (`//host/...`),
so an audit grepping for `https://` finds nothing. Offline it would have
failed harmlessly; online, an offline copy would have been reporting page
views to Google. `build-exe.py` now strips tracker markup during staging, and
the build fails loudly if any survives. The shipped build has zero references
to googletagmanager, google-analytics, googletagservices, googlesyndication or
doubleclick, and no `src=` attribute anywhere pointing off-box. Both pages
still render (86.7 KB and 11.3 KB, bodies intact).

What does still need a connection: clicking an outbound **link**. About twenty
hosts are referenced that way — github.com, archive.org, vatican.va, the
scanned-book libraries on polona.pl and dlibra.kul.pl, the donation links.
Those are ordinary hyperlinks in the Help and credits pages, not resources the
office loads, so they simply fail to open when you are offline. Nothing in the
office, Mass or Ordo depends on any of them.

### What is inside the app, and why

- **Strawberry Perl 5.40.5.1, pruned from 291 MB to 47 MB.** The download is
  a 291 MB zip that expands to over a gigabyte; 814 MB of that is the bundled
  MinGW toolchain, which a CGI script never touches. Dropping it, the CPAN
  command-line tools and 9.8 MB of pod documentation leaves the interpreter,
  its core library, and CGI 4.72 + URI.
- **Strawberry specifically, not the Perl already on this machine.** Git for
  Windows ships an MSYS2 Perl that runs Divinum Officium perfectly well, and
  bundling it would have cost 35 MB instead of 47 MB. But a copied MSYS Perl
  still resolves `/usr/...` against the Git installation rather than against
  itself, so the copy quietly depends on Git being installed — I could not
  honestly call that self-contained. Strawberry computes `@INC` relative to
  `perl.exe`, so it genuinely runs from anywhere.
- **98.2 MB of breviary data**, with the 92.8 MB of Help > Rubrics PDFs left
  out. Pass `--keep-pdfs` to `build-exe.py` if you want them.
- **Absolute `divinumofficium.com` URLs rewritten to local paths**, the same
  thing the project's Dockerfile does. This only affects links — the Help pages
  and the `SOURCE:` URL that `ical.pl` writes into exported calendars. iCal
  identifiers (`PRODID`, UID domains) are correctly left alone.
- **Third-party tracking markup stripped** — see the offline section above.

### Rebuilding it

```bash
python build-exe.py --layout folder
```

First run downloads Strawberry Perl and caches it under `%TEMP%\do-build`;
later runs reuse it. `--layout both` also produces the single-file exe,
`--skip-stage` reuses the staged payload when you are only changing the
launcher, and `--refetch` re-downloads Perl.

Close the app before rebuilding. The build first renames the old app folder
aside — Windows refuses that while any file inside is open, whether by the
running app or by OneDrive — and if it cannot, stops with nothing changed
(it checks before the two-minute PyInstaller step too). Deleting in place, as
it used to, got halfway through `perl/`, `typst/` and `web/` before reaching a
file the open app held, and left a broken app until the next build.

#### Quick updates

A full build takes 10–15 minutes: it restages 190 MB of Perl, breviary text
and Typst, runs PyInstaller, and writes it all into this OneDrive folder.
Most changes touch only the app's own code, so the folder build runs that code
from a plain folder beside the exe, `dist/DivinumOfficium/app/`, and an update
copies just the files that changed:

```bash
python build-exe.py --quick
```

It takes seconds and the app may stay open: Windows does not lock these files,
and the update takes effect the next time the app starts. Nothing is copied
unless every source file compiles; the files it replaces are kept in
`app/previous/`, and `python build-exe.py --rollback` swaps them back (run it
again to return to the newer ones). The app's log and its self-test say which
code is running (`app code: app folder, 7 files, newest …`).

How: the exe's frozen entry point is now `launcher.py`, which puts a finder
for `app/` ahead of the copies frozen inside the exe (those remain the
fallback, so an exe without `app/` still runs). PyInstaller still analyses the
app's code at full-build time, so every library it uses is bundled. A full
build is needed only when Perl, Typst, the breviary data, Python itself,
`launcher.py` or a library the code has never used changes; `--quick` warns
when the code imports a module the last full build did not bundle (the list
is in `app/bundled-imports.txt`). The single-file exe is only refreshed by a
full build.

#### Corrections to the data

`repo/` is a copy of the GitHub project, and anything edited inside it can be
lost: a fresh download or `git checkout` puts back the project's text. So
corrections to the breviary data are kept in `data-fixes.txt`, outside
`repo/`. Each correction replaces one section of one file, with a note of why
and from what source: St George's ninth lesson; the list of languages in
`horas.dialog`, which gains *English - Coverdale psalter*; and the Latin titles of
ten feasts of the *Rubrics 1960 - 2020 USA* calendar that the Latin files had in
English. Those ten are St Peter Chanel, Our Lady of Fatima, St Christopher
Magallanes, Sts John Fisher and Thomas More, St Augustine Zhao Rong, St
Sharbel, Sts Andrew Kim Taegon and Paul Chong Hasang, Padre Pio, St Martin de
Porres and St Juan Diego. They now read *S. Petri Chanel Martyris*, *S. Pii de
Pietrelcina Confessoris*, *S. Joannis Didaci Cuauhtlatoatzin Confessoris* and
so on: the names as the Roman Missal's calendar and the Holy See's Latin acts
give them, the title after the Common the feast takes, as the calendar's other
Latin titles have it.

Nine of those feasts printed "N." in their prayers, because their files had
no `[Name]` to fill the Common's: Sts Christopher Magallanes, Charles Lwanga
(English only), John Fisher and Thomas More, Augustine Zhao Rong, Sharbel,
Andrew Kim Taegon and Paul Chong Hasang, Padre Pio, Martin de Porres and Juan
Diego. Seventeen fixes add the names: the Latin in the genitive the Common's
Latin needs (*beáti Pii Confessóris tui*), the English as its prayers take it
(*thy blessed confessor Pio*). A scan of every hour of each feast's day finds
no "N." left in either language.

Four of them took the Common of several Martyrs who were also bishops (`C3`),
whose collect calls them all bishops (*Beatórum Mártyrum paritérque
Pontíficum…*, *the blessed Martyrs and Bishops…*): St Christopher Magallanes,
St Augustine Zhao Rong, Sts Andrew Kim and Paul Chong, and Sts John Fisher and
Thomas More, of whom only St John Fisher was a bishop. They now take the Common
of several Martyrs (`C3a`, *Deus, qui nos concédis sanctórum Mártyrum tuórum…*,
*O God, who dost permit us to keep the birthday of thy holy Martyrs…*), as the
calendar's other feasts of martyrs who were not bishops do. Its Eastertide form
follows by itself. St Charles Lwanga's English took that Common's collect
where the Latin has the feast's own. It now has an English translation of the
Latin, made for this project by AI (noted in `data-fixes.txt`). Coming from the
data rather than `translations-en.json`, it carries no grey *AI* mark.

**The monastic lessons in English.** Monastic Matins has twelve lessons where
the Roman has nine, so the monastic Latin files often make two lessons out of
one Roman lesson. They cut it at Latin words: `[Lectio5]
@Commune/C2:Lectio4:s/Quod autem .*//s` ends at *Quod autem…*, and `[Lectio6]`
begins there. Many English monastic files have no such section. The engine
then takes the Latin file's definition, cuts the English text at the same
Latin words, and never finds them. So the English column printed the whole
Roman lesson beside each half, and the Latin beside it looked cut off.

- **Scripture lessons** came out right: their cuts are on verse numbers, which
  both languages share.
- **The English data's own cuts:** in some files they no longer matched
  anything either (`The..plain` where the text has one space, `”Many` for
  `“Many`).

Found by an audit of every Matins lesson of Monastic 1963 (Latin beside
English: verse numbers, lengths, endings, and lessons whose English repeats
the one before). 189 corrections give the English its own cuts, at the
sentence where the English translation begins the Latin's sentence:

| Corrections | Count |
|---|---|
| The main lessons of the Common, the Saints and the Season | 138 |
| The Commons' alternative lessons (`in 2 loco` …), which the variant Commons use for their third Nocturn | 37 |
| Broken English cuts replaced | 12 |
| Latin slips: Vigil of the Epiphany XI, St Nicholas VIII | 2 |

Each English phrase was checked by applying the substitution to the English
text it cuts: it matches, and only once. After them:

| Check (Monastic 1963) | Before | After |
|---|---|---|
| Runs of lessons repeating one undivided English text | 46 | 0 |
| Lessons with English 1.7 or more times the Latin's length | 228 | 50 |

The 50 that remain are wordier translations with their scripture references.
The few cases not changed:

- **No English at all:** some alternative lessons, whose English column falls
  back to `translations-en.json`.
- **Other rubrics' forms:** the Cistercian, 1617 and Tridentine variants.
- **Latin cuts that match nothing either:** for example 094-0, where both
  columns print the whole lesson.

A fix that adds a section the file lacks carries the note `adds:`, and its
`replaces: none` records that the project had none. If the project later adds
that section itself, `datafixes.py` says so. If the language's file does not
exist at all (`English/SanctiM/08-29.txt`), such a fix starts it with just
that section. The engine lays a language's file over the Latin section by
section, so every other section still comes from the Latin's definition, as
before. Files the project
does not have at all are kept in `data-additions/`, in the same folders as
under `web/www/horas`, and copied in whole: the Coverdale psalter's
`English-Coverdale/` (see *The Coverdale psalter*).

```bash
python datafixes.py
```

writes them into `repo/` and into the built app, and reports each one.
`--check` only reports. The full build and `--quick` write them into the app's
copy every time, so the app keeps them whatever state `repo/` is in. To
update the data from GitHub, use

```bash
python datafixes.py --pull
```

It refuses if `repo/` has an edit that is not in `data-fixes.txt`, which a pull
could lose. Otherwise it sets the corrected files aside, pulls, and writes the
corrections back. A plain `git pull` is safe too: git refuses to pull over an
edited file rather than overwrite it. If the project later changes a section
that a correction replaces (perhaps fixing it itself), `datafixes.py` says so,
so the correction can be checked or dropped. Everything else in this folder
(the app's code, `translations-en.json`, `engine_dump.pl`) is outside `repo/`,
so git never touches it.

### Verified

**Clicking through the app**, as a person would, in a real browser: an hour
link (Vespers), the date arrows plus the go button (moved to the next day's
Vespers), Sancta Missa (carried the date across; Our Lady of Ransom on
Sep 24), the Mass's Options page, the Ordo and a day within it (Sep 29 →
Michaelmas), an hour on the mobile page, and Compare (two versions shown only
there, no error). An earlier round of testing loaded every hour by typing its
URL directly, which is why it missed that none of the *links* worked — see the
bare-name note in the server section above.

GUI window appears; the Perl process it spawns is the bundled one
(`dist/DivinumOfficium/perl/bin/perl.exe`, confirmed from the process table);
`kalendar.pl` returns byte-identical output to the original repo (20,656
bytes); 38/38 requests across the office, Mass, Ordo and mobile views
returned 200, both sequentially and with 12 concurrent requests; a real
browser renders Lauds and Vespers with correct rubrics and diacritics.

One caveat worth knowing: the first test run immediately after building into
this OneDrive folder produced a 500 and two malformed responses, which then
never recurred across ~50 later requests. The likely cause is OneDrive
locking files while it uploaded the freshly written 167 MB. If you see
something similar right after a build, let sync settle — or better, keep the
app outside OneDrive.

### Running from source instead

```bash
python do-serve.py --root repo/web --port 8000
```

Then open <http://localhost:8000/>. `do-serve.py` picks up `perl-lib/`
automatically, so there are no other flags and nothing to configure.

Requirements: any `perl` on `PATH` and Python 3.8+. Both were already present
here (Git for Windows ships Perl 5.38; it lacks `CGI.pm`, which is why
`perl-lib/` is vendored). **On Strawberry Perl you can delete `perl-lib/`** —
Strawberry 5.40 bundles CGI 4.68 and URI 5.32 already, so `perl` alone is enough.

What `docgi.py` does beyond the obvious: sets the CGI environment
(`SCRIPT_NAME`, `PATH_INFO`, `QUERY_STRING`, `HTTP_COOKIE`, …), runs each
script with its own directory as the working directory (the scripts resolve
data files relatively — both of the project's `.psgi` files `chdir` too, though
they do it globally per worker, which their own comment notes is racy under
load; passing `cwd` per subprocess avoids that), honours `Status:` and
`Location:` headers, passes POST bodies through, and threads requests. It
passes `-I` on the command line rather than setting `PERL5LIB`, because MSYS
Perl splits that variable on `:` and mangles `C:\...` paths.

It also runs each script by its **bare name** (`perl officium.pl`, not
`perl C:\...\officium.pl`), and this one matters. The scripts inspect `$0` to
work out who they are:

- `officium.pl` sets its own URL with `substr($0, rindex($0, '/') + 1)`. A
  backslashed Windows path contains no `/`, so the *entire* `C:\...` path
  became the form `ACTION` and the target of `hset()`. The browser treats that
  as a URL with scheme `c:` and silently refuses it — so every hour link, the
  date button, Options and the Ordo's day links went nowhere.
- The next line, `$Ck = substr($officium, 0, 1) eq 'C'`, identifies the
  two-version comparison page, `Cofficium.pl`. The drive letter satisfied it,
  so every page rendered as the comparison view — the stray
  "Rubrics 1960 - 1960/Divino Afflatu - 1954" header and the red
  "Unknown version: Divino Afflatu" error both came from this.
- `webdia.pl` matches `$0` against `/missa/`, `/Pofficium/` and `/Cofficium/`.
  With a full path, installing under a folder whose name contained "missa"
  would have made the Office behave like the Mass.

None of this happens under Apache or Starman on Linux, where `$0` is a
`/`-separated path under `/var/www`. It is purely a Windows-hosting trap.

### Verified working

Against today's office, with correct rubrics and UTF-8 diacritics intact:

| Endpoint | Result |
|---|---|
| `/` | 200, 7.6 KB |
| `/cgi-bin/horas/officium.pl` (Office, desktop) | 200, 22 KB |
| `/cgi-bin/horas/Pofficium.pl` (mobile) | 200, 23 KB |
| `/cgi-bin/missa/missa.pl` (Mass) | 200, 15 KB |
| `/cgi-bin/horas/kalendar.pl` (Ordo) | 200, 21 KB |
| POST Vespers, with cookies | 200, 54 KB |
| POST Matins (heaviest hour) | 200, 96 KB |

All 15 rubric versions, 19 languages, the diocesan calendars and the votive
offices load. `popup.pl` returns 400 to a bare `GET` — that is the script's own
`-status => '400 Bad request'` guard for a missing `popup` param; it is
POST-driven from `document.forms[0].action`, so that is correct behaviour, not a
hosting fault.

## Making PDFs

Click **Make a PDF…** in the app's window. Choose:

- **Days** — one day, a range, a month, a liturgical year (First Sunday of
  Advent to the Saturday before the next), or a calendar year.
- **Hours** — any combination of the eight, with shortcuts for all, Lauds &
  Vespers, and Vespers & Compline.
- **Office** — any of the fifteen versions the site offers (Tridentine 1570
  through Rubrics 1960 — 2020 USA, Monastic 1617/1930/1963/Barroux,
  Cistercian, Dominican), the diocesan calendar, a votive office (the Little
  Office of Our Lady, the Office of the Dead, the commons …), and whether it
  is said by a priest.
- **Languages** — left and right, or one column.
- **Page** — Letter, A4, half letter, A5 or pocket breviary (4.5 × 7 in); a
  new page per day or per hour; title page; contents; rubrics in colour or
  black; optionally a tagged PDF for screen readers.
- **Type** — the font (any text font installed on the PC; the faces that set a
  breviary well — Cambria, Constantia, Georgia, Palatino, Garamond, Book
  Antiqua … — are listed first), the size, the alignment (justified, left or
  right), and the colours of the rubrics and of the text (a short list of
  named colours, or any colour from the Windows colour picker). Headings and
  titles take the rubric colour; the contents are always set flush left.

It shows an estimate before you start (pages, time, size), a progress bar,
and a Cancel button; when it finishes, *Open PDF* and *Show in folder*. Your
choices are remembered.

What you get: Latin and the translation side by side, **aligned verse by
verse**; rubrics, verse numbers and initials in red; Latin hyphenation; a
running head with the day and the hour; page numbers; PDF bookmarks for every
day and every hour (the file opens with them showing); and for more than one
day, a contents list whose entries are clickable. A psalm title or a section
heading is never left alone at the foot of a page.

### How it works — and why not a scraper

Your instinct was right that the engine should decide what is said on each
day; reimplementing the rubrics would be hopeless. Two refinements:

- **No screen-scraping.** `officium.pl` has a built-in switch, `content=1`,
  that emits only the office — no menus, forms or scripts. `dooffice.py` runs
  the script directly with the bundled Perl (no web server), asks for the whole
  day in one call when you want every hour, and runs up to seven days in
  parallel. The engine's HTML uses a tiny, regular vocabulary — about twenty
  tag forms across a whole day in every version tried — so it parses into a
  clean structure (day → hours → sections → lines → styled runs) rather than
  a blob of text. The day's title even carries its liturgical colour.
- **Typesetting, not text dumping.** `dopdf.py` writes a Typst document and
  compiles it with the bundled `typst.exe`. Typst gives real book typesetting
  (justification, Latin hyphenation, running heads, bookmarks) and is fast on
  documents thousands of pages long.

### Measured

| | Time | Pages | File |
|---|---|---|---|
| One hour (Vespers) | 0.9 s | 6 | 0.12 MB |
| One day, all hours | 3.0 s | 35 | 0.33 MB |
| One month | 24 s | 1,055 | 7.2 MB |
| **Liturgical year 2026–27** | **6 min 22 s** | **12,317** | **84 MB** |

Measured through the built app (`--make-pdf`, with PATH cut down to
`C:\Windows\system32;C:\Windows`). The year run: 364 days, 3,278 bookmarks,
364 clickable contents entries, no
failed days; Typst peaked at 1.13 GB and the machine never dropped below
4.9 GB free.

Three things had to be solved to get there, recorded here because none is
obvious:

1. **A year in one Typst document does not fit in memory.** It passed 8 GB
   and was still going. So the book is typeset in *volumes* of about three
   weeks each (≤ 1.3 GB), with page numbers carried across; the title page and
   contents are typeset last, once every page number is known; `pypdf` joins
   the parts in about ten seconds, keeping all bookmarks. The contents rows are
   made clickable by asking Typst where each row landed (`typst query`) and
   adding GoTo links after the join. PDF page labels are set so the viewer's
   page numbers match the printed ones (roman front matter, then 1, 2, 3 …).
2. **Accessibility tagging was 60% of every file.** Typst writes a tagged PDF
   by default — 153,000 structure objects in one month. It is off by default
   here (`--no-pdf-tags`) and available as an option.
3. **Cambria's `ccmp` feature doubled the rest.** It redraws precomposed
   letters like *ó* as *o* plus an accent, and Typst then has to wrap every
   plain *a* and *o* in a correction span — 17,694 of them in one day's 34
   pages. The engine's text is already precomposed, so the feature is switched
   off for Latin-script languages: pixel-identical pages, identical extracted
   text, half the size. (Kept on for Vietnamese and Hebrew, which stack marks.)

Also: a day the engine fails on does not sink a year's export — it gets a
visible note in the PDF and is listed when the export finishes.

### From the command line

```bash
dist/DivinumOfficium/DivinumOfficium.exe --make-pdf 2026-11-29 2027-11-27 --version "Monastic - 1963" -o year.pdf
```

`--hours Vespera,Completorium`, `--lang2 Latin` (one column), `--paper`,
`--size`, `--black`, `--font Georgia`, `--align justify|left|right`,
`--rubric-colour "#1f4e9c"` and `--text-colour "#222222"` also work. `DivinumOfficium.exe --selftest` checks every
bundled piece (data, Perl, Typst, the engine, the PDF window, an export, the
breviary window, a breviary from the dumped parts and one from the rendered
Ordinary) and prints what passed. From source, `python dopdf.py …` takes the same options.

### Known limits

- Typeset with **Cambria**, which ships with Windows and is one of the few
  fonts that has ℣, ℟ and ✠. Change `Layout.font` for another face; the
  fallback chain covers anything missing.
- Bilingual rows are aligned line by line. In about 1% of sections — mostly
  hymns, whose translations have a different number of lines — the two sides
  simply run top-aligned instead.
- *Cantilenæ* (chant notation) is left out of the language lists: it is
  meant for a chant renderer, not for a text breviary.

## The breviary builder

A different product from the dated export above: not *what is said on each
day*, but a **book organised like a printed breviary**, in any order you choose.
Click **Make a breviary…** in the app's window:

- **Parts** — tick the ones you want and put them in order with ▲ ▼. By
  default, in the order of a printed breviary:

  | Part | English | Pages (1960, Latin and English) |
  |---|---|---|
  | *Kalendarium* | Calendar | 10 |
  | *Orationes ante et post Divinum Officium* | Prayers before and after the Office | 2 |
  | *Ordinarium Divini Officii* | Ordinary of the Office | 21 |
  | *Psalterium* | Psalter | 160 |
  | *Proprium de Tempore* | Proper of the Season | 838 |
  | *Commune de Tempore* | Common of the Seasons | 17 |
  | *Proprium Sanctorum* | Proper of the Saints | 779 |
  | *Commune Sanctorum* | Common of the Saints | 547 |

- **Include the alternative lessons** — on by default; off leaves out the
  Common's second and third sets of lessons (and the few "alia" forms
  elsewhere): the Common drops from 561 to 416 pages.
- **Version** — any of the fifteen. The title page follows it: *Breviarium
  Romanum*, *Breviarium Monasticum*, *Breviarium Cisterciense*, *Breviarium
  Ordinis Prædicatorum*.
- **Languages**, **page** and **type** options as for the dated PDFs (font,
  size, alignment, colours), plus whether each office starts on a new page
  (off by default: a breviary runs on). A **preview** under the font shows a
  few lines of the office in the chosen face (a heading, ℣. ℟. ✠, accents and
  æ/œ). Libertinus Serif and New Computer Modern are built into Typst, not
  installed in Windows, so the preview says it cannot show those.
- **Spacing and margins** (the window's lower right; 100% and 0% are the
  standard, which *Standard* puts back):
  - **Line spacing**: from one line to the next, 60–300%.
  - **Space between verses**: between the rows of the two columns and
    between paragraphs, 0–400%.
  - **Letter spacing**: added between letters, as a percentage of the type
    size, −10 to +30%.
  - **Word spacing**: the width of a space, 50–300%. Justified lines still
    stretch their spaces to fill the line.
  - **Margins** in inches. *Top* runs from the page's edge to the running
    head (the text starts about two lines below it); *bottom* from the text
    to the edge; *inside* is the binding side, left on right-hand pages and
    right on left-hand ones; *outside* the other side. They start at the
    paper's own (Letter and A4: 0.5, 0.5, 0.75, 0.75) and follow a change of
    paper until you change them. Below 0.5 in the window warns that
    print-on-demand printers ask for more. Chosen margins set any paper as
    Letter and A4 are set: page number in the head, no foot.

  The page estimate follows these settings, roughly. As an example, the
  Monastic book at the settings of run 15 with line spacing 95%, space
  between verses 80%, letter spacing −1%, word spacing 95% and margins 0.5,
  0.5, 0.9, 0.6 is 696 pages instead of 743, every one of its 7,764
  references verified, and every page's inside margin on the binding side. **Preview pages…**
  sets two facing pages (2 and 3: Sunday Lauds as the Psalter has it) with
  the type and spacing chosen, from the engine's text, in less than a second.
  A dashed line marks 0.5 in from the edge, and the pages are set again as
  the settings change. They open in a window of their own, which can be
  resized, with a zoom: **Fit** (both pages in the window) or 25% to 300% of
  the printed size, with − and +, *Actual size*, Ctrl + mouse wheel (at the
  pointer), Ctrl + / Ctrl − and Ctrl 0 (fit). Each zoom is set again by Typst
  at its own resolution, so the type stays sharp rather than enlarged; the
  page is dragged to move about, and the wheel scrolls (Shift: sideways). The same settings are on the command line:
  `--line-spacing`, `--verse-spacing`, `--letter-spacing`, `--word-spacing`
  (percent) and `--margins TOP,BOTTOM,INSIDE,OUTSIDE`.

  Typst places a page's inside margin by the page's place in its volume's
  file, but the book is typeset in volumes, and a volume can begin on an
  even page. So each volume says which side it is bound on (*binding:
  right* when it begins on an even page); otherwise its inside margins would
  fall on the wrong side. This also corrects the smaller papers, whose
  margins were already unequal.

Several things make the book shorter, as printed breviaries were:

- **Headings on the first line.** A section's heading starts its first line
  in red bold italic at the text's size, *Lectio I.* Isa 1:1-3, *Responsorium
  II.* ℟. Aspiciébam…, *Oratio.* Excita…, instead of taking a line of its own
  (about 10,000 lines in a Monastic book). Psalm and canticle titles keep their
  own line, and the Ordinary, the prayers and the Calendar are left as they
  are. The space above the hour and office headings is a little tighter too.
- **Part of a section printed before** (with the option below). Inside a
  section that is not a repeat as a whole, a run of three or more lines
  printed before, in both languages, becomes one line when that saves at
  least three printed lines: its opening words and, where the first copy goes
  on past the run, where to stop — *℟. Immísit Dóminus sopórem in Adam, et…
  usque ad ℣. Cumque obdormísset, tulit unam de costis… pag. 55* beside *…as
  far as ℣. And while he slept He took… p. 55* — then the rest of the
  section as usual. A section that others refer to as a whole is never
  shortened this way, so a reference always lands on the text itself, never
  on another reference.

  **Checking a book's references.** `python tools/audit_references.py
  book.pdf` reads every reference from the printed pages alone — nothing from
  the build — and checks that the page exists and comes before it, that all
  its quoted words are printed on that page as text (references there don't
  count), that an *usque ad* line follows, that the Latin and English give
  the same page (or the English the next, where its text starts a row lower),
  that the page's own number in the head agrees, and that the
  heading a reference names is the last of its kind over the words quoted
  (the nearest responsory heading above them is *Responsorium III* for *Resp.
  III*). It exits 1 if anything fails. Run on a Monastic 1963 book (A4, Times
  New Roman 7 pt, no alternative lessons) it found 63 of 7,844 references
  that landed on a first copy since turned into a reference itself (fixed:
  the rule above). With the headings named, that book checks 7,986 of 7,986
  and the 1960 book (Letter, 10 pt) 7,074 of 7,074. As controls: every
  target shifted by one page fails 7,748 of the 7,986; every numbered heading
  given the next number (*Resp. IV* for *Resp. III*) fails 4,796.
  Since the columns are paired by verse numbers, each column has its own
  bookmark and the Psalter is printed in full (see *The Coverdale psalter*,
  and below), the Monastic book checks 7,754 of 7,754, its Coverdale edition
  7,764 of 7,764, and the 1960 book 6,958 of 6,958. On the Coverdale book
  (before the Psalter was printed in full) the controls failed 7,796 and
  4,796 of 8,036.
  One reference in two half-letter volumes failed although the page was
  right: PyMuPDF joined a Latin line to the English line beside it, and the
  joined line's height put it level with the line above, the one with the
  large initial (*L*iberásti… nó-/minis), which then sorted after it. The
  audit now places each column's part of a line by its own height.

- **A repeated text printed once** (on by default; *Print a repeated text
  once…* in the window, `--full-texts` to turn it off). Where a whole section
  — a responsory, a hymn, a lesson, an antiphon set — has the same words in
  both languages as one printed earlier, it keeps its heading and becomes one
  line: its opening words, where the first copy stands and its page, *℟.
  Ego dixi, Dómine, miserére mei… Resp. III, pag. 34* beside *℟. I said:
  Lord, be merciful unto… Resp. III, p. 34* (the words to the asterisk, or six
  words; never past a ℟. or ℣. in mid-line). A set of antiphons is referred
  to by its first.

  *Where it stands* is the first copy's own heading, short in each column's
  language — *Resp. IV, Lect. VII, Hymn., Cap., Ad Magnif., Orat. mortuorum* /
  *Resp. IV, Lesson VII, Hymn, Chapter, Magnificat* — with its hour when the
  office has that heading at more than one hour (*Cap. ad Laud.*, *Chapter
  at Lauds*; *Hymn. in I Vesp.*). A heading that names several texts at once
  (*Capitulum Responsorium Hymnus Versus*) is given by the first. For part of
  a long section it is the nearest heading above the words inside it (*Ps. 50
  [3]* in a Psalmi of several pages), and none when the words quoted are a
  psalm's title themselves. It adds about half a percent to the book (a
  Monastic 1963 book in A4: 718 → 722 pages).
  Sections of one line stay as they are (a reference would save nothing):
  an antiphon, a versicle, a short lesson. A lesson of one paragraph is one
  line too, but a long one: from 200 characters it is referred to like any
  other section. Until the monastic Te Deum was moved (see *How it works*),
  a lesson qualified only by accident, when the stray *Te Deum* rubric gave
  it a second line; 225 repeated lessons of a Monastic 1963 book were
  printed in full every time. The Ordinary, the prayers, the Calendar and
  the Psalter are never replaced.

  In a book of one language this option failed outright until October
  2026: the first copy's marker (a hidden heading and its label, which the
  references count pages from) was placed with Typst's `place()`, which is
  ignored inside a paragraph, and one language sets each line as a
  paragraph, so every reference named a label that did not exist and the
  typesetting stopped. Two languages set their lines in a grid, where it
  works. Inside a paragraph the marker is now an empty inline box. Books of
  one language are now checked like the others (see *One language*).

  **The Psalter is printed in full**, as in a printed breviary: every day's
  psalms on that day, nothing replaced by a reference, so the little hours
  need no turning of pages. (In the Monastic office Tuesday to Saturday sing
  the same gradual psalms at Terce, Sext and None, and Compline is the same
  each night: each day has them all.) Its headings still run into their first
  lines. The parts after it can refer to it as before. Its own *Ut in Feria
  II* ("as on Monday") for a hymn or chapter said as on an earlier day is
  gone too: each day prints them. In a Monastic 1963 book at the settings of
  run 15 the Psalter grows from 67 to 85 pages and the book from 725 to 743
  (with the Coverdale psalter; 720 to 739 with the Douay); the 1960 book's
  Psalter from 127 to 146 pages.

  The page numbers come from the typesetting itself: the first copy carries a hidden
  bookmark, whose page is read back when its volume is typeset, for
  references in later volumes; references in the same volume ask Typst for
  the page directly. The number sits in a box of fixed width, so filling it in
  moves nothing. Each column's first line carries its own bookmark. Where the
  Latin has a line the English has not (a lesson in two paragraphs, the
  English in one beside the second), the English text starts a row lower, and
  a page can end between the two. The 1960 book had two such references,
  whose English then gave the page before its text.

- **The usual texts by their first words.** The *Glória Patri* after a psalm
  is one line, *℣. Glória Patri. ℟. Sicut erat.*, and the standard collect
  ending is *Per Dóminum. ℟. Amen.* (in English *Glory be to the Father. ℟. As
  it was.* and *Through our Lord. ℟. Amen.*). Only those exact texts are
  shortened. *Per eúndem*, *Qui vivis*, *Qui tecum*, the ending *in unitáte
  eiúsdem Spíritus Sancti*, the *Glória Patri* of a responsory and the hymns'
  doxologies all stay in full, so an unusual ending stands out. The Ordinary
  and the prayers before and after the Office keep the full texts; a book
  without the Ordinary keeps the first of each in full. A section is shortened
  only where both columns have the same texts, so the languages stay side by
  side.
- **Margins for print-on-demand** (Letter and A4). Lulu and similar printers
  want everything 0.5 in from the trimmed edge, running head and page number
  included. So the page number moves into the running head, at its outer
  corner (right on odd pages, left on even), and the text runs to 0.5 in from
  the foot; the running head starts 0.5 in from the top. The dated PDFs keep
  their layout.

Each office is laid out hour by hour — *In I Vesperis, Ad Matutinum, Ad Laudes
… In II Vesperis* — and each text under its heading in both languages
(*Antiphonæ / Antiphons*, *Capitulum / Chapter*, *Hymnus / Hymn*, *Ad Magnificat
/ At the Magnificat*, *Lectio I / Lesson I*, *Responsorium I / Responsory I* …),
the antiphons of Lauds and Vespers numbered 1 to 5, one to each psalm. At
Matins the antiphons follow the Invitatory and hymn. Each Nocturn's
antiphons are under their own heading (*In I Nocturno: / In the first
Nocturn:*) and numbered within it: 1 to 3 in the Roman office, 1 to 6 and then
the canticles' antiphon in the monastic (see *The Nocturns* below).

**Hymns** are set in two columns of stanzas in each language: the Latin's
stanzas in two narrow columns on the left, the English's on the right, about
half the height. A hymn's lines are short, and set one to a row they left
most of each column empty. Details:

- **The split:** the stanzas divide as near the middle as they allow, the
  first column the longer. Both languages divide at the same stanza when they
  have as many stanzas, so each stanza stays beside its translation.
- **Where a stanza begins:** after a blank line, or at a red initial (the
  Common of the Season, where the hymn is part of a longer section and
  stanzas are not spaced).
- **When it applies:** Typst measures the lines at the book's own type, size,
  paper and margins (`hymnfit`). Only where every line (all but one in ten,
  which then wraps) fits half a language's column is the hymn split.
  Otherwise, or for a hymn of one stanza, it stays as before.
- **Page breaks:** a split hymn is kept on one page, so a column is never
  read across a page turn.
- **One-language books:** the same, in their single column or each of two.
- **The audit:** `tools/audit_references.py` reads such a hymn column by
  column, its first column being the lines just before the second's in the
  PDF's own order.
- **Measured** on a two-volume Monastic 1963 set (Latin and Coverdale
  English, Letter, 7 pt): 610 + 604 pages became 597 + 594. Hymns of long
  lines stay whole: the Sapphics (*Nocte surgéntes*, *Iste Conféssor*), and
  most English of them. All references verified in a bilingual book and in
  Latin-only books in one and two columns.
Each part opens on its own title page (the prayers, two pages, just under a
heading); bookmarks go part → office (saints with their dates); the contents
list both, clickable; running heads name the part, the office and the hour.

What each of the new parts holds:

- **Prayers before and after the Office** — *Aperi, Domine* (with *Domine, in
  unione*), the *Pater noster*, the *Ave Maria*, the Apostles' Creed, and
  *Sacrosanctæ* (with *Beata viscera*), each under its own heading.
- **Calendar** — a table per month: the day, its Roman date (*Kal.*, *IV Non.*,
  *XIX Kal.* …), the dominical letter, the feast in both languages and its
  rank as the version names it (*III. classis* under the 1960 rubrics,
  *Duplex majus* before), commemorations marked *Comm.*
- **Ordinary** — each hour's fixed frame in full (*Deus in adjutorium*, the
  hymns of Prime and the little hours, Psalm 94 of the invitatory and the
  three Gospel canticles with their antiphons left as *Ant.*, the preces, the
  Martyrology's *Pretiosa*, the chapter office of Prime, all of Compline), and
  a rubric where the day's texts go (*Ut in Psalterio vel in Proprio*). Matins
  adds *De Absolutionibus et Benedictionibus*: which absolution and which
  blessings are said, and when — in an office of nine lessons (twelve,
  monastic) nocturn by nocturn, with the *Cujus festum colimus* forms for
  feasts of Saints (one or several, men or women, Our Lady), *Per evangelica
  dicta* when the last lesson is from a Gospel, and Christmas; in an office of
  three lessons the absolution by the day of the week and the blessings of a
  feria, of a Saint, of a day whose first lesson is a homily (vigils, Ember
  days, Lent …), of the Office of Our Lady on Saturday, and of a Sunday of
  three lessons; monastic, the blessing of the summer short lesson. Then the
  Te Deum — the only place the book prints it: after the last lesson of an
  office the Propers and the Common have the rubric *Te Deum, ut in Ordinario*;
  (monastic, then *Te decet laus* and the frame of the Gospel); Compline, the
  four antiphons of Our Lady with their seasons and everything after them;
  the Incipit, *Laus tibi, Domine* for Septuagesima to Easter; monastic Prime,
  a rubric for the day's portion of the Rule and its blessing. Last, the
  **Litany of the Saints**, as said after Lauds on the Greater and Lesser
  Litanies (25 April and the Rogation days).
- **Psalter** — Sunday to Saturday, every hour: psalms and canticles in full
  with their antiphons, the invitatory antiphon, hymns, chapters, short
  responsories, versicles, the weekday Benedictus and Magnificat antiphons.
  Sunday gives both the summer and the winter hymns (*Nocte surgentes* / *Primo
  die*); the weekdays add *Ad Laudes II*, the penitential second scheme. A
  text said as on an earlier day (the hymns and chapters of the little hours)
  is printed again on each day, not referred to (*Ut in Feria II*), so a day
  is read without turning back. Each day's Matins ends with its absolution and blessings for
  whatever office is said on it — of the feria, of a Saint, of a homily day,
  on Saturday of Our Lady — and, in the monastic psalter, the short lesson of
  the first Nocturn for summer weekdays (and for Eastertide) and the chapter
  closing the second Nocturn in every season (*Per annum*, *Tempore Adventus*,
  *Nativitatis*, *Epiphaniæ*, *Quadragesimæ*, *Passionis*, *Paschali*,
  *Ascensionis*), as a printed monastic psalter gives them. Each version's
  own psalter: the Tridentine's three Sunday nocturns and Athanasian Creed at
  Prime, the 1911 distribution, the Benedictine one — and each version's own
  hymns (the pre-1632 forms for 1570, the Dominicans and the monastic books).
  Saturday is the ferial Saturday's (a Saturday through the year is kept as
  the Office of Our Lady, so its hymns and chapters are taken from the
  September Ember Saturday instead). For the monastic and Cistercian books the
  Psalter ends with the **canticles of the third Nocturn** that the Propers
  and the Common name (*Ps. 266* …), set out once in number order.
- **Common of the Seasons** — what each season says that is in neither the
  Psalter nor the Proper of the Season: for Advent, Septuagesima, Lent,
  Passiontide, Eastertide and Ascensiontide, the hymns (*Creator alme
  siderum*, *Audi benigne Conditor*, *Vexilla Regis*, *Ad regias Agni dapes*,
  *Salutis humanæ Sator* …), the invitatory, the Matins versicles, the
  chapters of the little hours, the antiphons of the little hours in Lent.
  Each season is read from a whole week, so what the whole week says comes
  first and what only some days say is marked with those days (*Feria II et
  Feria V*: the Eastertide versicles of Matins change with the weekday). A
  hymn of the year sung with the season's last verse (*Deo Patri sit gloria,
  Qui a mortuis surrexit* …) is not printed again: *De hymnis* gives the
  verse once and the hymns that take it. Eastertide adds the versicles of the
  three Nocturns for a feast or Sunday of nine lessons whose Matins are the
  Psalter's. This is where the Proper of the Season's missing seasonal hymns
  now are.

### Measured (Latin and English, Letter)

| Book | Time | Pages | Size |
|---|---|---|---|
| Common of the Saints (1960) | 15 s | 561 | 4.0 MB |
| — without the alternative lessons | 11 s | 416 | 2.9 MB |
| All eight parts, 1960 | 76 s | 2,394 | 18.0 MB |
| All eight parts, 1960, without the alternative lessons | 76 s | 2,248 | 16.8 MB |
| All eight parts, Monastic 1963 | 89 s | 2,617 | 18.9 MB |

With the print-on-demand margins and the short *Glória Patri* and *Per
Dóminum* (above), the complete 1960 book is 2,241 pages (78 s, 17.4 MB) where
it was 2,394, and a Monastic 1963 book at 7 pt in Book Antiqua 1,423 where it
was 1,523 (15.1 MB). Printing each repeated text once takes them to 1,793
(1,717 without the alternative lessons; the Common of the Saints 519 → 241)
and 1,078 pages: 3,348 and 4,260 references, every one with its page, and
each page checked to hold the text referred to. Running the headings into
their first lines, references to parts of sections and the tighter heading
spacing take the 1960 book to 1,626 pages (1,553 without the alternative
lessons; 2,089 with every text in full), and a Monastic 1963 book in A4,
Times New Roman 7 pt, without the alternative lessons, from 853 to 716
pages (4,033 references, 301 of them to part of a section; again every one
with its page, checked).

Times are from source; the app itself took 76 s for the complete 1960 book
(longer on its first run after a rebuild, while the new files are first
read; 95 s on a later day while OneDrive was busy syncing the project folder,
the busiest process on the machine then), with `PATH` cut down to
`C:\Windows\system32;C:\Windows`. A Monastic 1963 book of all eight parts
(1,990 pages at 8 pt) took 91 s. In that 1960 book
all 855 contents rows link to their page, all 856 bookmarks land on a page
carrying their title, and the full Te Deum appears once, in the Ordinary. Volumes are cut to the same size as before, so the
typesetting peak stays near the 1.12 GB measured for the first three parts.

### How it works

The engine does all of the liturgical work. The Proper of the Season, the
Proper of the Saints, the Common and the fixed texts (the prayers, the Te
Deum, the absolutions and blessings, the antiphons of Our Lady) are *files*
in its data, read through two of its own routines called outside any web
request (`engine_dump.pl`, run with the bundled Perl):

- `setupstring($lang, $file)` loads each file for the version and language:
  version conditions (the raw `[Rank]` of 29 Sep holds three variants; the
  right one comes back), the 5,402 `@` cross-references, the fall-back to
  Latin, and the Monastic / Cistercian / Dominican overlays on the Roman files.
- `resolve_refs($text, $lang)` expands the files' shorthand — the 75 `$`/`&`
  macros (`$Per Dominum` → the full conclusion and ℟. Amen; `&psalm(109)` →
  the numbered psalm), `V.`/`R.` → ℣/℟, rubric lines — into the same HTML
  `dooffice.py` already parses, so the parser and typesetter take it unchanged.
  The dumper then applies the last date-free steps of the website's display
  chain: the version's Latin spelling (`spell_var`: 1960 writes *Iesu*,
  *eúndem*; older versions *Génitrix*) and the removal of chant markers.

Even *which* files make up a part comes from the engine: the saints from the
version's calendar chain (`get_from_directorium('kalendar', …)`, which walks
1960 → 1955 → 1954 … 1570, each calendar listing only its changes); the season
from its variant table (`get_from_directorium('tempora', …)`, which maps
`Tempora/Adv1-0` to the version's own file). The Calendar's rank names come
from the engine's own table (`[Festa]` in `Psalterium/Comment.txt`, itself
conditioned by version). A part takes 6–7 s to read.

The Ordinary, the Psalter and the Common of the Seasons are not files: they
are the frame every day's office is assembled on, by the engine's code. So
they are read from **offices the engine assembles** (`dopsalter.py`): asked
for a day with the saints left out (`testmode=Temporal`), `officium.pl` gives
the office of the season, and it labels each part of each hour with where that
part came from — `{ex Psalterio secundum diem}`, `{ex Psalterio secundum
tempora}`, `{ex Proprio de Tempore}` … Sixty-two days are rendered (in
parallel, about 20 s): a week after Pentecost, a winter Sunday, the weekdays
of the first week of Advent (their Lauds are the second scheme), a week of each
season, the September Ember Saturday, and two days of the Litany (Rogation
Monday and 25 April). Then:

- the **Psalter** keeps what the engine labels as the Psalter's;
- the **Ordinary** keeps in full what has no label — it is said alike every
  day — and puts a rubric where the rest goes (texts not said on the sample
  Monday, like the ferial preces, are taken from the Advent Wednesday);
- the **Common of the Seasons** keeps what a season's day says that is new —
  compared, by a fingerprint of each line blind to accents and spelling, with
  the Psalter, the Ordinary and the Proper of the Season, so nothing is
  printed twice (in Advent the little hours take the antiphons of Lauds of
  the Sunday, so those stay in the Proper of the Season; a section that is
  mostly new, like Lauds' chapter, hymn and versicle, is printed whole).

When neither book language is Latin, each day is also rendered in Latin alone,
just to read the labels (the hours line up section for section).

What `dobreviary.py` adds for the dumped parts is only what a book needs and
the engine has no notion of: which hour each section belongs to and a heading
for it (263 distinct section names occur; the common ones are placed
explicitly, anything unrecognised is still printed under its own name in a
closing *Alia* group), one form of each hymn (the monastic one for monastic
versions), leaving out the Mass texts that share these files, and the book's
order.

Things that turned up while building it:

- **Borrowed texts.** `[Rule] ex Sancti/05-08` means "the rest as on 8 May".
  When that source is itself in the book (1 January borrows from Christmas),
  the office says so — *Cetera ut in: In Nativitate Domini · The rest as in:
  Nativity of Our Lord*. When it is not — 1960 dropped 8 May, yet Michaelmas
  still takes its Vespers, Lauds and hymns from it — the source is loaded and
  merged in, so Michaelmas is complete.
- **Psalm references.** Antiphons end in the psalm they go with (`;;8`,
  `;;88(2-19)`, `;;14 (Alleluia.)`); they print as a small red *Ps. 8*, as in a
  breviary — 2,267 of them in the 1960 book.
- **Stanza breaks.** A line of `_` is the data's stanza break, turned into
  spacing further down the website's display chain than `resolve_refs`.
- **Ferias without propers** (the weekdays after Pentecost from August on,
  whose Matins readings come from the "readings of the month" files) are left
  out, as a printed breviary does; those monthly readings follow the Sundays
  after Pentecost as *Hebdomada I Augusti — Dominica* and so on.
- **Fonts outside a request.** The engine's display fonts come from the
  user's settings, which it cannot read outside a web request, so small red
  rubrics (*(Fit reverentia)*, *flexis genibus*) came out as plain text; the
  dumper now sets the site's defaults. This also improved the three parts
  built in the first session.
- **The engine's labels are loose in places.** It calls a weekday's
  Benedictus antiphon "of the Proper of the Season" even where it comes from
  the Psalter's own data, so the Psalter checks such antiphons against the
  Proper of the Season before leaving them out.
- **The Te Deum.** The files end the last lesson of an office with the macro
  `&teDeum`, which the engine expands into the whole hymn — 492 times in the
  data. The dumper puts a one-line rubric in its place (*Te Deum, ut in
  Ordinario* / *Te Deum, as in the Ordinary*); the hymn itself is in the
  Ordinary. The dated PDFs, which are each day's office in full, keep it.
- **Psalms of the weekday.** In the monastic and Cistercian offices of octave
  days (and Ascensiontide, the days after Epiphany …) each Nocturn has one
  antiphon and then bare `;;` lines: "the next psalm, from the Psalter's day of
  the week" — the engine's *Antiphonas per Octavam cum Psalmis de Feria*. The
  book prints the antiphon and the rubric *Psalmi de Feria currenti*; a line
  that is only a psalm number (`;;33`, one antiphon over a Nocturn in
  Eastertide) joins the antiphon before it: *Ps. 18, 33, 44*.
- **The Nocturns.** `[Ant Matutinum]` is one list for all of Matins. The
  book used to print it as one block under *Antiphonæ*, ahead of the
  Invitatory, with nothing to say where a Nocturn began. The builder now divides
  it as the engine does (`getantmatutinum`, `psalmi_matutinum_monastic`):
  - **How it divides:** three lines to a Roman Nocturn, six to a monastic
    one, then the monastic canticles' line. Where the list has its versicles
    in it, each Nocturn ends at its ℣. ℟. A list of any other length (a
    single Nocturn, as in Easter week, or a short list) is numbered through
    without Nocturn headings.
  - **The third Nocturn's antiphon:** the monastic `[Ant Matutinum 3N]` (the
    office's own, or that of the office it takes the rest from) goes in
    the third Nocturn, over the canticles. Before, its canticles' numbers
    were run onto the twelfth antiphon (*Ps. 98, 249, 250, 251*) and the
    antiphon was printed elsewhere.
  - **The chapter:** the monastic chapter in place of the third Nocturn
    (`[MM Capitulum]`) follows the versicles, as *Capitulum in fine II
    Nocturni*.
  - **One-antiphon substitutions:** `[Ant Matutinum 11]` and `[Ant Matutinum
    12]` replace one antiphon of the Common (Holy Innocents, the
    Annunciation). They are headed by its place (*In III Nocturno,
    antiphona 2*). An office whose rule does not call for one leaves it out,
    as the engine does. `[Ant Matutinum2]` and `[Ant MatutinumBMV]` are
    building blocks and are left out. All four used to go to *Alia* under
    their raw names.
  - **References:** a reference to a whole set of antiphons does not carry
    the first Nocturn's heading. A reference to part of a set neither begins
    nor ends on a Nocturn heading.

  Over every office of all fifteen versions:

  | Matins antiphon lists | Count |
  |---|---|
  | Divided into Nocturns | 1,453 |
  | Numbered through | 144 |
  | Left as they are (*Psalmi de Feria* without a Nocturn pattern) | 58 |

  The third Nocturns that show only their canticles' numbers are offices whose
  data has no antiphon for them (Barroux 30 May, the Cistercian 13 November).
- **Where the version's file is.** A Monastic or Dominican file the version's
  table names may exist only in the Roman folder (`TemporaM/Pent03-2Feria` is
  `Tempora/Pent03-2Feria`), a Cistercian one only in the Monastic folder; the
  dumper now looks for them as the engine does (`checklatinfile`). Before, it
  fell back to the octave files, so Monastic 1963 printed the octaves of the
  Sacred Heart and of St Joseph that it no longer keeps, and the Cistercian
  book took 181 offices from the Roman files instead of the Monastic ones.
- **Two columns of different length.** The engine gives the two languages
  line for line almost everywhere; where it does not (the Latin sets *Homilia
  sancti N.* on a line of its own, the English runs it into the lesson), the
  columns are lined up by the kind of each line — heading, rubric, versicle,
  text — so the one extra line leaves a gap beside it instead of pushing the
  rest of the other column out of step.
- **The monastic sections.** The monastic, Cistercian and Dominican files use
  section names of their own, which went to the closing *Alia* group under
  their raw names (770 of them in a Monastic 1963 book). Each is now placed as
  the engine reads it: the antiphon of the third Nocturn, the short
  responsories of Lauds, Vespers and the little hours (monastic Lauds and
  Vespers borrow the Roman one of Terce and of Sext when they have none of
  their own, so those move there), the First Vespers responsory (Dominican,
  Cistercian), the Gospels *in 2. loco* …, the Cistercian short lesson and
  invitatory of three lessons, the commemorations of an octave, the Dominican
  versicle before Lauds, Compline's *Nunc dimittis* antiphons, the lessons of
  Our Lady on Saturday by month. Sections the engine never reads by name —
  the data's building blocks (`AntMatutinumM`, pulled into `Ant Matutinum` by
  reference), another family's variants, switches — are left out, and the
  Ascensiontide check still finds every line.
- **What was left under *Alia*.** Every section that still went to *Alia* was
  traced to whether the engine reads it by name or the data only pulls it into
  another section (43 in a Monastic 1963 book, 26 in a 1960 one). None remain
  in Monastic 1963.
  - **Read by name, now placed:**
    - the hymns' doxology for a feast (`[Doxology]`, Transfiguration …) and
      All Souls' close of every hour (`[Conclusio]`), under a new heading at
      the head of the office: *In toto Officio / Throughout the Office*;
    - the prayer of the weekdays after Trinity Sunday (`[OratioW]`, at
      Lauds);
    - Christmas's First Vespers chapter;
    - a Common's prayer for several Popes (`[Oratio pro plurium]`);
    - the commemoration of an octave (`[Octava]`);
    - the Tridentine books' own responsories (`[ResponsoryT2]`, in place of
      the other; left out elsewhere).
  - **Left out:** building blocks such as the Saturday antiphons of Our Lady
    (`Ant VesperaBMV`) or Jude's lessons, and sections no rule reads (a
    placeholder `from tempora`, St Anselm's `AltLectio`).
  - **All Souls:** the order of each of its hours is written with
    `&special('Initial')`, `&special('Oratio mortuorum')`. The engine expands
    these against the day it is saying, and the dump's day is another, so
    they came out empty: the hours lacked their opening and their prayer, and
    the parts were printed loose under *Alia*. `engine_dump.pl` now expands
    them from the office's own sections, which are then not printed again.
  - **Other versions:** a few rare ones remain in the Barroux, Cistercian
    and Dominican books.
- **The Commons, told apart.** The data has a file for each form of a Common:
  - `C2-1` is the Common of one Martyr Bishop with the prayer and third
    Nocturn of its second Mass, *Sacerdotes Dei*;
  - `C2a` is for a martyr who was not a bishop;
  - `C10a`, `C10b` … are Our Lady on Saturday by season.

  A form that takes its parent whole takes the parent's `[Officium]` too, so
  a monastic volume printed five Commons as *Commune Unius Martyris Pontificis* and
  six as *Sanctæ Mariæ Sabbato*. The monastic Evangelists were *Commune
  Apostolorum*. Each was printed whole, about 200 pages in a monastic volume.
  Now:
  - **Names:** each Common is named as the website names it
    (`horas.dialog [communes]`), with an English name. Where two would read
    alike, the Mass is added: *Commune Unius Martyris Pontificis (Missa
    Sacerdotes Dei)*. For Our Lady on Saturday it is the season: *(in
    Adventu)*.
  - **Contents:** a form whose parent is in the book prints only what is its
    own (for `C2-1`, its prayer and lessons IX–XII), then *Cetera ut in:
    Commune Unius Martyris Pontificis (Missa Statuit)*. A form identical to
    its parent is that line alone.
  - **The saints:** their notes use the same names.
  - **Size:** the monastic Common (without the alternative lessons) went from
    15,690 lines of text to 4,531.
  - **Self-references:** C10 had pointed "the rest" at itself (`vide C10`);
    it no longer does.
- **References to nowhere.** A few references in the data lead to no text,
  and the engine prints its own "Sancti/07-02:Capitulum Sexta is missing!" (so
  does the website). The book leaves those notes out; the Dominican ones that
  mean "the first antiphon of Lauds, with these psalms" (Epiphany, the
  Apostles) are completed from the office's own antiphons.
- **A font left open.** Palm Sunday's Passion under the 1960 rubrics starts
  with `v.~`, a large initial joined to the title on the next line ("Pássio
  Dómini…"). The engine makes a `v.` line's first letter the initial before
  it joins the lines, so the initial is of nothing. Its `setfont()` then
  returns a `<FONT SIZE='+2' COLOR="red">` with no `</FONT>`, and the whole
  Passion, in both languages, was set as one huge red title (the website does
  the same). `engine_dump.pl` joins the two lines first, so the initial is
  the P of *Pássio*. A `!` line with nothing after it opens a red font the
  same way (the English Introit of the Sunday after Epiphany); such an empty
  opening tag is now dropped. No section of any version's Masses or offices
  has an unclosed font left.
- **Quotes that drew as boxes.** Some English texts were once in
  Windows-1252 and were read as Latin-1, so their curly quotes and dashes
  are control characters (U+0092 for ’, U+0094 for ”, U+0097 for —). No font
  draws those. There were eight boxes in a two-volume monastic set: "the
  lion’s mouth" in Palm Sunday's Tract, "Holy Zion’s Help" in the hymn of the
  Dedication, the Seven Sorrows hymn. `dooffice.parse` reads them as the
  characters they were.
- **Running heads** now name an office that *begins* on the page, not the
  one in force at its top (which put the Saturday of the Psalter over the
  first page of Advent). The dated PDFs share the fix.
- **The Gospel at monastic Matins.** On a day of twelve lessons the monastic
  office reads the Gospel of the day after the twelfth responsory, before the
  *Te decet laus* (monastic.pl, `lectioE`). The engine takes it from the
  office's own `[Evangelium]`, else from the day's *Mass* (the same file
  under `missa/`, without the M/Cist folder), else from the Common. The data
  keeps most of them with the Mass, where a book read from the office files
  never looked: only the Commons and 18 offices printed theirs, and 85
  Sundays and feasts (the Sundays of Advent and after Epiphany and Pentecost,
  the Epiphany, the Purification, St Joseph, Sts Peter and Paul …) went from
  Responsory XII straight to Lauds. The dumper now looks where the engine
  looks, on the days it reads a Gospel (the same test: *12 lectiones* in the
  rule, or in the older monastic versions a rank of three Nocturns), and the
  book prints it in both languages, a Gospel already printed in the same book
  or volume as a reference. Checked against the engine itself: every Gospel
  its Matins read on the 175 days of 2026–27 that have one (87 different
  Gospels) is in the book. Two Mass Gospels (the 2nd Sunday after Easter, the
  7th after Pentecost) end in `~`, the data's "joined to the next line", and
  `resolve_refs` drops a last line that waits for one; the dumper drops the
  `~`. The Gospel is placed after Responsory XII. The Commons' Gospels had
  been printed between the ninth lesson and the tenth, where a Roman Matins
  ends, and now follow Responsory XII too. So does the Te Deum. The data
  ends the last lesson with it, as a Roman Matins would, so the book printed
  *Te Deum* before Responsory XII, on days of three lessons, and not at all
  where the lessons have none (Advent I). The monastic engine takes it off
  every lesson and says it only after the twelfth responsory
  (`tedeum_required`: `$num == 12`; checked: its Matins of three lessons,
  Easter Monday or a feast of the third class, has none). The dumper now
  does the same: no lesson keeps it, and the 164 Monastic 1963 offices of
  twelve lessons get a line of their own after Responsory XII, so the order
  reads Responsory XII, *Te Deum, ut in Ordinario*, Gospel. The Roman office has no such
  reading: its Matins goes from the ninth lesson to the Te Deum, and its
  Gospel is the opening words at the head of the homily (*… Et réliqua*),
  which the book already prints. Measured on a half-letter set in four
  volumes (7 pt, alternatives off): the Gospels, Christ the King and the
  Second Sunday after Christmas (below) add 38 pages, from 626, 680, 621
  and 631 to 635, 685, 628 and 648. Referring to repeated lessons (see *A
  repeated text printed once*) then takes 84 off: 615, 662, 608 and 629
  pages, all 18,862 references checked. The same set of *Rubrics 1960 - 2020
  USA* went from 586, 659, 633 and 623 to 566, 639, 614 and 603 pages
  (15,706 references, all checked).
- **Offices only the transfer tables name.** Christ the King is kept on the
  last Sunday of October, so no calendar lists it: the version's transfer
  tables put it on 25–31 October year by year (`10-29=10-DU`), and the Proper
  of the Saints, read from the calendar, never had it, in any version. The
  dumper now reads the transfer tables over four hundred years (every
  dominical letter with every date of Easter) and adds the feasts they put on
  a Sunday after the last day they can fall on, dated *25–31 Oct* in the
  contents (Christ the King; in the Tridentine versions also the Precious
  Blood, the Seven Sorrows and the Rosary on their Sundays). The Proper of
  the Season likewise gains the offices only those tables name, unless the
  file is another office of the book over again with a change or two (the
  Sunday within the Octave on 29 December). In Monastic 1963 that is the
  Second Sunday after Christmas (*Dominica Secunda Nativitatis*, between 2
  and 5 January), with its own lessons and Gospel.

From the command line: `DivinumOfficium.exe --make-breviary --parts
kalendarium,ordinarium,psalterium --version "Monastic - 1963" -o breviary.pdf`
(the parts: `kalendarium orationes ordinarium psalterium tempora temporis sancti
commune missa`; also `--lang2`, `--paper`, `--size`, `--black`, `--font`, `--align`,
`--rubric-colour`, `--text-colour`,
`--new-page-per-office`, `--no-alternatives`).

### The English the data lacks

Where the English files have no text for a section, the engine gives the
Latin, and the English column printed it: 813 sections of a Monastic 1963
book, about 190 of a 1960 or Divino Afflatu one (Matins lessons of the
monastic feasts, their responsories and antiphons, homily titles, a few
hymns). `translations-en.json` (beside the exe) supplies the English for those
lines, and both the breviary and *Make a PDF* use it. It has 1,342 lines from
three sources, marked on each entry:

- `do` (117): the same Latin translated elsewhere in the English data — the
  Roman office's copy of a responsory a monastic feast repeats, the Gospel of
  Matins whose English is only in another Mass (Palm Sunday's from the
  blessing of palms, St Gregory's from the Mass of a Coronation), and so on.
  Pairs that turned out to be misaligned in the data, metrical hymn versions
  that do not follow the Latin line by line, and citations with other numbers
  were left out and translated instead.
- `dr` (37): Bible verses in the Douay-Rheims (Challoner), which is the
  translation the English data uses for the psalms, lessons and Gospels;
  read from drbo.org.
- `ai` (1,188): the rest, translated for this project literally, in the
  "prayerbook English" of the other texts (thee and thou, *Holy Ghost*,
  *Homily by St. N., Bishop*).

**The AI translations are marked.** Each `ai` line ends with a small grey
superscript *AI* — in the breviary and in *Make a PDF*, and on a reference
to such a text — and the title page says what the mark means (*English
marked AI was translated for this edition by AI, where the Divinum Officium
English has none*). The `do` and `dr` lines are existing translations and are
not marked. The mark takes no room: the Monastic 1963 book above is 716 pages
with it and without it, carrying 1,321 marks. *Mark English translated by AI*
under Page (on by default) or `--no-ai-mark` turns it off, note included.

A line is found by its Latin, ignoring accents, j/i, æ/ae, the labels the book
adds (℟., ℣., *Ant.*, numbers) and an antiphon's psalm, so one entry serves
every version that has the text. Only lines of the three versions above were
translated; the others gain wherever they share those texts. What is left in
those three is not missing English: *Conclusio specialis* is a directive, and
*Alleluia* is the same in both. St George's ninth lesson (`Lectio94`) was
English in the Latin file as well. It now has the Latin: the historical
lesson approved for the dioceses of England, as Guéranger's *Liturgical Year*
prints it (one full stop after *fuisset* made a comma). The correction is kept
in `data-fixes.txt` (see *Corrections to the data*). To add a line, add an
entry under `entries` (the key is what `dotranslate.key()` gives for the
Latin) and run `build-exe.py --quick`.

### The Coverdale psalter

*English - Coverdale psalter*, in the language lists of the breviary, *Make a
PDF* and the site, is the English with the psalms of the Book of Common Prayer
(Miles Coverdale's, pointed for singing) in place of the Douay's: all 150
psalms, the Venite at the invitatory, and the Magnificat and Benedictus.
Everything else is the English as before: antiphons, lessons, the other
canticles (the Nunc dimittis among them). So an antiphon may quote its psalm
in the Douay's words beside the Coverdale.

It is a language of its own to the engine, `English-Coverdale`, whose folder
holds only these files; whatever a folder like that lacks, the engine takes
from the language before the dash (as `Polski-Newer`, a Polish psalter, does
already). `tools/make_coverdale.py` makes the files, into
`data-additions/English-Coverdale/`, from

- `sources/coverdale/psalter.txt`: the Prayer Book's thirty-day psalter, as
  given;
- `sources/coverdale/canticles.txt`: the Magnificat and Benedictus, with the
  verse numbers (Luke 1), a `*` at the Prayer Book's colon and a `+` for the
  sign of the cross added.

`datafixes.py` copies `data-additions/` into the data with the corrections
(see *Corrections to the data*), and an entry in `data-fixes.txt` adds the
language to `horas.dialog`: the engine accepts only the languages listed
there. After changing a source:

```bash
python tools/make_coverdale.py
python datafixes.py
```

What the script does with the psalter:

- **Takes the psalms and verses only.** It leaves out the day headings,
  *Morning/Evening Prayer*, and the Gloria and Amen after each psalm (the engine
  adds its own Gloria). It joins the psalms the thirty days divide into parts
  (Psalm 119's twenty-two; I. and II. of 18, 37, 78, 89, 105–107), numbers the
  first verse of each psalm or part, and joins verses broken over two lines.
- **Numbers the psalms as the Latin does.** The Prayer Book's 110 is the
  Latin's 109. The Latin's 9 is the Prayer Book's 9 and 10, its 113 is 114
  and 115, its 114 and 115 are 116, and its 146 and 147 are 147.
- **Numbers the verses as the Latin does.** The Prayer Book divides verses
  its own way: 86 psalms have a different number of verses from the Latin.
  So each psalm is lined up against the Douay English, which follows the Latin
  line for line, by the words they share, and each Coverdale verse takes the
  number of the Latin line beside it:
  - one verse beside one line: 2,232;
  - one verse holding two of the Latin's lines: 155;
  - two verses sharing one line: 52 (the second takes the number printed inside
    that line, "(9)", or else b);
  - other groupings: 4.

  One place the two divide too differently to line up by words (Latin
  111:5–7) is numbered by hand in the script.
- **Checks the result.** Every place the Latin's schedule divides a psalm
  (`118(33-48)`, `36(27-40)`, 173 in all) must fall between two Coverdale
  verses, so each part has the right verses, and every verse must have one `*`.
- **Corrects the source:**
  - the `*` three verses lacked (4:1, 5:3, 11:1; the accents show where);
  - the parentheses of 7:4 and 49:8, since the engine prints anything in
    parentheses as a red rubric;
  - two stray letters (*LŐRD*, *İ*).

  The pointing's accents and `†` are kept.
- **Fits the Venite to the invitatory's five parts.** It carries the marks at
  which the engine cuts the invitatory on some days, so in Passiontide it
  begins at *As in the provocation*. The same text, in the Latin's divisions,
  is Psalm 94 (Epiphany).

**Pairing the columns.** The book sets each Coverdale verse beside the Latin
verse with the same number. Where one Coverdale verse is the Latin's 50:3a
and 50:3b, the 3b line has space beside it, and the reverse where two
Coverdale verses share one Latin line. This pairing by verse numbers is used
wherever a psalm's two columns differ, and the references to repeated text
(*usque ad*…) work on the same rows. The Douay psalms pair line for line, as
before. The 1960s versions print no verse letters, so a Latin verse in two
lines shows its number twice.

**Measured** on the Monastic 1963 book at the settings of run 15 (A4, Times
New Roman 7 pt, alternatives off):

- **Pages:** 743 with the Coverdale psalter, 739 with the Douay. The 4 extra
  pages are the space beside verses the two divide differently.
- **References:** `tools/audit_references.py` verifies all 7,764 of the
  Coverdale book and all 7,754 of the English one.

The pairing by verse numbers also helps the Douay English. Where it joins two
of the Latin's lines (Isaiah 33:9, Ecclesiasticus 36:18), the rest of the
canticle no longer slips out of step. Since references now work on paired
rows as well, the English book gained 36 references and lost 2 pages (722 to
720).

### One language

*Right: — none (one column) —* makes a book of one language, the left one.
Its text runs the width of the page unless **Two columns per page (one
language)** is ticked (under the languages; `--two-columns` on the command
line): then the text flows in two columns, as printed breviaries set it. Most
of a breviary is short lines -- verses, antiphons, versicles -- that leave the
right half of a wide line empty, so two columns save a third of the pages on
Letter or A4, and about a sixth on half letter, where the columns are narrow.
The title page, the contents and each part's title page stay across the page,
and so does the Calendar (its table is the page's width); a part's title page
is set in one column and the part begins on the next page in two. An office's
title is a little smaller in a column (1.12em against 1.25em), so a long one
breaks less often.

**In English**, the book is labelled in English: an English book (English or
English - Coverdale psalter on the left) used to print the English texts
under the Latin headings the two-language book gives the left column
(*Lectio I*, *Responsorium*, *Ad Laudes*, *Capitulum*). The first column's
labels now follow its language: section headings (*Lesson I*, *Responsory*,
*Chapter*, *At the Benedictus*), the hours (*At Lauds*, *At Second
Vespers*), the parts (*Proper of the Season*, in the contents, bookmarks,
running heads and title page), the Psalter's days and the Calendar's months,
the notes (*The rest as in*, *Commemoration*), the Sunday Masses (*Introit*,
*Epistle*, *Collect, as in the Office*), and the references (*Ant. at I
Vespers, p. 8*). A book with English on the left and Latin on the right gets
the same, with the Latin beneath. The names of the offices are the English
data's own, which keeps some in Latin (*Feria II infra Hebdomadam I
Adventus*).

Things that turned up on the way:
- **The Martyrology of 14 January in All Souls.** All Souls' own order of
  Prime (and the monastic commemoration of the Order's dead) says the
  Martyrology at its place: `&special('#Martyrologium')`, which the engine
  fills with the day's entry -- the day the dumper sets, 14 January 2026. Every
  book printed that date's martyrology in the office of the dead. The dumper
  now puts a rubric there (*Martyrologium, ut in Ordinario · The Martyrology,
  as in the Ordinary*).
- **A reference naming "Martyrology {anticipated}".** The engine's note after
  a heading was taken into the name a reference gives (*Martyrology
  {anticipated}, p. 472*); the name is now the heading alone.
- **Orémus beside the English prayer.** Where the English has no *Let us
  pray*, the two columns, the same length, were paired straight across: the
  English collect beside the Latin's *Orémus*, and the Latin collect a line
  lower, beside a blank -- and where a page ended between them, the English
  referred a page earlier than the Latin (found at the settings of
  October 2026, Letter, 7 pt; the same in the app's code before these
  changes). *Orémus* and *Let us pray* now pair only with each other, or with
  a gap.
- **Checking a book of one language.** `tools/audit_references.py` read
  every page as a Latin column and an English one. A book with only "pag." or
  only "p." in it (or `--one-language`) is now read as one text -- a page in
  two columns, its left column and then its right; a page across its width,
  line by line -- with every check but the pairing of the two languages.

**Measured** on Monastic 1963 (the Monastic Ordinary and Psalter, Commons and
Propers, alternatives off, 7 pt; the Coverdale psalter for the English), every
reference checked:

| Book | Letter | Half letter |
|---|---|---|
| Latin and English | 693 pages | 1,361 |
| Latin, one column | 490 | 768 |
| Latin, two columns | 330 | 639 |
| English, one column | 514 | 836 |
| English, two columns | 362 | 707 |
| Latin, two columns, 4 volumes | | 293, 313, 290, 299 |
| English, two columns, 4 volumes | | 319, 343, 316, 324 |

The window's estimate takes one language as 76% of two on Letter and A4 and
50% on the smaller papers (against its own estimate of two languages), and
two columns as 69% and 84% of one.

### The Sunday Masses

*Proprium Missarum Dominicalium — Proper of the Sunday Masses* is a part like
the others: ticked in the list (it starts unticked, at the end of the list:
it is an addition to the breviary, not part of it) and moved with ▲ ▼ to the
start or the end of the book, or of each volume. Measured on four volumes
with it first: every one of the 19,026 references checked. For each Sunday of the Season, and for Christ the
King, it prints the proper of the Mass in the order it is said: Introit, a
rubric for the Collect (*Oratio, ut in Officio · Collect, as in the
Office*: the Mass's Collect is the office's own, printed with the Sunday),
Epistle, Gradual (with the Alleluia, or the Tract in Lent), the Sequence at
Easter and Pentecost, Gospel, Offertory, Secret, Communion, Postcommunion.
The Kyrie, Gloria, Credo and the rest of the Ordinary of the Mass are not
proper and are not printed.

The texts are the project's own Mass data (`web/www/missa`), read through the
engine like the office's: `engine_dump.pl --part missa` loads each Sunday's
Mass file with `setupstring()` (version conditions, `@` references,
language fall-back) and expands it with `resolve_refs()`. The file is the one
the engine reads the monastic Gospel from (`lectioE`: the office's file under
`missa/`, without the M/Cist folder), so every version finds its Masses:
55 Sundays in Monastic 1963 (the Second Sunday after Christmas and Christ
the King among them), 54 under the 1960 rubrics and in the USA calendar. Each
Sunday is named as in the Proper of the Season. The English of the Mass data
is a modern one (*You, Your*), not the breviary's *thee* and *thy*. The
Glória Patri of the Introit and the Per Dóminum of the Secret and
Postcommunion are printed by their first words, as in the rest of the book,
and a text printed earlier in the volume (the Sunday's Gospel, already read
at monastic Matins) by its first words and page.

**In volumes**, each Sunday's Mass goes into the volume(s) its Sunday can
fall in, exactly as its office does in the Proper of the Season: the winter
volume has the Masses of Advent to Septuagesima, the autumn volume those of
the late Sundays after Pentecost, with the Sundays after Epiphany said again
in November, and Christ the King by the days it can take (25 to 31
October). A Sunday that can fall in two volumes is in both.

Two things turned up on the way:
- Reading a Sunday's office file just after its Mass file made the engine go
  round its references for ever (the 3rd Sunday after Pentecost, Monastic
  1963): `setupstring()` keeps its caches by file name, and a Mass with no
  file of its own is read from the office's under that name. The dumper reads
  every Sunday's title before any Mass file.
- Palm Sunday's Passion has lines that open with ✠, which the book sets as
  a line's large initial; the cross's own larger size was added to the
  initial's, and Typst refused the duplicate. A cross now keeps the size of
  the run it is in when that run has one.

**Measured** alone at the estimate's settings (1960, Letter, 10 pt, two
languages): 79 pages, built in 4 s.

### Blank pages

*Blank pages: front … back …* in the Page box (`--blank-front N`,
`--blank-back N`) adds empty pages to each book or volume: at the front,
before the title page, and at the back, after the last page -- for
end-papers, or a page count a printer asks for. The front ones come in pairs
(an odd number is taken down by one): one page more would put the title page
on a left-hand page and every page's binding margin on the wrong side. They
are added when the volume is put together, so the printed page numbers do not
change; the bookmarks and the contents' links move with them, and in the
viewer the pages before the title take roman numbers with the title and
contents. Checked on a small book (front 3, back 3: 2 and 3 pages; 320 pages
to 325): every bookmark and contents link one page further on by two, all
5,046 references verified as before.

### Custom parts

**Add custom part…** (beside *Include the alternative lessons*, under the
parts list) asks for a `.txt`, `.md` or `.pdf` file and adds it to the list as
a part of its own, ticked, at the end. It can then be moved with ▲ ▼ like any
other part, unticked, given another file (**Change…**) or taken out (**✕**).
There can be as many as wanted. The list scrolls once it is longer than eight
rows, with the mouse wheel or its scroll bar. The files are remembered with
the other settings. A file that has since been moved or deleted is shown as
*file missing*, and while it is ticked the PDF can't be made until the
file is changed or the part is removed.

- **A PDF** is put in page by page, each page scaled to fit the book's page
  (centred, keeping its proportions). Its bookmark and contents entry take
  the file's name.
- **A Markdown file** is named by its first heading (`# Prayers before Mass`),
  or by the file's name if it does not open with one. Paragraphs are set as
  prose. Headings are set in the book's heading style. Lists are set with
  • or 1. 2. 3. Quotations are set in italic. `**bold**` and `*italic*` are
  set as such. `` `code` ``, links and images keep only their text.
- **A plain text file** is named by its file name, and each line is set as it
  is written. A blank line leaves a line's space.

A text part is set in the book's type and size, and in its two columns when
it has them. It is not translated, shortened or cross-referenced. **In
volumes**, a custom part goes into every volume, in its place. The estimate
counts a PDF's pages and gives a text file its pages roughly by its length.

From the command line, `--custom FILE` (repeatable) adds the parts `custom1`,
`custom2` … in that order. Name them in `--parts` to place them, otherwise they
come last:

```
python dobreviary.py --custom "Graces.txt" --custom "Prayers.md" --parts custom2,orationes,psalterium,custom1 -o out.pdf
```

Checked with three at once (a 2-page PDF first, a Markdown file after the
prayers, a text file last) in a book of two volumes: each part in both
volumes, in the order given, with its bookmark.

### A breviary in volumes

**Volumes** (the window's lower left) makes the breviary as two, three or four
books, divided by the liturgical year, as printed breviaries were. Each volume
is a book of its own, with its own title page, contents, page numbers and
references; a reference never sends you to another volume. Every volume has
the parts not ordered by season (the Calendar, the prayers, the Ordinary, the
Psalter, the Common of the Saints) entire, and of the Proper of the Season, the
Proper of the Saints and the Common of the Seasons what falls in its time.

Volume I always begins with Advent. Where each of the others begins is chosen
from: Christmas, the Epiphany, Septuagesima, the first Sunday of Lent, Passion
Sunday, Easter, Pentecost, Trinity Sunday, and the first Sunday of August,
September, October or November. The usual division for each number:

| Volumes | Begin at | Names |
|---|---|---|
| 4 | Advent, Lent I, Trinity Sunday, first Sunday of September | *Pars Hiemalis, Verna, Æstiva, Autumnalis* |
| 3 | Advent, Easter, first Sunday of September | *Pars Prima, Secunda, Tertia* |
| 2 | Advent, Trinity Sunday | *Pars Prima, Secunda* |

Four is the Roman Breviary's division, which the [EWTN introduction to the
Roman Breviary](https://ewtn.com/catholicism/library/introduction-to-the-roman-breviary-11837)
describes. It is also the most even of all the four-volume divisions; the two-
and three-volume ones are the most even for those numbers, measured on the
Monastic book. The window warns if the chosen points are out of the year's
order (Lent I before Septuagesima can never be). Volumes are saved beside the
name chosen, as *Breviarium Monasticum (Monastic - 1963) - I. Pars
Hiemalis.pdf* and so on: one complete PDF each, every font embedded, to send to
a printer (Lulu and the like) as a book by itself. The save dialog says how
many files it will make and how they are named. *Open PDF* opens the first
and *Show in folder* the folder with all of them. On the command line: `--volumes 4`, or `--divide
lent,trinity,september`.

**What goes in which volume** (`dovolumes.py`). Many points move with Easter,
so a fixed feast near one falls in one volume some years and in the next in
others. A printed breviary gives such a feast in both volumes, and so does
this. Likewise for the Sundays after Pentecost against a volume that begins
on the first Sunday of a month, and for the Sundays after Epiphany said again
in November when there are more than twenty-four Sundays after Pentecost
(given there, before the last Sunday). It is not a table: every day of every
kind of year is walked through, its office named as the engine names it (a
port of `Date.pm`'s `getweek` and `monthday`), and each office goes in every
volume its days can fall in. A year's offices depend only on the weekday it
begins on, whether it is a leap year and the date of Easter: seventy kinds,
each taken from a real Gregorian year. The months' first Sundays follow the
version: the Sunday nearest the first, or under the 1960 rubrics the first
Sunday on or after it.

For the four Roman volumes this gives what the printed ones have:

| | Proper of the Season | Saints | Printed |
|---|---|---|---|
| *Hiemalis* | Advent to the Saturday before Lent I | 27 Nov – 13 Mar | 26 Nov – 12 Mar |
| *Verna* | Lent I to the Ember Saturday of Pentecost | 8 Feb – 19 Jun | 7 Feb – 19 Jun |
| *Æstiva* | Sundays I–XV after Pentecost, August's weeks | 17 May – 3 Sep | 18 May – 2 Sep |
| *Autumnalis* | Sundays XI–XXIV after Pentecost, September–November | 29 Aug – 2 Dec | 28 Aug – 2 Dec |

That is with the older reckoning of the first Sunday of September. Under the
1960 rubrics the autumn volume begins at Sunday XII after Pentecost, with the
saints from 1 September, and the summer one runs to Sunday XVI and 6
September. Each volume's saints are in the order of its
time: the winter volume's from late November through December into March.

**Measured** on the Monastic 1963 book at the settings of run 15 (Coverdale
psalter; one book: 743 pages):

| Volumes | Pages of each | In all | Time |
|---|---|---|---|
| 4 (Roman) | 344, 374, 341, 346 | 1,405 | 135 s |
| 3 | 414, 429, 346 | 1,189 | 122 s |

And *Rubrics 1960 - 2020 USA* (Letter, 10 pt, Latin and English, the
alternative lessons in; one book: 1,671 pages):

| Volumes | Pages of each | In all | Time |
|---|---|---|---|
| 4 (Roman) | 725, 809, 785, 764 | 3,083 | 86 s |

Its calendar adds the American feasts (St Peter Chanel, Padre Pio, St
Martin de Porres and others, the data's `MM-DDn` files), which fall in their
volumes by date like the rest. Under the 1960 rubrics the second week of
November never comes, but its office is in the data and the book prints it;
it goes where the older reckoning puts it, in the autumn volume.

Each volume's references were checked with `tools/audit_references.py` and
all were verified, 4,182 to 5,104 a volume, none wrong. The engine's text is
gathered once for all the volumes, and each is then shortened, referred and
typeset by itself.

### Known limits

- The seasons are sampled on one week each (the octaves and Ember days are
  kept out of the sample weeks, since they would put their own texts there);
  a season's text that belongs to a single feast of the season is in the
  Proper of the Season instead.
- The Common of the Saints prints *N.* where the day names its saint
  (*beátum N.*, in the *O Doctor óptime* antiphon and some collects); those
  offices of the Proper of the Saints that only refer to the Common
  (*Oratio ut in Communi*) therefore read with *N.*, as in a printed book.
- Christmastide and Epiphanytide have no entry in the Common of the Seasons:
  their texts are in the Proper of the Season.
- The Ordinary's rubrics are the builder's own, in Latin (and English beside
  an English column); the engine has no such rubrics to give. The rules for
  the absolutions and blessings are the engine's code (`specmatins.pl`,
  `monastic.pl`) written out; checked against what the engine itself says on
  each day of the sample week, they agree for all 21 days (1960, Divino
  Afflatu, Monastic 1963). The Cistercian ferial blessings follow its own
  lists, less closely checked.
- The monastic chapter for Ascensiontide is not kept with the other seasons'
  (in `Matutinum Special.txt`) but in the offices after the Ascension
  (`TemporaM/`), and it differs by version — *Dignus est Agnus* (Apoc 5:12)
  in 1963, 1 Pet 4:7-8 in 1930 — so it is read from the rendered weekday
  after the Ascension, as the engine says it.

**Checked for Ascensiontide.** Every line the engine says on each hour of
5–15 May 2027 (the Ascension, its octave, the Sunday after, St Robert
Bellarmine, Pentecost's eve …) was looked for in the book built for the same
version, for all fifteen versions — 9,000 to 12,000 lines each, hymn stanzas
included. Everything is found except the saint's name in texts the Common
gives with *N.* (above), the Evangelist's name in the monastic Gospel after
the Te Deum (the Ordinary has *secundum N.*), and three things of the
Cistercian and Dominican books: the Cistercian invitatory psalm, which the
engine breaks into lines differently on those days, the Dominican festal
versicle before Lauds, and the Dominican Eastertide invitatory of 8 May.

## Performance

Plain CGI means a fresh Perl process per request. Measured here (from a OneDrive
folder, which does not help):

| | |
|---|---|
| Vespers, 10 runs | min 0.77 s, **avg 0.86 s**, max 0.96 s |
| Matins | 0.90 s |
| Full-year Ordo (`kmonth=14`) | **6.1 s** |

Where that 0.86 s goes: 0.05 s bare interpreter, 0.08 s with the CGI stack
loaded, 0.15 s to compile `officium.pl`. So only ~0.15 s is process overhead —
the remaining ~0.7 s is the liturgical calculation and reading dozens of `.txt`
files. Fine for one person praying the office. If you want it faster:

- **Persistent workers.** `plackup test_env.psgi` compiles the scripts once,
  which removes the 0.15 s and lets the modules' in-memory caches survive
  between requests. Needs `cpanm Plack Plack::App::CGIBin CGI::Compile
  CGI::Emulate::PSGI` (~30 dists; Strawberry's bundled gcc handles it).
  Note `test_env.psgi` mounts only `/cgi-bin/horas`, `/cgi-bin/missa` and
  `/www`, and redirects `/` straight to `officium.pl` — it does not serve
  `index.html`.
- **The full-year Ordo is the only genuinely slow page.** Production doesn't
  solve this with more workers; `app.psgi` intercepts `kmonth=14` and serves a
  pre-rendered file from `web/ordo-cache/`, warmed nightly by
  `warm-ordo-cache.sh`. If you use that page, pre-render it once rather than
  paying 6 s each time.
- **Build the lexicon Storable** if you want the interlinear gloss:
  `perl lexicon-tools/build_lexicon_storable.pl`. Without it, `Lexicon.pm`
  falls back to parsing a 2.5 MB JSON file per request; with neither file it
  degrades silently to no glosses.

## Slimming the data

`web/` is 190 MB, but not all of it is liturgy:

- **7 PDFs in `web/www/horas/Help/Rubrics/` are 92.8 MB** — half the install.
  They're reference literature (`EnglishDORubrics.pdf` alone is 41.8 MB), linked
  only from the Help pages. Deleting them takes `web/` to **~98 MB** and breaks
  nothing but those links.
- **3,341 `.gabc` files, 10.6 MB** — Gregorian chant notation for the
  `Cantilenæ` option. Drop only if you don't want chant.
- `.git` is another 143.6 MB even shallow-cloned. Once you have the files you
  can delete it; re-clone when you want updates.

Floor for a working install: **~98 MB** of data plus 0.6 MB of program.

Two practical notes: clone with `--depth 1` (full history is much larger), and
**move this out of OneDrive** — 36,000 small files in a synced folder means
constant sync churn, and directory traversal here was measurably slow.

## If you want zero server at all

`standalone/tools/epubgen2/` generates static/eBook versions of the office. The
top-level `standalone/README` is a placeholder, but the generator has its own
README. That's the right answer if you want the text offline on a reader rather
than a site you can click around.

## Sources

- [Download / install page](https://www.divinumofficium.com/www/horas/Help/download.html)
- [GitHub repo](https://github.com/divinumofficium/divinum-officium)
- [Python: pending removal in 3.15](https://docs.python.org/3/deprecations/pending-removal-in-3.15.html)
- [Strawberry Perl 5.40.2.1 release notes](https://strawberryperl.com/release-notes/5.40.2.1-64bit.html) (bundled module list)
