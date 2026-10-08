#!/usr/bin/env bash
# Sets up a Linux working copy -- a Claude Code cloud session, chiefly -- to run
# the engine and make PDFs from the source (python3 dopdf.py, dobreviary.py).
# A cloud session runs it at every start (.claude/settings.json, SessionStart);
# once done it takes a few seconds. Anywhere but Linux it does nothing: on
# Windows the app's own build (dist/) carries all of this.
#
#   repo/      the upstream Divinum Officium project (a git submodule)
#   datafixes  the corrections in data-fixes.txt, written into repo/
#   Typst      the typesetter: the Linux build of the version the app ships
#   Python     pypdf (the breviary builder), PyMuPDF (tools/audit_references.py)
#
# Perl needs nothing installed: the tools give a system Perl perl-lib/ (CGI.pm,
# URI), which Perl has not carried since 5.22.

set -u
case "$(uname -s)" in Linux*) ;; *) exit 0 ;; esac
cd "$(dirname "$0")/.." || exit 0
TYPST_VERSION=0.15.1  # the one build-exe.py bundles (TYPST_URL)
BIN="$HOME/.local/bin"
problems=()

if [ ! -f repo/web/cgi-bin/horas/officium.pl ]; then
  git submodule update --init --depth 1 repo >&2 || problems+=("could not fetch repo/ (the upstream project)")
fi
if [ -f repo/web/cgi-bin/horas/officium.pl ]; then
  python3 datafixes.py >/dev/null 2>&1 || problems+=("datafixes.py failed: run it to see why")
fi

if ! command -v typst >/dev/null 2>&1 && [ ! -x "$BIN/typst" ]; then
  url="https://github.com/typst/typst/releases/download/v$TYPST_VERSION/typst-x86_64-unknown-linux-musl.tar.xz"
  tmp="$(mktemp -d)"
  mkdir -p "$BIN"
  if curl -fsSL "$url" -o "$tmp/typst.tar.xz" && tar -xJf "$tmp/typst.tar.xz" -C "$tmp"; then
    cp "$tmp/typst-x86_64-unknown-linux-musl/typst" "$BIN/typst" && chmod +x "$BIN/typst"
  else
    problems+=("could not download Typst from $url")
  fi
  rm -rf "$tmp"
fi
# For the rest of the session (the env file a SessionStart hook may write to).
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$BIN:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

if ! python3 -c "import pypdf, fitz" 2>/dev/null; then
  python3 -m pip install --quiet --disable-pip-version-check pypdf pymupdf >&2 2>/dev/null \
    || python3 -m pip install --quiet --disable-pip-version-check --break-system-packages pypdf pymupdf >&2 \
    || problems+=("pip could not install pypdf and PyMuPDF")
fi

# What a SessionStart hook prints, Claude reads: one line, or the problems.
if [ ${#problems[@]} -eq 0 ]; then
  echo "cloud-setup: ready -- repo/ with data-fixes.txt applied, Typst $TYPST_VERSION, pypdf, PyMuPDF (see CLAUDE.md)"
else
  printf 'cloud-setup: %s\n' "${problems[@]}"
fi
exit 0
