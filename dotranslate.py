"""
English for the texts the English data does not have.

Where the engine's English files lack a section, it gives the Latin instead,
and the book printed it in the English column. translations-en.json supplies
the English for those lines, from three sources, in this order of preference:

  do    the same Latin elsewhere in the project's own English data (the Roman
        office's copy of a responsory a monastic feast repeats ...);
  dr    Bible verses in the Douay-Rheims (Challoner), the translation the
        English data uses for Scripture;
  ai    the rest (homilies, lives, hymns, antiphons ...), translated for this
        project literally, in the English of the other texts. Such a line
        carries `ai`, and the PDF marks it with a small grey "AI" (Layout.mark_ai).

Lines are matched on their Latin, blind to accents, j/i, æ/ae and the labels
the book adds (℟., ℣., Ant., antiphon and verse numbers), so one entry serves
every version and both spellings.
"""

import json
import os
import re
import sys
import unicodedata

import dooffice

FILE = "translations-en.json"


def default_path():
    """translations-en.json: beside the source, beside the app folder's code
    (app/..), or beside the exe."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, FILE), os.path.join(os.path.dirname(here), FILE)]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates += [os.path.join(exe_dir, FILE), os.path.join(exe_dir, "_internal", FILE)]
        if getattr(sys, "_MEIPASS", None):
            candidates.append(os.path.join(sys._MEIPASS, FILE))
    return next((c for c in candidates if os.path.isfile(c)), None)


def translate_days(items, lang1, lang2, path=None):
    """Fill the English column of everything with hours (Days, Offices); how many lines."""
    lang1, lang2 = dooffice.family(lang1), dooffice.family(lang2)  # English-Coverdale is English
    if "English" not in (lang1, lang2):
        return 0
    column = 1 if lang2 == "English" and lang1 != "English" else 0
    table = Translations.load(path or default_path())
    if not len(table):
        return 0
    n = 0
    for item in items:
        for h in item.hours:
            for s in h.sections:
                n += table.fill_section(s, column)
    return n

# A label before the text: kept as it is, the text after it is translated.
_PREFIX = re.compile(r"^\s*((?:℟\.\s?br\.|R\.\s?br\.|℟\.|℣\.|R\.|V\.|Ant\.|\*|\d+:\d+[a-z]?(?=\s)"
                     r"|\d+\.|\d+[a-z]?(?=\s))\s*)+")


# What the book adds after a text: an antiphon's psalm ("Ps. 84"), a canticle's
# number ("(Ps. 243)", "[13]").
_SUFFIX = re.compile(r"\s*(\(Ps\.[^)]*\)|\[\d+\]|Ps\.\s[\d\s,()-]+)\s*$")


def key(text):
    """The lookup key of a Latin line: its letters only, from the first word of text."""
    body = _SUFFIX.sub("", _PREFIX.sub("", text or ""))
    t = unicodedata.normalize("NFD", body.lower())
    t = t.replace("æ", "ae").replace("œ", "oe")
    t = "".join(c for c in t if c.isalpha()).replace("j", "i")
    t = t.replace("ae", "e").replace("oe", "e")
    return t if len(t) >= 12 else None


def split_prefix(text):
    m = _PREFIX.match(text or "")
    return (m.group(0), text[m.end():]) if m else ("", text or "")


def with_body(line, text):
    """The line with its text replaced by `text`, its label and its look kept."""
    prefix, _body = split_prefix(line.text)
    runs = line.runs
    # Keep the label's runs (red ℟., the antiphon's number) and anything the
    # book appended after the text (the small red "Ps. 8").
    head, tail = [], []
    seen = ""
    for r in runs:
        if len(seen) < len(prefix) and prefix.startswith(seen + r.text):
            head.append(r)
            seen += r.text
        else:
            break
    rest = runs[len(head):]
    while rest and rest[-1].size == "small" and rest[-1].red:
        tail.insert(0, rest.pop())
    # A red initial (a hymn's stanza, a lesson's first word, a collect's
    # ending) takes the new first letter.
    if rest and rest[0].size in ("heading", "initial") and len(rest[0].text.strip()) == 1:
        first = dooffice.Run(**{**vars(rest[0]), "text": text[:1]})
        body = [first, dooffice.Run(text=text[1:])]
    else:
        style = rest[0] if rest else None
        body = [dooffice.Run(text=text, italic=bool(style and style.italic),
                             red=bool(style and style.red and style.italic))]
    # What of the label the kept runs do not hold (all of it, or the space
    # after "℟. br." when the space began the text's run) goes before the text.
    leftover = prefix[len(seen):]
    if leftover:
        body[0] = dooffice.Run(**{**vars(body[0]), "text": leftover + body[0].text})
    return dooffice.Line(runs=head + body + tail)


class Translations:
    def __init__(self, entries, sources=None):
        self.entries = entries  # key -> English text
        self.sources = sources or {}  # key -> "do", "dr" or "ai"

    @classmethod
    def load(cls, path):
        if not path or not os.path.isfile(path):
            return cls({})
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = {k: v for k, v in data.get("entries", {}).items() if v.get("en")}
        return cls({k: v["en"] for k, v in entries.items()},
                   {k: v.get("src", "") for k, v in entries.items()})

    def __len__(self):
        return len(self.entries)

    def english(self, text):
        k = key(text)
        return self.entries.get(k) if k else None

    def fill_line(self, line):
        """The line with its Latin replaced by the English, or None. A line
        translated by AI carries `ai`, for the mark the writer may print."""
        k = key(line.text)
        en = self.entries.get(k) if k else None
        if not en:
            return None
        new = with_body(line, en)
        if self.sources.get(k) == "ai":
            new.ai = True
        return new

    def fill_section(self, section, column):
        """Translate the Latin lines of one column of a section in place; how many."""
        if column >= len(section.columns) or not self.entries:
            return 0
        n = 0
        col = section.columns[column]
        for i, line in enumerate(col):
            if line.is_blank:
                continue
            # Only Latin finds an entry: an English line's key is not in the table.
            new = self.fill_line(line)
            if new is not None:
                col[i] = new
                n += 1
        return n
