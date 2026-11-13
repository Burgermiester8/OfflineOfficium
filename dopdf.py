"""
Typeset Divine Office text as a PDF.

The text comes from dooffice.py (which runs the project's own engine); this
module only lays it out. It writes a Typst document and compiles it with the
bundled typst.exe. Typst is used because it gives book typesetting --
justification, Latin hyphenation, running heads, bookmarks, a table of
contents -- and stays fast on documents thousands of pages long.

Bilingual layout pairs the two languages line by line: every line of the
engine's output (a verse, a versicle, an antiphon) becomes one row with Latin
on the left and the translation on the right. The engine emits the two
languages with matching line structure in well over 99% of sections, so
verses stay aligned across the page, and rows are short enough that a page
can break between any two of them. Where the line counts differ, the columns
are lined up by the kind of each line, and psalm verses by their numbers
(align_columns).

Command line:
    python dopdf.py 2026-09-23 [2026-09-30] --hours Vespera,Completorium -o out.pdf
"""

import argparse
import dataclasses
import difflib
import json
import logging
import datetime as dt
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject

import dooffice

# Typst hyphenation/shaping language for each engine language.
LANG_CODES = {
    "Latin": "la",
    "Latin-Bea": "la",
    "English": "en",
    "Deutsch": "de",
    "Francais": "fr",
    "Italiano": "it",
    "Espanol": "es",
    "Portugues": "pt",
    "Polski": "pl",
    "Polski-Newer": "pl",
    "Magyar": "hu",
    "Magyar-Kaldi": "hu",
    "Bohemice": "cs",
    "Cesky-Schaller": "cs",
    "Nederlands": "nl",
    "Dansk": "da",
    "Vietnamice": "vi",
    "Hebrew": "he",
}

# Chant notation is meant for a chant renderer, not for a text breviary.
NOT_FOR_PDF = {"Latin-gabc"}

# How a title page names a language whose folder name says less.
LANG_NAMES = {"English-Coverdale": "English (Coverdale psalter)"}


def languages_named(meta):
    """"Latin and English" for a title page."""
    names = [LANG_NAMES.get(l, l) for l in (meta.lang1, meta.lang2)]
    return names[0] if meta.lang1 == meta.lang2 else "%s and %s" % tuple(names)

PAPERS = {
    "Letter (8.5 × 11 in)": ("paper", "us-letter"),
    "A4": ("paper", "a4"),
    # Book sizes of the print-on-demand printers (Lulu's names), between
    # Letter and half letter.
    "Crown Quarto (7.44 × 9.68 in)": ("size", ("7.44in", "9.68in")),
    "Executive (7 × 10 in)": ("size", ("7in", "10in")),
    "US Trade (6 × 9 in)": ("size", ("6in", "9in")),
    "Half letter (5.5 × 8.5 in)": ("paper", "us-statement"),
    "A5": ("paper", "a5"),
    "Pocket breviary (4.5 × 7 in)": ("size", ("4.5in", "7in")),
}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


@dataclass
class Layout:
    paper: str = "Letter (8.5 × 11 in)"
    font_size: float = 10.0
    font: str = "Cambria"
    new_page_per_day: bool = True
    new_page_per_hour: bool = False
    title_page: bool = True
    contents: bool = True  # only emitted when there is more than one day
    rubric_colour: str = "#a31621"
    red_in_print: bool = True  # False gives an all-black book for plain printers
    # The breviary: each office of the Propers headed in its liturgical colour
    # (with red_in_print); off, in the rubric colour like the rest.
    heading_colours: bool = True
    text_colour: str = "#000000"
    align: str = "justify"  # or "left" / "right": ragged on the other side
    accessible: bool = False  # tagged PDF for screen readers; about 3x the file size
    mark_ai: bool = True  # a small grey "AI" after English translated by AI (dotranslate.py)
    # One language only: the text in two columns on the page, as printed
    # breviaries set it (the breviary; the Calendar keeps one).
    two_columns: bool = False
    # Two languages (the breviary): a rule in the rubrics' colour between
    # them, and the first language on the outside of each page of a bound
    # book -- at the left of a left-hand page, the right of a right-hand one
    # (Writer.bi_rules).
    column_rule: bool = False
    mirror_columns: bool = False
    # Blank pages added to each book or volume (the breviary): at the front,
    # before the title page, in pairs -- an odd number would put the title on
    # a left-hand page and every page's binding margin on the wrong side --
    # and at the back, any number.
    blank_front: int = 0
    blank_back: int = 0
    # Spacing (the breviary window's Spacing box); these values are the standard.
    line_spacing: float = 1.0  # from one line to the next, against the standard
    verse_spacing: float = 1.0  # the space between verses (rows) and paragraphs
    letter_spacing: float = 0.0  # added between letters, in em (0.01 is 1% of the size)
    word_spacing: float = 1.0  # the width of a space, against the font's own
    # (top, bottom, inside, outside) in inches, top being the edge to the
    # running head; None is the paper's own. Only the breviary uses them.
    margins: tuple = None


@dataclass
class Meta:
    version: str = ""
    lang1: str = "Latin"
    lang2: str = "English"
    hours: list = field(default_factory=list)
    first: dt.date = None
    last: dt.date = None


ALIGNS = ("justify", "left", "right")

# Each paper's size in inches, and the breviary's margins on it: (top, bottom,
# inside, outside), top being the edge to the running head (the text begins
# about two lines lower). Letter, A4 and the print-on-demand book sizes keep
# everything 0.5 in from the edge, as those printers require; the smaller
# papers are as the dated PDFs set them.
PAPER_INCHES = {
    "Letter (8.5 × 11 in)": (8.5, 11.0), "A4": (8.27, 11.69),
    "Crown Quarto (7.44 × 9.68 in)": (7.44, 9.68), "Executive (7 × 10 in)": (7.0, 10.0),
    "US Trade (6 × 9 in)": (6.0, 9.0),
    "Half letter (5.5 × 8.5 in)": (5.5, 8.5), "A5": (5.83, 8.27),
    "Pocket breviary (4.5 × 7 in)": (4.5, 7.0),
}
STANDARD_MARGINS = {p: (0.5, 0.5, 0.75, 0.75) for p in (
    "Letter (8.5 × 11 in)", "A4", "Crown Quarto (7.44 × 9.68 in)", "Executive (7 × 10 in)",
    "US Trade (6 × 9 in)")}
SMALL_MARGINS = (0.35, 0.5, 0.55, 0.45)


def standard_margins(paper):
    return STANDARD_MARGINS.get(paper, SMALL_MARGINS)


def small_paper(paper):
    """A page too narrow for the dated PDFs' wide margins (under 6.5 in)."""
    return PAPER_INCHES.get(paper, (8.5, 11.0))[0] < 6.5


# The type's own spacing, in em: from the foot of one line to the top of the
# next (Typst's leading, the line box being about a capital's height, CAP),
# between paragraphs, and between the rows of two columns.
LEADING, CAP, PAR_SPACING, ROW_GAP = 0.48, 0.70, 0.42, 0.34


def spacing(lay):
    """(leading, paragraph spacing, row gap) in em, letter spacing in em and
    word spacing as a fraction, from a Layout, each kept within reason."""
    def within(value, low, high, default):
        try:
            return min(high, max(low, float(value)))
        except (TypeError, ValueError):
            return default
    line = within(lay.line_spacing, 0.6, 3.0, 1.0)
    verse = within(lay.verse_spacing, 0.0, 4.0, 1.0)
    # Line spacing scales the distance from one baseline to the next.
    leading = max(0.0, (CAP + LEADING) * line - CAP)
    return (leading, PAR_SPACING * verse, ROW_GAP * verse,
            within(lay.letter_spacing, -0.1, 0.3, 0.0), within(lay.word_spacing, 0.5, 3.0, 1.0))


