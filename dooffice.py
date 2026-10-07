"""
Get the text of the Divine Office out of the Divinum Officium engine.

officium.pl has a content-only mode (`content=1`) that emits the office without
the website's menus, forms and scripts. This module runs that script directly
with Perl -- no web server involved -- and parses its output into a small
structured model that the PDF writer typesets.

That keeps every rubrical decision (precedence, commemorations, transfers,
which psalter, which version) inside the project's own engine: nothing here
knows or guesses what is said on a given day. It only reorganises what the
engine produced.

The engine's HTML uses a tiny, regular vocabulary (about twenty tag forms
across a whole day, in every version surveyed):

    <P ALIGN=CENTER><FONT COLOR="purple">       the day's title
    <H2 ID='Vesperatop'>                         an hour's heading
    <TR> <TD>[<TD>]                              one section; a cell per language
    <DIV ALIGN='right'>...</DIV>                 Top/Next navigation -- dropped
    <FONT SIZE='+1' COLOR="red"><B><I>           a section heading (Incipit, Psalmi)
    <FONT SIZE='-1'>                             a note beside a heading ({...})
    <FONT COLOR="red">                           rubric text: V. R. Ant. Psalmus 127
    <FONT SIZE='1' COLOR="red">                  verse numbers, the flex mark, small rubrics
    <FONT SIZE='+2' COLOR="red"><B><I>           a red initial (drop cap)
    <span style='color:red...'>                  the sign of the cross
    <span class='nigra'>                         black text inside a red rubric
    <BR/>                                        end of a line
"""

import concurrent.futures
import datetime as dt
import os
import re
import subprocess
from dataclasses import dataclass, field
from html.parser import HTMLParser

import docgi

HOURS = (
    "Matutinum",
    "Laudes",
    "Prima",
    "Tertia",
    "Sexta",
    "Nona",
    "Vespera",
    "Completorium",
)

HOUR_LABELS = {
    "Matutinum": "Matins",
    "Laudes": "Lauds",
    "Prima": "Prime",
    "Tertia": "Terce",
    "Sexta": "Sext",
    "Nona": "Nones",  # not "None": alone in a running head it reads like a missing value
    "Vespera": "Vespers",
    "Completorium": "Compline",
}

RENDER_TIMEOUT = 300  # a Monastic Christmas with all hours takes ~2 s; this is slack


# --------------------------------------------------------------------------
# option lists, read from the same file the website's dropdowns come from


@dataclass(frozen=True)
class Choice:
    label: str  # what the site shows
    value: str  # what the engine expects as a parameter


def family(lang):
    """The language a variant belongs to: "English-Coverdale" (English with
    another psalter) is English, "Latin-Bea" Latin. The engine takes whatever a
    variant's folder lacks from the language before the dash."""
    return lang.split("-")[0] if lang else lang


def read_choices(web_root):
    """Versions, languages, dioceses and votive offices from horas.dialog.

    Entries are either `Name` or `Label/value`; the site shows the label and
    submits the value.
    """
    path = os.path.join(web_root, "www", "horas", "horas.dialog")
    sections, current = {}, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            match = re.match(r"^\[(.+)\]$", line)
            if match:
                current = match.group(1)
                sections[current] = []
            elif current and line:
                sections[current].append(line)

    def parse(name):
        items = []
        for chunk in ",".join(sections.get(name, [])).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            label, _, value = chunk.partition("/")
            items.append(Choice(label.strip(), (value or label).strip()))
        return items

    return {
        "versions": parse("versions"),
        "languages": parse("languages"),
        "dioceses": parse("dioecesis"),
        "votives": parse("votives"),
    }


# --------------------------------------------------------------------------
# the model


@dataclass
class Run:
    text: str
    red: bool = False
    bold: bool = False
    italic: bool = False
    size: str = ""  # "", "small" (verse numbers, flex), "note", "heading", "initial"
    cross: bool = False


@dataclass
class Line:
    runs: list = field(default_factory=list)

    @property
    def text(self):
        return "".join(r.text for r in self.runs)

    @property
    def is_blank(self):
        return not self.text.strip()

    @property
    def is_heading(self):
        first = next((r for r in self.runs if r.text.strip()), None)
        return bool(first and first.size == "heading")

    @property
    def is_rubric(self):
        """Only rubric text: "Psalmus 110 [2]", "Lectio 1", "Hymnus" and the like.

        Such a line introduces what follows, so it should never be left alone
        at the foot of a page.
        """
        body = [r for r in self.runs if r.text.strip()]
        return bool(body) and all(r.red or r.size in ("note", "heading") for r in body)


@dataclass
class Section:
    columns: list = field(default_factory=list)  # one list[Line] per language


@dataclass
class Hour:
    key: str
    heading: str
    sections: list = field(default_factory=list)


@dataclass
class Day:
    date: dt.date
    version: str
    title: str = ""
    subtitle: str = ""  # commemorations etc. printed under the title
    colour: str = ""  # liturgical colour: white, red, green, violet, black, rose
    hours: list = field(default_factory=list)


# --------------------------------------------------------------------------
# parsing