def _em(value):
    return ("%.3f" % value).rstrip("0").rstrip(".") + "em" if value else "0em"


def colour(value, fallback):
    """A #rrggbb colour, or the fallback when the value is not one."""
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def long_date(d):
    return "%s, %d %s %d" % (WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1], d.year)


def short_date(d):
    return "%d %s %d" % (d.day, MONTHS[d.month - 1][:3], d.year)


# --------------------------------------------------------------------------
# Typst source generation


def q(s):
    """A Typst string literal. Text only ever goes in as strings, so the
    asterisks, underscores and hashes in the office are never read as markup."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Each distinct combination of run styles becomes one short helper, declared
# in the preamble: `#rbi("Incipit")` rather than a long text(...) call per run.
_SIZE = {"small": ".72em", "note": ".86em", "heading": "1.08em", "initial": "1.32em"}


def style_key(run):
    heading = run.size == "heading"
    key = ""
    if run.red or heading or run.size == "initial" or run.cross:
        key += "r"
    if run.bold or heading or run.size == "initial":
        key += "b"
    if run.italic or heading:
        key += "i"
    key += {"small": "s", "note": "n", "heading": "h", "initial": "c"}.get(run.size, "")
    if run.cross:
        key += "x"
    return key


def style_def(key):
    args = []
    if "r" in key:
        args.append("fill: rub")
    if "b" in key:
        args.append('weight: "bold"')
    if "i" in key:
        args.append('style: "italic"')
    sized = False
    for code, size in (("s", "small"), ("n", "note"), ("h", "heading"), ("c", "initial")):
        if code in key:
            args.append("size: " + _SIZE[size])
            sized = True
    # A cross is set a little larger -- unless the run has a size of its own
    # already (a line of the Passion that opens with ✠ takes it as its initial).
    if "x" in key and not sized:
        args.append("size: 1.15em")
    return "#let s%s = text.with(%s)" % (key, ", ".join(args))


# Cambria's `ccmp` feature redraws precomposed letters such as ó as o plus a
# combining accent. Typst then records "ó" as the text of the plain o glyph,
# and has to wrap every ordinary o and a in an ActualText span to keep the
# PDF's text correct -- 17,694 spans on one day's 34 pages, nearly half the
# file. The engine's text is already precomposed (NFC), so for scripts that
# need no mark stacking the feature buys nothing: switching it off leaves the
# rendering pixel-identical and the extracted text identical, at half the size.
_NO_CCMP = {"la", "en", "de", "fr", "it", "es", "pt", "pl", "hu", "cs", "nl", "da"}


def _features(lang, reset=False):
    if lang in _NO_CCMP:
        return ", features: (ccmp: 0)"
    return ", features: (ccmp: 1)" if reset else ""


# After English that was translated by AI (dotranslate.py): a small grey "AI",
# kept on the line of the word before it.
AI_MARK = ("#sym.wj#box[#h(0.15em)#super(text(fill: luma(110), size: 0.85em, "
           "tracking: 0.03em)[AI])]")
AI_NOTE = ("English marked #super[AI] was translated for this edition by AI, "
           "where the Divinum Officium English has none.")


def title_block(big, line1, line2, line3, ai_note=False, volume=None):
    """The centred title page shared by the dated PDFs and the breviary.

    line2 may be a list (a breviary's parts), set one to a line. ai_note: say
    what the "AI" mark means (the book has some). volume: (number, Latin name,
    English name, Latin span, English span) of one volume of a breviary.
    """
    if isinstance(line2, (list, tuple)):
        second = "  text(size: 1em, [%s])" % " #linebreak() ".join("#" + q(x) for x in line2)
    else:
        second = "  text(size: 1em, %s)" % q(line2)
    part = []
    if volume:
        _number, la, en, span_la, span_en = volume
        part = [
            "  v(0.9em)",
            "  text(size: 1.55em, fill: rub, %s)" % q(la),
            "  linebreak()",
            '  text(size: 1em, style: "italic", %s)' % q(en),
            "  v(0.5em)",
            "  text(size: 0.95em, %s)" % q(span_la),
            "  linebreak()",
            '  text(size: 0.9em, style: "italic", %s)' % q(span_en),
            "  v(0.4em)",
        ]
    return [
        "#align(center + horizon, {",
        "  text(size: 2.4em, fill: rub, %s)" % q(big),
    ] + part + [
        "  v(0.6em)",
        "  text(size: 1.25em, %s)" % q(line1),
        "  v(2.2em)",
        second,
        "  v(0.4em)",
        '  text(size: 0.9em, style: "italic", %s)' % q(line3),
        "  v(4em)",
        "  text(size: 0.75em, fill: luma(110), %s)"
        % q("Texts from the Divinum Officium project, divinumofficium.com"),
    ] + ([
        "  v(0.5em)",
        "  text(size: 0.75em, fill: luma(110))[%s]" % AI_NOTE,
    ] if ai_note else []) + [
        "})",
    ]


def _upright_versicles(run):
    """Split ℣ and ℟ out of italic runs.

    The engine sets them in italics, but Cambria only has these two glyphs in
    its upright face; left italic they fall back to another font.
    """
    if not run.italic or not any(c in run.text for c in "℣℟"):
        return [run]
    out = []
    for piece in re.split(r"([℣℟])", run.text):
        if piece:
            out.append(dataclasses.replace(run, text=piece, italic=piece not in "℣℟"))
    return out


def _line_kind(line):
    if line.is_blank:
        return "b"
    if line.is_heading:
        return "h"
    if line.is_rubric:
        return "r"
    text = line.text.lstrip()
    # "Orémus." / "Let us pray." stand beside each other or a gap: where the
    # English has no "Let us pray", its prayer belongs beside the Latin's, a
    # line lower -- not beside "Orémus" (whereupon a page could end between
    # the two prayers).
    if _OREMUS.fullmatch(text.strip()):
        return "o"
    if text.startswith(("℣", "V.")):
        return "v"
    if text.startswith(("℟", "R.")):
        return "R"
    return "t"


_VERSE = re.compile(r"^\s*(\d+):(\d+)")
_OREMUS = re.compile(r"(Or[eé]mus|Let us pray)\.?", re.I)


def _line_keys(lines):
    """For each line its kind, and a psalm verse's psalm, verse and which of the
    verse's lines it is ("109:1a", "109:1b" -> 109, 1, 0 and 109, 1, 1)."""
    keys, seen = [], {}
    for line in lines:
        kind = _line_kind(line)
        m = _VERSE.match(line.text) if kind == "t" else None
        if m:
            verse = (kind, int(m.group(1)), int(m.group(2)))
            seen[verse] = seen.get(verse, -1) + 1
            keys.append(verse + (seen[verse],))
        else:
            keys.append((kind,))
    return keys


def align_columns(left, right):
    """The rows of two columns set side by side: [(left line, right line)],
    either None where the other language has a line this one has not.

    The same lines are paired as they stand. Otherwise the two are lined up
    by the kind of each line (heading, rubric, versicle, text, blank), so a
    line only one language has -- the Latin's "Homilia sancti N." on a line of
    its own, where the English runs it into the lesson -- leaves a gap beside
    it instead of pushing the rest of the other column out of step; and a
    psalm's verses by their numbers, so where one translation divides the
    verses otherwise (Coverdale's 50:3 is the Latin's 50:3a and 50:3b) each
    verse stays beside its own.
    """
    lk, rk = _line_keys(left), _line_keys(right)
    if len(left) == len(right) and all(a == b or ((len(a) == 1 or len(b) == 1) and "o" not in (a[0], b[0]))
                                       for a, b in zip(lk, rk)):
        return list(zip(left, right))
    sm = difflib.SequenceMatcher(None, lk, rk, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            rows += zip(left[i1:i2], right[j1:j2])
        elif tag == "delete":
            rows += [(x, None) for x in left[i1:i2]]
        elif tag == "insert":
            rows += [(None, x) for x in right[j1:j2]]
        else:
            rows += itertools.zip_longest(left[i1:i2], right[j1:j2])
    return rows


# The running head's content (Typst, inside `header: context {...}`): the day
# (or office) in force -- one that begins on this page, else the last one begun
# before it -- and its hour likewise; none on a page with a <nohead> marker (a
# part's title page).
RUNNING_HEAD_FIND = [
    "    let pg = here().page()",
    "    let first-here(lbl) = {",
    "      let n = query(selector(lbl).after(here()))",
    "      if n.len() > 0 and n.first().location().page() == pg { n.first() } else { none }",
    "    }",
    "    let last-before(lbl) = {",
    "      let b = query(selector(lbl).before(here()))",
    "      if b.len() > 0 { b.last() } else { none }",
    "    }",
    "    let fresh = first-here(<day>)",
    "    let d = if fresh != none { fresh } else { last-before(<day>) }",
    "    let h = if fresh != none { first-here(<hour>) } else { last-before(<hour>) }",
    "    let quiet = query(<nohead>).any(m => m.location().page() == pg)",
]


class Writer:
    # True when the joined PDF should get a bookmark tree built from the
    # contents entries rather than the volumes' own (see the breviary).
    rebuild_outline = False

    def __init__(self, layout, meta):
        self.layout = layout
        self.meta = meta
        self.out = []
        self.styles = set()

    def emit(self, s=""):
        self.out.append(s)

    def runs(self, line):
        parts = []
        for run in line.runs:
            for piece in _upright_versicles(run):
                key = style_key(piece)
                if key:
                    self.styles.add(key)
                    parts.append("#s%s(%s)" % (key, q(piece.text)))
                else:
                    parts.append("#" + q(piece.text))
        if getattr(line, "ai", False) and self.layout.mark_ai:
            parts.append(AI_MARK)
            self.meta.ai_lines = getattr(self.meta, "ai_lines", 0) + 1  # for the title page's note
        return "".join(parts)

    # -- structure ---------------------------------------------------------

    def day(self, day, index):
        lay = self.layout
        if index and lay.new_page_per_day:
            self.emit("#pagebreak(weak: true)")
        label = "%s · %s" % (short_date(day.date), day.title or "")
        self.emit("#metadata((d: %s, t: %s))<day>" % (q(short_date(day.date)), q(day.title)))
        self.emit("#heading(level: 1)[#%s]" % q(label))
        self.emit(
            "#dayhead(%s, %s, %s)"
            % (q(long_date(day.date)), q(day.title), q(day.subtitle))
        )
        for i, hour in enumerate(day.hours):
            self.hour(hour, i)

    def hour(self, hour, index):
        if index and self.layout.new_page_per_hour:
            self.emit("#pagebreak(weak: true)")
        name = dooffice.HOUR_LABELS.get(hour.key, hour.key)
        self.emit("#metadata(%s)<hour>" % q(name))
        self.emit("#heading(level: 2)[#%s]" % q(hour.heading))
        for section in hour.sections:
            if len(section.columns) >= 2:
                self.pairs(section.columns[0], section.columns[1])
            else:
                self.single(section.columns[0])
            self.emit("#v(0.55em, weak: true)")

    def single(self, lines):
        # Lines set as paragraphs of their own (one language): what runs()
        # puts in front of a line must be inline (see BookWriter.runs).
        self.in_paragraph = True
        for line in lines:
            if line.is_blank:
                # A custom part's blank line is a line's space; the office's, a little gap.
                self.emit("#v(%s, weak: true)" % ("0.9em" if getattr(line, "prose", False) else "0.35em"))
            elif line.is_heading or line.is_rubric:
                # Kept with the next line: never a psalm title alone at a page foot.
                self.emit("#block(sticky: true)[#verse[%s]]" % self.runs(line))
            elif getattr(line, "prose", False):  # a paragraph of a custom part: no hanging indent
                self.emit("#par[%s]" % self.runs(line))
            else:
                self.emit("#verse[%s]" % self.runs(line))
        self.in_paragraph = False

    def pairs(self, left, right):
        rows = align_columns(left, right)
        # A heading row is glued to the row after it so a section title is
        # never left alone at the foot of a page.
        chunk = []

        def flush(sticky=False):
            if not chunk:
                return
            cells = ", ".join("[%s], R[%s]" % (a, b) for a, b in chunk)
            if sticky:
                self.emit("#block(sticky: true, width: 100%%)[%s]" % self.bi(cells, len(chunk)))
            else:
                self.emit(self.bi(cells, len(chunk)))
            chunk.clear()

        for a, b in rows:
            a_txt = self.runs(a) if a and not a.is_blank else ""
            b_txt = self.runs(b) if b and not b.is_blank else ""
            heading = any(x is not None and (x.is_heading or x.is_rubric) for x in (a, b))
            if not a_txt and not b_txt:
                flush()
                self.emit("#v(0.35em, weak: true)")
                continue
            if heading:
                flush()
                chunk.append((a_txt, b_txt))
                flush(sticky=True)
                continue
            chunk.append((a_txt, b_txt))
        flush()

    # -- the document ------------------------------------------------------

    def preamble(self):
        lay, meta = self.layout, self.meta
        kind, value = PAPERS.get(lay.paper, ("paper", "us-letter"))
        if kind == "paper":
            page_size = 'paper: "%s"' % value
        else:
            page_size = "width: %s, height: %s" % value
        small = small_paper(lay.paper)
        lang1 = LANG_CODES.get(meta.lang1) or LANG_CODES.get(dooffice.family(meta.lang1), "la")
        lang2 = LANG_CODES.get(meta.lang2) or LANG_CODES.get(dooffice.family(meta.lang2), "en")
        rub = colour(lay.rubric_colour, Layout.rubric_colour) if lay.red_in_print else "#000000"
        ink = colour(lay.text_colour, Layout.text_colour)
        align = lay.align if lay.align in ALIGNS else "justify"
        title = "Divinum Officium — %s" % meta.version
        if getattr(meta, "volume", None):
            title += " — %s" % meta.volume[1]
        leading, par_spacing, row_gap, letters, words = spacing(lay)
        extra = (", tracking: %s" % _em(letters) if letters else "") + \
                (", spacing: %d%%" % round(words * 100) if round(words * 100) != 100 else "")
        # Typst gives a page's inside margin by its place in the volume's file:
        # a volume that begins on an even page of the book is bound on the right.
        binding = "right" if getattr(self, "start_page", 1) % 2 == 0 else "left"
        return "\n".join(
            [
                "// Generated by Divinum Officium (offline) from the project's own engine.",
                '#set document(title: %s, author: "Divinum Officium")' % q(title),
                '#let rub = rgb("%s")' % rub,
                '#set text(font: (%s, "Cambria", "Times New Roman", "Segoe UI Symbol"), '
                'size: %.1fpt, lang: "%s", hyphenate: true, fill: rgb("%s")%s%s)'
                % (q(lay.font), lay.font_size, lang1, ink, _features(lang1), extra),
                "#set par(justify: %s, leading: %s, spacing: %s)"
                % ("true" if align == "justify" else "false", _em(leading), _em(par_spacing)),
                "#set page(%s, binding: %s," % (page_size, binding),
            ] + self.page_furniture(small) + [
                ")",
                # Ragged right is the default ("start"); ragged left sets the
                # text to the end of the line (in each column's own direction).
                "#set align(end)" if align == "right" else "// text aligned to the start",
                # Bilingual rows: left language, right language.
                "#let R(body) = { set text(lang: \"%s\"%s); body }" % (lang2, _features(lang2, reset=True)),
            ] + self.bi_rules(row_gap, par_spacing) + [
                "#let verse(body) = par(hanging-indent: 1.1em, body)",
                # A day: date line, title in red, commemorations beneath.
                "#let dayhead(date, title, sub) = {",
                "  v(0.2em)",
                "  align(center, block(width: 100%, sticky: true, {",
                '    text(size: 0.82em, tracking: 0.06em, upper(date))',
                "    linebreak()",
                "    text(fill: rub, size: 1.3em, weight: \"bold\", hyphenate: false, title)",
                '    if sub != "" { linebreak(); text(size: 0.86em, style: "italic", sub) }',
                "  }))",
                "  v(0.4em)",
                "}",
            ] + self.heading_rules()
        )

    def mirrored(self):
        """Two languages with the first on the outside of each page (Layout.mirror_columns)."""
        return bool(getattr(self.layout, "mirror_columns", False)) and self.meta.lang1 != self.meta.lang2

    # Rows of the two languages set so far (mirrored), and, once they have been
    # measured (measure_rows), each row's order: {its number: "R" or "T"}.
    bi_rows = 0
    row_orders = None

    def bi(self, cells, rows=1):
        """A call of bi() for these rows ("[..], R[..], ..."). When the columns
        are mirrored the rows are numbered through the volume (c: the call's
        first), and each has its order (o: a letter a row) once that is known:
        L the first language at the left, R at the right, T both, the row
        going over a page turn (bi_rules)."""
        if not self.mirrored():
            return "#bi(%s)" % cells
        first = self.bi_rows + 1
        self.bi_rows += rows
        orders = self.row_orders or {}
        o = "".join(orders.get(n, "L") for n in range(first, first + rows)).rstrip("L")
        return "#bi(c: %d, %s%s)" % (first, 'o: "%s", ' % o if o else "", cells)

    def bi_rules(self, row_gap, par_spacing):
        """bi(): rows of the two languages side by side, the first at the left.

        Layout.column_rule draws a rule between them in the rubrics' colour:
        it runs through a call's rows, and a little below the last, to meet
        the next call's across the space between them (a blank line, a
        heading, the next section).

        Layout.mirror_columns puts the first language on the outside of each
        page of a bound book. The two columns are equally wide, so which
        stands at the left changes no line and no page: the volume is set
        once with the first language at the left of every row, each row's
        page read back from its markers (measure_rows), and then set with
        each row's order given (bi(), above). A row that goes over a page
        turn is set in both orders at once, one upon the other, and each word
        shown in the one its page wants, hidden in the other (turncell):
        hidden text is not in the PDF at all. (A first version had each row
        ask for its own page while it was set: Typst then set the book five
        times over, 103 s for a volume that takes 12 s.)"""
        rule = bool(getattr(self.layout, "column_rule", False))
        if not rule and not self.mirrored():
            return ["#let bi(..cells) = grid(columns: (1fr, 1fr), column-gutter: 1.1em, "
                    "row-gutter: %s, ..cells.pos())" % _em(row_gap)]
        out = [
            "#let colgap = 1.1em",
            "#let rowgap = %s" % _em(row_gap),
            "#let birule = %s" % ("0.4pt + rub" if rule else "none"),
            # Below a call's last row: as far as the next can be (the space
            # between blocks, a blank line's, a section's).
            "#let bitail = %s" % _em(max(0.4, row_gap, par_spacing)),
        ]
        if not self.mirrored():
            return out + [
                "#let bi(c: 0, ..cells) = {",
                "  let rows = cells.pos().chunks(2)",
                "  grid(columns: (1fr, 0pt, 1fr), column-gutter: colgap / 2,",
                "    row-gutter: (rowgap,) * (rows.len() - 1) + (0pt,),",
                "    grid.vline(x: 1, stroke: birule),",
                "    ..rows.map(r => (r.at(0), [], r.at(1))).flatten(),",
                "    grid.cell(x: 1, place(top + left, line(angle: 90deg, length: bitail, stroke: birule))))",
                "}",
            ]
        return out + [
            # A first copy's bookmark (BookWriter.runs): once, not again in the
            # second setting of a row that goes over a page turn (alt, below).
            "#let bimk(t, l) = context {",
            "  if heading.supplement == auto {",
            "    box(width: 0pt, height: 0pt, hide([#heading(level: 3)[#t] #label(l)]))",
            "  }",
            "}",
            # A cell of such a row, in one of the two places it can stand:
            # only its words on right-hand pages are shown (want), or only
            # those on left-hand ones. The text is taken TURN_CHUNK words at a
            # time, each chunk followed by a marker (l), and is where it ends
            # -- a marker before a word that begins a line stands at the end
            # of the line above. A chunk that ends on the page where the one
            # before it ended is on that page, and shown or hidden whole. The
            # one that goes over the turn is taken word by word in the same
            # way (word, its markers lw). A word cut over the turn is shown in
            # two pieces, cut where the piece on the new page is as wide as
            # what stands before the word's end. (Word by word throughout, a
            # volume of 643 pages took 137 s; by chunks 31 s.)
            "#let bioff = %d" % (getattr(self, "start_page", 1) - 1),  # a page's number, less its place
            "#let turncell(l, want, body) = {",
            '  let lw = label(str(l) + "w")',
            "  let vis(n, s) = if calc.odd(n + bioff) == want { s } else { hide(s) }",
            "  let word(w) = {",
            "    context {",
            "      let me = here()",
            "      let after = query(selector(lw).after(me))",
            "      let before = query(selector(lw).before(me))",
            "      if after.len() == 0 or before.len() == 0 { w } else {",
            "        let e = after.first().location()",
            "        let pe = e.page()",
            "        let s = before.last().location()",
            "        if s.page() == pe { vis(pe, w) } else {",
            "          let tail = e.position().x - query(l).first().location().position().x",
            "          if tail < 0.5pt or tail > measure[#w].width - 0.3pt { vis(pe, w) } else {",
            "            let cl = w.clusters()",
            "            let best = 0",
            "            let err = 1000pt",
            "            for j in range(1, cl.len()) {",
            "              let d = calc.abs(measure[#cl.slice(j).join()].width - tail)",
            "              if d < err { err = d; best = j }",
            "            }",
            "            if best == 0 or err > 1pt { vis(pe, w) } else {",
            "              vis(s.page(), cl.slice(0, best).join()) + vis(pe, cl.slice(best).join())",
            "            }",
            "          }",
            "        }",
            "      }",
            "    }",
            "    [#metadata(none)#lw]",
            "  }",
            r'  show regex("\\S+( \\S+){0,%d}"): it => {' % (TURN_CHUNK - 1),
            "    let chunk = it.text",
            "    context {",
            "      let me = here()",
            "      let after = query(selector(l).after(me))",
            "      if after.len() == 0 { chunk } else {",
            "        let e = after.first().location()",
            "        let s = query(selector(l).before(me)).last().location()",
            "        if s.page() == e.page() { vis(e.page(), chunk) } else {",
            "          [#metadata(none)#lw]",
            '          chunk.split(" ").map(word).join(" ")',
            "        }",
            "      }",
            "    }",
            "    [#metadata(none)#l]",
            "  }",
            "  [#metadata(none)#l]",
            "  body",
            "}",
            # A row (n: its number in the volume) in its order (o). Its markers:
            # at its first line (bis) and at the end of each language (bie),
            # by which its pages are read (measure_rows) and checked
            # (ROWS_CHECK). The second setting of a row that goes over a page
            # turn has them without their labels: each row is found once.
            "#let birow(n, w, la, en, gap, o) = {",
            "  let cells = if o == \"T\" {",
            "    let id = str(n)",
            "    let alt(body) = block(width: w, { set heading(supplement: [alt]); body })",
            "    let la1 = [#metadata(n)<bis>] + la + [#metadata(n)<bie>]",
            "    let la2 = [#metadata(n)] + la + [#metadata(n)]",
            "    let en1 = en + [#metadata(n)<bie>]",
            "    let en2 = en + [#metadata(n)]",
            '    (alt(turncell(label("a" + id), true, en2)), turncell(label("b" + id), false, la1), [],',
            '     alt(turncell(label("c" + id), true, la2)), turncell(label("d" + id), false, en1))',
            "  } else {",
            "    let la = [#metadata(n)<bis>] + la + [#metadata(n)<bie>]",
            "    let en = en + [#metadata(n)<bie>]",
            "    if o == \"R\" { ([], en, [], [], la) } else { ([], la, [], [], en) }",
            "  }",
            "  block(above: gap, below: rowgap, grid(",
            "    columns: (0pt, 1fr, 0pt, 0pt, 1fr), column-gutter: (0pt, colgap / 2, colgap / 2, 0pt),",
            "    ..if birule != none { (grid.vline(x: 2, stroke: birule),) },",
            "    ..cells,",
            "    ..if birule != none { (grid.cell(x: 2, y: 1,",
            "      place(top + left, line(angle: 90deg, length: bitail, stroke: birule))),) }))",
            "}",
            '#let bi(c: 0, o: "", ..cells) = layout(size => {',
            "  let w = (size.width - colgap.to-absolute()) / 2",
            "  for (k, r) in cells.pos().chunks(2).enumerate() {",
            "    birow(c + k, w, r.at(0), r.at(1), if k == 0 { auto } else { rowgap },",
            '          o.at(k, default: "L"))',
            "  }",
            "})",
        ]

    # The running head's rule: a fixed distance under its words (a stack's, not
    # the space between blocks, which follows the spacing chosen: the rule
    # went through the words at a small "space between verses", and away from
    # them at a large one).
    HEAD_RULE = [
        "        0.32em,",
        "        line(length: 100%, stroke: 0.4pt + luma(170)),",
    ]

    def page_furniture(self, small):
        """The page's margins, running head and foot (#set page arguments)."""
        margin = ("(inside: 0.55in, outside: 0.45in, top: 0.62in, bottom: 0.55in)" if small
                  else "(x: 0.75in, top: 0.85in, bottom: 0.75in)")
        return ["  margin: %s," % margin, "  header: context {"] + RUNNING_HEAD_FIND + [
            "    if d != none and not quiet {",
            '      set text(size: 0.78em, fill: luma(90), hyphenate: false)',
            "      stack(dir: ttb,",
            "        grid(columns: (1fr, auto), column-gutter: 1em, align: (left, right),",
            "          text(style: \"italic\", d.value.d + \"  ·  \" + d.value.t),",
            '          if h != none { h.value } else { "" }),',
            ] + self.HEAD_RULE + [
            "      )",
            "    }",
            "  },",
            "  footer: context align(center, text(size: 0.8em, fill: luma(90), counter(page).display())),",
        ]

    def heading_rules(self):
        return [
            # The level-1 heading exists for the bookmarks and contents; the
            # visible header is dayhead(), so the heading itself is hidden.
            "#show heading.where(level: 1): it => place(hide(it))",
            "#show heading.where(level: 2): it => block(width: 100%, sticky: true, above: 1.1em, below: 0.7em,",
            "  align(center, text(fill: rub, size: 1.2em, style: \"italic\", weight: \"regular\", it.body)))",
        ]

    def volume_entries(self, vol, pdf_path, next_page):
        """Contents entries for one compiled volume: (level, left, title, page)."""
        _count, starts = _day_pages(pdf_path)
        return [(1, short_date(day.date), day.title, next_page + idx)
                for day, idx in zip(vol, starts)]

    def title_page(self):
        meta = self.meta
        span = (
            long_date(meta.first)
            if meta.first == meta.last
            else "%s — %s" % (long_date(meta.first), long_date(meta.last))
        )
        langs = languages_named(meta)
        hours = (
            "All hours"
            if len(meta.hours) == len(dooffice.HOURS)
            else ", ".join(dooffice.HOUR_LABELS.get(h, h) for h in meta.hours)
        )
        return title_block("Divinum Officium", meta.version, span, "%s · %s" % (hours, langs),
                           ai_note=bool(getattr(meta, "ai_lines", 0)))

    def document(self, days, start_page=1):
        """Typst source for one volume: these days, page-numbered from start_page."""
        self.start_page = start_page
        self.out = []
        self.styles = set()
        self.bi_rows = 0
        for i, day in enumerate(days):
            self.day(day, i)
        head = [self.preamble()]
        head += [style_def(k) for k in sorted(self.styles)]
        head.append("#counter(page).update(%d)" % start_page)
        return "\n".join(head + self.out) + "\n"

    def front(self, entries):
        """Title page and contents, typeset last once page numbers are known.

        entries: [(level, left, title, body_page)]. Level 1 rows are days (or,
        in a breviary, parts); level 2 rows are indented beneath them. `left`
        is a short label in its own column (the date), or "". Each row leaves
        a <tocpos> marker so its position can be read back with `typst query`
        and made clickable after the volumes are joined.
        """
        lay = self.layout
        dated = any(e[1] for e in entries)
        out = [
            self.preamble(),
            "#set page(header: none, footer: none)",
            "#set align(start)",  # the contents are never ragged left or justified
            "#set par(justify: false)",
            "#let tocrow(i, lvl, left, title, page) = {",
            "  context [#metadata((i: i, k: 0, p: here().page(), x: here().position().x / 1pt,"
            " y: here().position().y / 1pt))<tocpos>]",
            # Fixed label column, so titles line up whether the day has one digit or two.
            "  grid(columns: (%s, 1fr, auto), column-gutter: 0.9em," % ("5.7em" if dated else "0em"),
            "    text(fill: luma(90), left),",
            # One line, cut at the column's edge: the inner box is wider than any
            # title, so a long one is not broken (a second line showed under
            # the cut at a tight line spacing, over the next row).
            "    box(width: 100%, height: 1.25em, clip: true, pad(left: (lvl - 1) * 1.4em, box(width: 60cm,",
            "      if lvl == 1 and left == \"\" { text(fill: rub, weight: \"bold\", hyphenate: false, title) }",
            "      else { text(hyphenate: false, title) }))),",
            "    [#str(page)#context [#metadata((i: i, k: 1, p: here().page(),"
            " x: here().position().x / 1pt, y: here().position().y / 1pt))<tocpos>]])",
            "  v(0.2em, weak: true)",
            "}",
        ]
        if lay.title_page:
            out += self.title_page()
        if lay.contents and len(entries) > 1:
            if lay.title_page:
                out.append("#pagebreak()")
            out.append('#align(center, text(fill: rub, size: 1.5em, "Contents"))')
            out.append("#v(0.8em)")
            for i, (level, left, title, page) in enumerate(entries):
                if level == 1 and not left and i:
                    out.append("#v(0.5em)")
                out.append("#tocrow(%d, %d, %s, %s, %d)" % (i, level, q(left), q(title), page))
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# compiling


Cancelled = dooffice.Cancelled

# About three weeks of the whole office in two languages: roughly 1.3 GB at
# peak while Typst lays it out. A year in one piece needed over 8 GB.
VOLUME_ROWS = 24000

logging.getLogger("pypdf").setLevel(logging.ERROR)  # it narrates every link it copies


def _run(cmd, cancelled=None):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags)
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancelled and cancelled():
                proc.kill()
                proc.communicate()
                raise Cancelled()
    if proc.returncode != 0:
        msg = (err or out).decode("utf-8", "replace").strip()
        raise RuntimeError("typesetting failed:\n" + msg[-2000:])
    return out


def compile_pdf(typ_path, pdf_path, typst_exe, cancelled=None, tagged=False):
    cmd = [typst_exe, "compile", "--root", os.path.dirname(typ_path)]
    if not tagged:
        # The accessibility structure tree is about 60% of the file.
        cmd.append("--no-pdf-tags")
    _run(cmd + [typ_path, pdf_path], cancelled)


def query(typ_path, label, typst_exe, cancelled=None):
    out = _run([typst_exe, "query", "--root", os.path.dirname(typ_path), typ_path,
                label, "--field", "value", "--format", "json"], cancelled)
    return json.loads(out.decode("utf-8"))


# The first language on the outside of each page (Writer.bi_rules): where each
# row of a volume begins and ends, read back from a setting of it with
# `typst query` -- ([row, page] of each row's first line, [row, page] of each
# language's end), the pages counted from the volume's first.
ROW_PAGES = ("\n#context [#metadata((query(<bis>).map(m => (m.value, m.location().page())), "
             "query(<bie>).map(m => (m.value, m.location().page())))) <bipages>]\n")
# ...and the words with which a volume set with its rows' orders stops, should
# a row begin on another page than the one it was measured on.
ROWS_MOVED = "a row of the two languages is not on the page it was measured on"
MIRROR_ROUNDS = 3
# A row that goes over a page turn is looked through this many words at a
# time for the turn (Writer.bi_rules, turncell).
TURN_CHUNK = 16


def measure_rows(source, typ_path, typst_exe, cancelled=None):
    """(where each row begins, where each language of it ends) in a volume
    set from this source: {row: page} and {row: its last page}."""
    with open(typ_path, "w", encoding="utf-8") as fh:
        fh.write(source + ROW_PAGES)
    found = query(typ_path, "<bipages>", typst_exe, cancelled)
    starts, ends = found[0] if found else ([], [])
    first, last = {}, {}
    for n, page in starts:
        first.setdefault(n, page)
    for n, page in ends:
        last[n] = max(last.get(n, 0), page)
    return first, last


def row_orders(first, last, start_page):
    """Each row's order from its pages: {row: "R"} for a row on a right-hand
    page (an odd page number: the first language at the right, outside),
    {row: "T"} for one that goes over a page turn; the others are left out."""
    orders = {}
    for n, page in first.items():
        if last.get(n, page) != page:
            orders[n] = "T"
        elif (start_page + page - 1) % 2:
            orders[n] = "R"
    return orders


def rows_check(first):
    """Source that stops the typesetting (ROWS_MOVED) unless each row begins
    on the page it was measured on. The first setting has nothing to compare
    (no row is found yet); Typst sets the volume again until what was found
    is what is set, so the pages printed are the pages checked."""
    if not first:
        return ""
    return ("\n#let bipages = (%s,)\n#context {\n"
            "  let got = query(<bis>).map(m => m.location().page())\n"
            "  if got.len() > 0 and got != bipages { panic(\"%s\") }\n}\n"
            % (",".join(str(first[n]) for n in sorted(first)), ROWS_MOVED))


def mirrored_source(make, typ_path, typst_exe, start_page, cancelled=None, orders=None):
    """A volume's source with the first language on the outside of each page.

    make(orders) -> (writer, source) writes the volume with these orders of
    its rows (None: the first language at the left of each). It is set once
    to find each row's pages (measure_rows, at typ_path), and written again
    with the orders those give. (writer, source, orders, check): the check
    (rows_check) goes after the source; a volume with no rows of two
    languages has its source as written, and no check."""
    writer, source = make(orders)
    if not writer.mirrored() or not writer.bi_rows:
        return writer, source, None, ""
    first, last = measure_rows(source, typ_path, typst_exe, cancelled)
    new = row_orders(first, last, start_page)
    if new != (orders or {}):
        writer, source = make(new)
    return writer, source, new, rows_check(first)


def _set_volume(make, typ, pdf, typst_exe, start_page, cancelled, tagged, known=None):
    """Write a volume's source (make, as for mirrored_source) and typeset it.
    Mirrored, its rows are measured first -- unless their orders are known
    from a setting before (known: what this returned then) -- and measured
    again, from the volume as it was last written, if the check finds a row
    elsewhere. (writer, (orders, check))."""
    orders = None
    for attempt in range(MIRROR_ROUNDS):
        if known is not None:
            (orders, check), known = known, None
            w, source = make(orders)
        else:
            w, source, orders, check = mirrored_source(make, typ[:-4] + "-rows.typ", typst_exe,
                                                       start_page, cancelled, orders)
        with open(typ, "w", encoding="utf-8") as fh:
            fh.write(source + check)
        try:
            compile_pdf(typ, pdf, typst_exe, cancelled, tagged=tagged)
            return w, (orders, check)
        except RuntimeError as exc:
            if ROWS_MOVED not in str(exc):
                raise
    raise RuntimeError("The first language could not be kept on the outside of each page: the rows "
                       "of %s did not come to rest in %d settings. Make the book without that "
                       "option, or with other spacing." % (os.path.basename(typ), MIRROR_ROUNDS))


def day_rows(day):
    return 30 + sum(max((len(c) for c in s.columns), default=0)
                    for h in day.hours for s in h.sections)


def plan_volumes(days, budget=VOLUME_ROWS, rows_of=None):
    """Split units (days, or a breviary's offices) into consecutive volumes
    that each fit comfortably in memory."""
    rows_of = rows_of or day_rows
    volumes, current, rows = [], [], 0
    for day in days:
        r = rows_of(day)
        if current and rows + r > budget:
            volumes.append(current)
            current, rows = [], 0
        current.append(day)
        rows += r
    if current:
        volumes.append(current)
    return volumes


def _day_pages(pdf_path):
    """(page count, [page index of each day]) from a volume's level-1 bookmarks."""
    reader = PdfReader(pdf_path)
    starts = [reader.get_destination_page_number(item)
              for item in reader.outline if not isinstance(item, list)]
    return len(reader.pages), starts


def export(
    engine,
    requests,
    layout,
    pdf_path,
    typst_exe,
    progress=None,
    cancelled=None,
    workdir=None,
    keep_source=False,
):
    """Render each request with the engine, then typeset them into one PDF.

    progress(stage, done, total) is called with stage "text" while the engine
    runs, "typeset" per volume, and "assemble" while the parts are joined.
    Returns (days, failures); failures lists (request, exception) for any day
    the engine could not produce -- those days carry a note in the PDF.
    """
    requests = list(requests)
    if not requests:
        raise ValueError("nothing to export")

    def text_progress(done, total):
        if progress:
            progress("text", done, total)

    parts = engine.days(requests, progress=text_progress, cancelled=cancelled)
    days, failures = [], []
    for req, part in zip(requests, parts):
        if isinstance(part, Exception):
            # Keep the day in the book with a visible note rather than
            # dropping it silently or failing the whole export.
            failures.append((req, part))
            part = dooffice.Day(date=req.date, version=req.version,
                                title="(text unavailable)",
                                hours=[_failure_hour(req)])
        # A request per hour comes back as several Days for one date; merge.
        if days and days[-1].date == part.date:
            days[-1].hours.extend(part.hours)
            if not days[-1].subtitle and part.subtitle:
                days[-1].subtitle = part.subtitle
        else:
            days.append(part)
    order = {h: i for i, h in enumerate(dooffice.HOURS)}
    for d in days:
        d.hours.sort(key=lambda h: order.get(h.key, 99))

    first = requests[0]
    # English for the texts the engine's English data lacks, as in the breviary.
    import dotranslate

    dotranslate.translate_days(days, first.lang1, first.lang2)
    hours = sorted({r.hour for r in requests}, key=lambda h: order.get(h, 99))
    if "Omnes" in hours:
        hours = list(dooffice.HOURS)
    meta = Meta(version=first.version, lang1=first.lang1, lang2=first.lang2,
                hours=hours, first=days[0].date, last=days[-1].date)

    own_dir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="do-pdf-")
    os.makedirs(workdir, exist_ok=True)
    try:
        _typeset(days, meta, layout, pdf_path, typst_exe, workdir, progress, cancelled)
    finally:
        if own_dir and not keep_source:
            shutil.rmtree(workdir, ignore_errors=True)
    return days, failures


def _typeset(days, meta, layout, pdf_path, typst_exe, workdir, progress, cancelled,
             writer=None, rows_of=None):
    """Typeset units in volumes, then the front matter, then join them.

    `writer` is the Writer class (the dated office, or the breviary's), which
    supplies each volume's source, its contents entries and the title page.
    """
    writer = writer or Writer
    tagged = layout.accessible
    volumes = plan_volumes(days, rows_of=rows_of)
    vol_pdfs, entries, next_page, again = [], [], 1, []
    for n, vol in enumerate(volumes):
        if cancelled and cancelled():
            raise Cancelled()
        if progress:
            progress("typeset", n, len(volumes))
        typ = os.path.join(workdir, "volume%02d.typ" % (n + 1))
        pdf = os.path.join(workdir, "volume%02d.pdf" % (n + 1))

        def make(orders, vol=vol, start=next_page):
            w = writer(layout, meta)
            w.row_orders = orders
            return w, w.document(vol, start_page=start)

        w, rows = _set_volume(make, typ, pdf, typst_exe, next_page, cancelled, tagged)
        count = len(PdfReader(pdf).pages)
        entries += writer(layout, meta).volume_entries(vol, pdf, next_page)
        vol_pdfs.append(pdf)
        if getattr(w, "unresolved", False):
            again.append((make, typ, pdf, next_page, count, rows))
        next_page += count
    # A page named before it was typeset (the breviary's "Cetera ut in" from
    # the Proper of the Saints to the Common after it): those volumes set
    # again, every page now known. The number's box is of fixed width, so the
    # pages come out the same (and each row's order with them); were they
    # not, the book would be wrong.
    for make, typ, pdf, start, count, rows in again:
        if cancelled and cancelled():
            raise Cancelled()
        _set_volume(make, typ, pdf, typst_exe, start, cancelled, tagged, known=rows)
        if len(PdfReader(pdf).pages) != count:
            raise RuntimeError("%s came out %d pages, not %d, when set again with its references"
                               % (os.path.basename(pdf), len(PdfReader(pdf).pages), count))
    if progress:
        progress("typeset", len(volumes), len(volumes))

    front_pdf, marks = None, []
    want_contents = layout.contents and len(entries) > 1
    if layout.title_page or want_contents:
        typ = os.path.join(workdir, "front.typ")
        front_pdf = os.path.join(workdir, "front.pdf")
        with open(typ, "w", encoding="utf-8") as fh:
            fh.write(writer(layout, meta).front(entries))
        compile_pdf(typ, front_pdf, typst_exe, cancelled, tagged=tagged)
        if want_contents:
            marks = query(typ, "<tocpos>", typst_exe, cancelled)

    if progress:
        progress("assemble", 0, 1)
    _assemble(front_pdf, vol_pdfs, entries, marks, layout, meta, pdf_path,
              rebuild_outline=writer.rebuild_outline,
              even_front=getattr(writer, "even_front", False))
    if progress:
        progress("assemble", 1, 1)