class _OfficeParser(HTMLParser):
    """Walk the engine's HTML, keeping a stack of the styles in force."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts, self.subtitle_parts = [], []
        self.colour = None
        self.hours = []
        self.stack = []  # (tag, style-dict) for every open styling element
        self.skip = 0  # depth inside navigation we are dropping
        self.where = None  # "title" | "heading" | "cell" | None
        self.hour = None
        self.section = None
        self.column = None
        self.line = None

    # -- style bookkeeping -------------------------------------------------

    def style(self):
        merged = {}
        for _tag, st in self.stack:
            merged.update(st)
        return merged

    def push(self, tag, st):
        self.stack.append((tag, st))

    def pop(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    # -- structure ---------------------------------------------------------

    def end_line(self):
        if self.column is not None and self.line is not None:
            self.column.append(_tidy(self.line))
        self.line = Line() if self.column is not None else None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self.skip:
            if tag == "div":
                self.skip += 1
            return

        if tag == "div" and a.get("align", "").lower() == "right":
            self.skip = 1  # the Top / Next links in each cell
            return
        if tag == "h2":
            self.hour = Hour(key=(a.get("id") or "").replace("top", ""), heading="")
            self.hours.append(self.hour)
            self.where = "heading"
            return
        if tag == "tr" and self.hour is not None:
            self.section = Section()
            self.hour.sections.append(self.section)
            return
        if tag == "td" and self.section is not None:
            self.column = []
            self.section.columns.append(self.column)
            self.line = Line()
            self.where = "cell"
            return
        if tag == "br":
            if self.where == "cell":
                self.end_line()
            return

        st = {}
        if tag == "p" and self.hour is None and a.get("align", "").lower() == "center":
            st["zone"] = "title"  # the day's title paragraph, above the first hour
        elif tag == "b":
            st["bold"] = True
        elif tag == "i":
            st["italic"] = True
        elif tag == "font":
            color = a.get("color", "").lower()
            size = a.get("size", "")
            if self.style().get("zone") == "title" and self.colour is None:
                # The title is set in the liturgical colour of the day; an
                # empty colour is white (Christmas, feasts of Our Lady ...).
                self.colour = {"purple": "violet", "": "white", "grey": "black"}.get(
                    color, color
                )
            if color == "red":
                st["red"] = True
            elif color:
                st["red"] = False
            st["size"] = {"1": "small", "-1": "note", "+1": "heading", "+2": "initial"}.get(
                size, self.style().get("size", "")
            )
        elif tag == "span":
            css = a.get("style", "").replace(" ", "").lower()
            if "nigra" in a.get("class", ""):
                st["red"] = False
            if "color:red" in css:
                st["red"] = True
                st["cross"] = True
            if "font-size:82%" in css and self.hour is None:
                # The commemoration line printed under the day's title.
                st["zone"] = "subtitle"
        self.push(tag, st)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == "div":
                self.skip -= 1
            return
        if tag == "h2":
            self.where = None
        elif tag == "td":
            if self.line is not None and not self.line.is_blank:
                self.column.append(_tidy(self.line))
            self.column, self.line, self.where = None, None, None
        elif tag == "tr":
            self.section = None
        self.pop(tag)

    def handle_data(self, data):
        if self.skip or not data:
            return
        if self.where == "heading" and self.hour is not None:
            self.hour.heading += data
            return
        if self.hour is None:
            zone = self.style().get("zone")
            if zone == "title":
                self.title_parts.append(data)
            elif zone == "subtitle":
                self.subtitle_parts.append(data)
            return
        if self.where != "cell" or self.line is None:
            return
        # HTML whitespace: source newlines are just spaces, runs collapse.
        data = re.sub(r"[ \t\r\n]+", " ", data).replace("\xa0", " ")
        st = self.style()
        run = Run(
            text=data,
            red=st.get("red", False),
            bold=st.get("bold", False),
            italic=st.get("italic", False),
            size=st.get("size", ""),
            cross=st.get("cross", False) and data.strip() in ("✠", "✙", "+"),
        )
        last = self.line.runs[-1] if self.line.runs else None
        if last and (last.red, last.bold, last.italic, last.size, last.cross) == (
            run.red, run.bold, run.italic, run.size, run.cross
        ):
            last.text += run.text
        else:
            self.line.runs.append(run)


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


def _tidy(line):
    """Trim a line's outer whitespace and collapse spaces across run joins."""
    runs = [r for r in line.runs if r.text]
    for i in range(1, len(runs)):
        if runs[i - 1].text.endswith(" ") and runs[i].text.startswith(" "):
            runs[i].text = runs[i].text[1:]
    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    line.runs = [r for r in runs if r.text]
    return line


# Text once in Windows-1252, read as Latin-1: its curly quotes and dashes came
# through as control characters (U+0092 for ’, U+0094 for ”: "the lion’s
# mouth" in the English Palm Sunday Tract), which no font draws. Read them as
# the characters they were.
_C1 = re.compile("[\x80-\x9f]")


def _cp1252(m):
    try:
        return bytes([ord(m.group())]).decode("cp1252")
    except UnicodeDecodeError:  # 0x81, 0x8d, 0x8f, 0x90, 0x9d: nothing there
        return ""