def _goto(writer, rect, page_index):
    """A borderless link to a page of this document.

    Built by hand: pypdf's Link(target_page_index=...) came out as a bare page
    number, which the PDF standard reserves for links into *other* files
    (MuPDF-based readers then treat it as a named destination). A GoTo action
    pointing at the page object itself is unambiguous, and pypdf passes /A
    through untouched, where it rewrites /Dest.
    """
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/Border"): ArrayObject([NumberObject(0)] * 3),
        NameObject("/A"): DictionaryObject({
            NameObject("/S"): NameObject("/GoTo"),
            NameObject("/D"): ArrayObject([
                writer.pages[page_index].indirect_reference, NameObject("/Fit")]),
        }),
    })


def blank_pages(layout):
    """(front, back): the blank pages a book or volume gets; the front ones in pairs."""
    front = max(0, min(20, int(getattr(layout, "blank_front", 0) or 0)))
    back = max(0, min(20, int(getattr(layout, "blank_back", 0) or 0)))
    return front - front % 2, back


def _assemble(front_pdf, vol_pdfs, entries, marks, layout, meta, pdf_path,
              rebuild_outline=False, even_front=False):
    """even_front: a bound book's page 1 is a right-hand page, so a title page
    and contents of an odd number of pages are followed by a blank one."""
    writer = PdfWriter()
    blank_front, blank_back = blank_pages(layout)
    if blank_front or blank_back:
        box = PdfReader(front_pdf or vol_pdfs[0]).pages[0].mediabox
        size = (float(box.width), float(box.height))
    for _ in range(blank_front):  # before the title page, unnumbered like it
        writer.add_blank_page(*size)
    n_front = blank_front
    if front_pdf:
        writer.append(front_pdf, import_outline=False)
        if even_front and len(writer.pages) % 2:
            box = writer.pages[-1].mediabox
            writer.add_blank_page(float(box.width), float(box.height))
        n_front = len(writer.pages)
        if layout.title_page:
            writer.add_outline_item("Title", blank_front)
        if marks:
            first_toc = blank_front + min(m["p"] for m in marks) - 1
            writer.add_outline_item("Contents", first_toc)
    for pdf in vol_pdfs:
        writer.append(pdf, import_outline=not rebuild_outline)
    if rebuild_outline:
        parent = None
        for level, left, title, page in entries:
            target = n_front + page - 1
            label = "%s · %s" % (left, title) if left else title
            if level == 1:
                parent = writer.add_outline_item(label, target)
            else:
                writer.add_outline_item(label, target, parent=parent)

    # Clickable contents: each row's position came back from `typst query`.
    rows = {}
    for m in marks:
        rows.setdefault(m["i"], {})[m["k"]] = m
    row_h = layout.font_size * 1.4
    for i, (_level, _left, _title, page) in enumerate(entries):
        m = rows.get(i)
        if not m or 0 not in m:
            continue
        start = m[0]
        toc_index = blank_front + start["p"] - 1
        box = writer.pages[toc_index].mediabox
        height, width = float(box.height), float(box.width)
        x1 = m[1]["x"] + layout.font_size * 1.2 if 1 in m else width - start["x"]
        rect = (start["x"], height - start["y"] - row_h, x1, height - start["y"] + 2)
        writer.add_annotation(page_number=toc_index,
                              annotation=_goto(writer, rect, n_front + page - 1))

    for _ in range(blank_back):
        writer.add_blank_page(*size)

    # Viewer page numbers match the printed ones: roman front matter (and the
    # blank pages before it), then 1, 2, 3 ... (on to the blank pages after).
    total = len(writer.pages)
    if n_front:
        writer.set_page_label(0, n_front - 1, style="/r")
    if total > n_front:
        writer.set_page_label(n_front, total - 1, style="/D", start=1)
    writer.add_metadata({"/Title": "Divinum Officium — %s" % meta.version,
                         "/Creator": "Divinum Officium (offline)"})
    writer.page_mode = "/UseOutlines"  # open with the bookmarks showing
    tmp = pdf_path + ".part"
    with open(tmp, "wb") as fh:
        writer.write(fh)
    os.replace(tmp, pdf_path)


def _failure_hour(req):
    note = dooffice.Line(runs=[dooffice.Run(
        text="The office engine could not produce %s for this day (%s). "
             "This is recorded here rather than left out." % (
                 "the whole office" if req.hour == "Omnes"
                 else dooffice.HOUR_LABELS.get(req.hour, req.hour),
                 req.version),
        red=True, italic=True)])
    return dooffice.Hour(key=req.hour if req.hour != "Omnes" else "Matutinum",
                         heading=dooffice.HOUR_LABELS.get(req.hour, "Officium"),
                         sections=[dooffice.Section(columns=[[note]])])


def requests_for(first, last, hours, **options):
    """Engine requests for a date range and a set of hours.

    Asking for every hour uses the engine's own whole-day mode (one call per
    day instead of eight).
    """
    whole_day = set(hours) >= set(dooffice.HOURS)
    out = []
    for day in dooffice.date_range(first, last):
        if whole_day:
            out.append(dooffice.Request(day, "Omnes", **options))
        else:
            for h in dooffice.HOURS:
                if h in hours:
                    out.append(dooffice.Request(day, h, **options))
    return out


# --------------------------------------------------------------------------


def add_type_arguments(ap):
    """--font, --align, colours and spacing (shared with the breviary)."""
    ap.add_argument("--font", default=Layout.font, help="a font family installed on this PC")
    ap.add_argument("--align", default="justify", choices=ALIGNS)
    ap.add_argument("--rubric-colour", default=Layout.rubric_colour, metavar="#RRGGBB")
    ap.add_argument("--text-colour", default=Layout.text_colour, metavar="#RRGGBB")
    ap.add_argument("--no-ai-mark", action="store_true",
                    help='no small "AI" after English translated by AI for this project')
    ap.add_argument("--line-spacing", type=float, default=100, metavar="PERCENT",
                    help="from line to line, against the standard (100)")
    ap.add_argument("--verse-spacing", type=float, default=100, metavar="PERCENT",
                    help="the space between verses, against the standard (100)")
    ap.add_argument("--letter-spacing", type=float, default=0, metavar="PERCENT",
                    help="added between letters, in percent of the type size (0)")
    ap.add_argument("--word-spacing", type=float, default=100, metavar="PERCENT",
                    help="the width of a space, against the font's own (100)")
    ap.add_argument("--margins", metavar="TOP,BOTTOM,INSIDE,OUTSIDE",
                    help="the breviary's margins in inches, top being to the running head")