def parse(html, date=None, version=""):
    """Parse officium.pl content-mode HTML into a Day."""
    body = html.split("\n\n", 1)[1] if html.startswith("Content-") else html
    body = _C1.sub(_cp1252, body)
    p = _OfficeParser()
    p.feed(body)
    p.close()
    day = Day(date=date, version=version)
    day.title = _clean("".join(p.title_parts))
    day.colour = p.colour or ""
    # The commemoration line sits in the same centred paragraph as the title.
    sub = _clean("".join(p.subtitle_parts))
    if sub and sub not in day.title:
        day.subtitle = sub
    for hour in p.hours:
        hour.heading = _clean(hour.heading)
        hour.key = hour.key or hour.heading
        # Drop leading/trailing blank lines inside each cell.
        for section in hour.sections:
            for col in section.columns:
                while col and col[0].is_blank:
                    col.pop(0)
                while col and col[-1].is_blank:
                    col.pop()
        hour.sections = [s for s in hour.sections if any(s.columns)]
    day.hours = [h for h in p.hours if h.sections]
    return day


# --------------------------------------------------------------------------
# running the engine


@dataclass(frozen=True)
class Request:
    date: dt.date
    hour: str = "Omnes"  # one of HOURS, or Omnes for the whole day
    version: str = "Rubrics 1960 - 1960"
    lang1: str = "Latin"
    lang2: str = "English"  # equal to lang1 for a single column
    votive: str = ""  # "" is the office of the day; C12 is the Little Office of the BVM
    dioecesis: str = "Generale"
    priest: bool = False
    # "Temporal": the office of the season alone, as if no saint were kept --
    # the breviary builder reads the Psalter and the Ordinary from such days.
    testmode: str = "regular"

    def query(self):
        from urllib.parse import quote

        params = [
            ("command", "pray" + self.hour),
            ("date", "%d-%d-%d" % (self.date.month, self.date.day, self.date.year)),
            ("version", self.version),
            ("lang1", self.lang1),
            ("lang2", self.lang2),
            ("dioecesis", self.dioecesis),
            ("testmode", self.testmode),
            ("content", "1"),
        ]
        if self.votive:
            params.append(("votive", self.votive))
        if self.priest:
            params.append(("priest", "yes"))
        return "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in params)


class Engine:
    """Runs officium.pl for a Request and returns the parsed Day."""

    def __init__(self, web_root, perl, perl_libs=()):
        # Paths Perl is given are plain ASCII (see docgi.perl_path).
        self.web_root = docgi.perl_path(os.path.abspath(web_root))
        self.perl = docgi.perl_path(perl)
        self.perl_libs = [docgi.perl_path(p) for p in perl_libs]
        self.script_dir = os.path.join(self.web_root, "cgi-bin", "horas")
        if not os.path.isfile(os.path.join(self.script_dir, "officium.pl")):
            raise FileNotFoundError("no officium.pl under %s" % self.script_dir)

    def html(self, req):
        env = dict(os.environ)
        env.pop("PERL5LIB", None)
        for key in list(env):
            if key.startswith("HTTP_"):
                del env[key]  # in particular, never inherit a cookie
        env.update(
            {
                "GATEWAY_INTERFACE": "CGI/1.1",
                "REQUEST_METHOD": "GET",
                "QUERY_STRING": req.query(),
                "SCRIPT_NAME": "/cgi-bin/horas/officium.pl",
                "SERVER_NAME": "localhost",
                "SERVER_PORT": "0",
            }
        )
        cmd = [self.perl] + [a for lib in self.perl_libs for a in ("-I", lib)]
        # Bare script name: officium.pl derives its own name from $0 (see docgi.py).
        cmd.append("officium.pl")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.run(
            cmd,
            cwd=self.script_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=RENDER_TIMEOUT,
            creationflags=flags,
        )
        out = proc.stdout.decode("utf-8", "replace")
        if proc.returncode != 0 or "<H2" not in out:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(
                "engine failed for %s %s (%s): %s"
                % (req.date, req.hour, req.version, err[-400:] or "no output")
            )
        return out

    def day(self, req):
        return parse(self.html(req), date=req.date, version=req.version)

    def days(self, requests, workers=None, progress=None, cancelled=None):
        """Render many requests in parallel; return results in request order.

        Each result is a Day, or the exception the engine raised for that
        request -- over a year of dates, one bad day should be reported, not
        lose the other 364. Each render is its own Perl process, so threads
        give real parallelism here.
        """
        workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
        requests = list(requests)
        results = [None] * len(requests)
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.day, r): i for i, r in enumerate(requests)}
            try:
                for fut in concurrent.futures.as_completed(futures):
                    if cancelled and cancelled():
                        raise Cancelled()
                    try:
                        results[futures[fut]] = fut.result()
                    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                        results[futures[fut]] = exc
                    done += 1
                    if progress:
                        progress(done, len(requests))
            except BaseException:
                for f in futures:
                    f.cancel()
                raise
        return results


class Cancelled(Exception):
    """The user stopped an export."""


def date_range(start, end):
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)