def type_options(args):
    margins = None
    if getattr(args, "margins", None):
        try:
            margins = tuple(float(v) for v in args.margins.split(","))
        except ValueError:
            margins = ()
        if len(margins) != 4:
            raise SystemExit("--margins takes four numbers: top,bottom,inside,outside")
    return {"font": args.font, "align": args.align,
            "rubric_colour": colour(args.rubric_colour, Layout.rubric_colour),
            "text_colour": colour(args.text_colour, Layout.text_colour),
            "mark_ai": not args.no_ai_mark,
            "line_spacing": args.line_spacing / 100, "verse_spacing": args.verse_spacing / 100,
            "letter_spacing": args.letter_spacing / 100, "word_spacing": args.word_spacing / 100,
            "margins": margins}


def _default_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.path.join(here, "dist", "DivinumOfficium"), here):
        web = os.path.join(base, "web")
        perl = os.path.join(base, "perl", "bin", "perl.exe")
        if os.path.isdir(web) and os.path.isfile(perl):
            return web, perl
    return os.path.join(here, "repo", "web"), shutil.which("perl")


def _default_libs(perl):
    """perl-lib/ (CGI.pm and URI) for a Perl other than the app's own
    Strawberry: Linux's, say, which has had no CGI.pm since 5.22."""
    here = os.path.dirname(os.path.abspath(__file__))
    lib = os.path.join(here, "perl-lib")
    bundled = os.path.join(here, "dist", "DivinumOfficium", "perl")
    if perl and os.path.isdir(lib) and not os.path.abspath(perl).startswith(bundled):
        return (lib,)
    return ()


def _default_typst():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "dist", "DivinumOfficium", "typst", "typst.exe"),
        os.path.join(here, "typst", "typst.exe"),
        os.path.join(
            tempfile.gettempdir(), "do-build", "typst-cache",
            "typst-x86_64-pc-windows-msvc", "typst.exe",
        ),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return shutil.which("typst")


def main(argv=None, web=None, perl=None, typst=None, perl_libs=()):
    """Command line. The app passes its bundled web/, perl and typst in."""
    ap = argparse.ArgumentParser(prog="--make-pdf" if web else None,
                                 description="Make a PDF of the Divine Office.")
    ap.add_argument("first", help="first day, YYYY-MM-DD")
    ap.add_argument("last", nargs="?", help="last day (default: same as first)")
    ap.add_argument("--hours", default="all",
                    help="comma list of %s, or all" % ",".join(dooffice.HOURS))
    ap.add_argument("--version", default="Rubrics 1960 - 1960")
    ap.add_argument("--lang1", default="Latin")
    ap.add_argument("--lang2", default="English", help="same as --lang1 for one column")
    ap.add_argument("--paper", default="Letter (8.5 × 11 in)", choices=list(PAPERS))
    ap.add_argument("--size", type=float, default=10.0, help="font size in points")
    ap.add_argument("--black", action="store_true", help="print rubrics in black")
    add_type_arguments(ap)
    ap.add_argument("-o", "--output", default="office.pdf")
    ap.add_argument("--keep-source", metavar="DIR", help="also keep the .typ source here")
    args = ap.parse_args(argv)

    first = dt.date.fromisoformat(args.first)
    last = dt.date.fromisoformat(args.last) if args.last else first
    hours = list(dooffice.HOURS) if args.hours == "all" else args.hours.split(",")
    unknown = [h for h in hours if h not in dooffice.HOURS]
    if unknown:
        ap.error("unknown hour(s) %s; choose from %s" % (", ".join(unknown), ",".join(dooffice.HOURS)))
    if not (web and perl):
        web, perl = _default_paths()
        perl_libs = perl_libs or _default_libs(perl)
    typst = typst or _default_typst()
    if not typst:
        sys.exit("typst.exe not found")
    engine = dooffice.Engine(web, perl, perl_libs)
    reqs = requests_for(first, last, hours, version=args.version,
                        lang1=args.lang1, lang2=args.lang2)
    layout = Layout(paper=args.paper, font_size=args.size, red_in_print=not args.black,
                    **type_options(args))

    def progress(stage, done, total):
        sys.stderr.write("\r%-8s %d/%d   " % (stage, done, total))

    if args.keep_source:
        os.makedirs(args.keep_source, exist_ok=True)
    _days, failures = export(engine, reqs, layout, args.output, typst, progress=progress,
                             workdir=args.keep_source, keep_source=bool(args.keep_source))
    sys.stderr.write("\nwrote %s\n" % args.output)
    for req, exc in failures:
        sys.stderr.write("  could not render %s %s: %s\n" % (req.date, req.hour, exc))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
