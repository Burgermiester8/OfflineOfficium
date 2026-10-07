"""
The parts of a breviary that are the frame of every day's office rather than
files of their own: the Ordinary, the Psalter and the Common of the Seasons,
built here from the engine's own offices -- and the Calendar and the prayers
before and after the Office, from its data.

Asked for a day with the saints left out (testmode=Temporal), officium.pl
gives the office of the season, and it labels each part of each hour with
where that part comes from: {ex Psalterio secundum diem}, {ex Psalterio
secundum tempora}, {ex Proprio de Tempore} ... Those labels do the sorting:

- the Psalter is a week of weekdays through the year, keeping what the engine
  says comes from the Psalter;
- the Ordinary is the same hours, keeping in full what has no label (it is
  said alike every day) and naming the rest;
- the Common of the Seasons is a weekday of each season, keeping what is said
  then that is neither in the Psalter nor in the Proper of the Season --
  Creator alme siderum, Audi benigne Conditor, the seasons' chapters at the
  little hours, and so on.

So the engine still decides every text, including each version's own psalter
(the Tridentine, the 1911 and the Monastic distributions all differ).
"""

import calendar
import datetime as dt
import html
import re
import unicodedata
from dataclasses import dataclass, field

import dooffice
import dopdf

D = dt.timedelta

HOUR_NAMES = {
    "Matutinum": ("Ad Matutinum", "At Matins"),
    "Laudes": ("Ad Laudes", "At Lauds"),
    "Prima": ("Ad Primam", "At Prime"),
    "Tertia": ("Ad Tertiam", "At Terce"),
    "Sexta": ("Ad Sextam", "At Sext"),
    "Nona": ("Ad Nonam", "At Nones"),
    "Vespera": ("Ad Vesperas", "At Vespers"),
    "Completorium": ("Ad Completorium", "At Compline"),
}
WEEKDAYS = (("Dominica", "Sunday"), ("Feria II", "Monday"), ("Feria III", "Tuesday"),
            ("Feria IV", "Wednesday"), ("Feria V", "Thursday"), ("Feria VI", "Friday"),
            ("Sabbato", "Saturday"))
MONTHS_LA = ("Januarius", "Februarius", "Martius", "Aprilis", "Majus", "Junius", "Julius",
             "Augustus", "September", "October", "November", "December")

# --------------------------------------------------------------------------
# the sample days


def easter(year):
    """Easter Sunday (Gregorian), by the anonymous algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


@dataclass(frozen=True)
class Season:
    key: str
    la: str
    en: str
    date: dt.date


def samples(year=2026):
    """The days whose offices these parts are read from.

    week: Sunday to Saturday of the seventh week after Pentecost -- clear of
    the octaves of Corpus Christi and the Sacred Heart that older versions keep.
    winter: a Sunday in November, for the Sunday hymns of the winter months.
    advent: the weekdays of the first week of Advent, whose Lauds take the
    second, penitential, scheme of psalms.
    seasons: a weekday of each season (a Wednesday in Advent, so that the
    ferial preces are said even under the 1960 rubrics).
    """
    e, e2 = easter(year), easter(year + 1)
    sunday = e + D(days=98)  # Pentecost + 7 weeks
    nov8 = dt.date(year, 11, 8)
    winter = nov8 + D(days=(6 - nov8.weekday()) % 7)
    dec3 = dt.date(year, 12, 3)
    advent = dec3 - D(days=(dec3.weekday() + 1) % 7)  # the Sunday nearest St Andrew
    seasons = (
        Season("adv", "Tempore Adventus", "In Advent", advent + D(days=3)),
        Season("quadp", "Tempore Septuagesimæ", "From Septuagesima to Lent", e2 - D(days=62)),
        # The second week of Lent: the first holds the Ember days.
        Season("quad", "Tempore Quadragesimæ", "In Lent", e2 - D(days=34)),
        Season("quad5", "Tempore Passionis", "In Passiontide", e2 - D(days=13)),
        # The fourth week after Easter: the second and third hold, before 1955,
        # the solemnity of St Joseph and its octave.
        Season("pasch", "Tempore Paschali", "In Eastertide", e2 + D(days=29)),
        Season("asc", "Ab Ascensione usque ad Pentecosten", "From the Ascension to Pentecost",
               e2 + D(days=47)),
    )
    return {
        "week": [sunday + D(days=i) for i in range(7)],
        "winter": winter,
        "advent": [advent + D(days=i) for i in range(1, 7)],
        "seasons": seasons,
        # The litanies after Lauds: Rogation Monday, and St Mark (25 April),
        # the only day the 1960 rubrics keep them.
        "litany": [e2 + D(days=36), dt.date(year + 1, 4, 25)],
        # A Saturday through the year is kept as the Office of Our Lady on
        # Saturday, which puts the Common's hymns and chapters (and, monastic,
        # its psalms at the little hours) in the place of the Psalter's. The
        # September Ember Saturday outranks it and keeps the Psalter's: after
        # 14 September before 1960, after the third Sunday of September since.
        "saturdays": [dt.date(year, 9, 19), dt.date(year, 9, 26)],
    }


def season_week(season):
    """Sunday to Saturday of the week of a season's sample day: some of its texts
    change with the weekday (the Eastertide versicles of Matins ...)."""
    sunday = season.date - D(days=(season.date.weekday() + 1) % 7)
    return [sunday + D(days=i) for i in range(7)]


# --------------------------------------------------------------------------
# reading the engine's labels


# The headings of an hour's parts (the '#' items of the Ordinarium files, and
# a few the engine adds). Any other heading -- "Lectio 1" inside Matins -- is
# part of the item it appears in.
_ITEM = re.compile(
    r"^(Incipit|Invitatorium|Hymnus|Psalmi|Capitulum|Canticum|Preces|Oratio|Suffragium|"
    r"Conclusio|Antiphona finalis|Antiphonae final|Martyrologium|De Officio Capituli|"
    r"Lectio brevis|Regula|Commemoratio|Versus in loco|Prelude)")
PSALTER = ("ex Psalterio", "Per Annum", "de Dominica")  # [Source] in Psalterium/Comment.txt
PROPER = ("ex Proprio", "ex Commune", "Votiva")
_PSALM_TITLE = re.compile(r"\[\d+\]\s*$")  # "Psalmus 46 [1]", "Canticum David [4]"
_LESSONISH = ("Iube, Dómine", "Jube, Dómine", "Iube Dómine", "Jube Dómine", "Benedictio.",
              "Absolutio.", "Pater Noster", "Pater noster", "Te Deum", "Reliqua omittuntur",
              "sancti Evangélii", "sancti Evangelii")


@dataclass
class Item:
    """One part of an hour: its heading, the engine's note on its source, its sections."""
    label: str
    note: str = ""
    kind: str = ""  # "psalter", "proper", "" (said alike every day), "omitted"
    parts: list = field(default_factory=list)  # (dooffice.Section, Latin lines)


def _clean(s):
    return re.sub(r"\s+", " ", s).strip()


def _kind(note):
    if any(s in note for s in PSALTER):
        return "psalter"
    if any(s in note for s in PROPER):
        return "proper"
    return ""


def hour_items(hour, latin_hour, col):
    """Group an hour's sections into its items, labelled from the Latin column."""
    items, cur = [], None
    for sec, lsec in zip(hour.sections, latin_hour.sections):
        lat = lsec.columns[col] if len(lsec.columns) > col else lsec.columns[0]
        first = lat[0] if lat else None
        if first is not None and first.is_heading:
            label = _clean("".join(r.text for r in first.runs if r.size == "heading"))
            if _ITEM.match(label):
                note = _clean("".join(r.text for r in first.runs if r.size == "note")).strip("{} ")
                cur = Item(label=label, note=note, kind=_kind(note))
                items.append(cur)
        elif (first is not None and len(lat) == 1 and "{" in first.text
              and all(r.size == "note" for r in first.runs if r.text.strip())):
            # "Preces Feriales{omittitur}": an item not said today.
            items.append(Item(label=_clean(first.text.split("{")[0]), kind="omitted"))
            cur = None
            continue
        if cur is None:
            cur = Item(label="")
            items.append(cur)
        cur.parts.append((sec, lat))
    return items


def _latin_hours(day, latin_day):
    if latin_day is None:
        return {h.key: h for h in day.hours}
    return {h.key: h for h in latin_day.hours}


def day_items(day, latin_day, col):
    """{hour: [Item]} for a rendered day.

    latin_day: the same day rendered in Latin alone, when neither of the
    book's languages is Latin (the labels are read in Latin); col: the column
    holding the Latin (0 or 1), or 0 for latin_day.
    """
    lat = _latin_hours(day, latin_day)
    out = {}
    for h in day.hours:
        lh = lat.get(h.key)
        c = 0 if latin_day is not None else col
        if lh is None or len(lh.sections) != len(h.sections):
            lh, c = h, 0  # cannot align: read the labels in the first language
        out[h.key] = hour_items(h, lh, c)
    return out


# --------------------------------------------------------------------------
# small tools on sections


def _copy_line(line, drop_notes=False):
    runs = [dooffice.Run(**vars(r)) for r in line.runs if not (drop_notes and r.size == "note")]
    if runs:
        runs[-1].text = runs[-1].text.rstrip()
    return dooffice.Line(runs=runs)


def _strip_notes(section):
    """A copy with the engine's notes taken off: all of them on its heading lines
    (the sources, {ex Psalterio ...}), and anywhere those in braces, its hints
    ("Hymnus {Doxology: Pasch}")."""
    cols = []
    for col in section.columns:
        out = []
        for line in col:
            copy = _copy_line(line, drop_notes=line.is_heading)
            kept = [r for r in copy.runs
                    if not (r.size == "note" and re.fullmatch(r"\s*\{.*\}\s*", r.text))]
            if kept and len(kept) < len(copy.runs):
                kept[-1].text = kept[-1].text.rstrip()
                copy.runs = kept
            out.append(copy)
        cols.append(out)
    return dooffice.Section(columns=cols)


def _heading_of(item):
    """Just the heading lines of an item, as a section of their own."""
    if not item.parts or not item.parts[0][0].columns:
        return None
    sec = item.parts[0][0]
    cols = []
    for col in sec.columns:
        cols.append([_copy_line(col[0], drop_notes=True)] if col and col[0].is_heading else [])
    return dooffice.Section(columns=cols) if any(cols) else None


def _rubric(text):
    return dooffice.Line(runs=[dooffice.Run(text=text, red=True, italic=True)])


def _heading(text):
    return dooffice.Line(runs=[dooffice.Run(text=text, size="heading", red=True, bold=True,
                                            italic=True)])


class Langs:
    """Which language each column is in, for the few texts written here."""

    def __init__(self, lang1, lang2, version=""):
        self.langs = [lang1] if lang1 == lang2 else [lang1, lang2]
        # The engine's spell_var: the 1960s versions write i for j in Latin
        # ("Iube", "cuius"), and the texts written here should match its own.
        self.i_for_j = "196" in version

    def pick(self, la, en, i):
        if dooffice.family(self.langs[i]) == "English":
            return en
        return la.replace("j", "i").replace("J", "I") if self.i_for_j else la

    def lines(self, la, en, make=_rubric):
        return [[make(self.pick(la, en, i))] for i in range(len(self.langs))]

    def section(self, la, en, make=_rubric):
        return dooffice.Section(columns=self.lines(la, en, make))


def _append(section, cols_lines, after_heading=False):
    """Add lines to each column of a section (after its heading, or at the end)."""
    for i, col in enumerate(section.columns):
        extra = cols_lines[i] if i < len(cols_lines) else cols_lines[0]
        if after_heading and col and col[0].is_heading:
            col[1:1] = [_copy_line(l) for l in extra]
        else:
            col.extend(_copy_line(l) for l in extra)
    return section


def _is_ant(line):
    return line.text.startswith("Ant.")


def _antiphon_list(item, parts):
    """An item's antiphons, each once, under the item's heading (no psalms).

    Before a psalm only an antiphon's opening may be said and after it the
    whole: the fuller form of each is kept.
    """
    ncol = max((len(sec.columns) for sec, _ in parts), default=0)
    first = item.parts[0][0] if item.parts else None
    cols = []
    for i in range(ncol):
        head = []
        if first is not None and i < len(first.columns) and first.columns[i] \
                and first.columns[i][0].is_heading:
            head = [_copy_line(first.columns[i][0], drop_notes=True)]
        ants, order = {}, []
        for sec, _ in parts:
            for line in (sec.columns[i] if i < len(sec.columns) else []):
                if not _is_ant(line):
                    continue
                key = (_fp(line.text) or line.text)[:16]
                if key not in ants:
                    order.append(key)
                    ants[key] = line
                elif len(line.text) > len(ants[key].text):
                    ants[key] = line
        cols.append(head + [_copy_line(ants[k]) for k in order])
    return dooffice.Section(columns=cols)


def _antiphon_of(item):
    """A canticle cut down to its heading and antiphon."""
    if not item.parts:
        return None
    sec = item.parts[0][0]
    cols = []
    for col in sec.columns:
        head = [_copy_line(col[0], drop_notes=True)] if col and col[0].is_heading else []
        ants = [l for l in col if _is_ant(l)]
        cols.append(head + ([_copy_line(max(ants, key=lambda l: len(l.text)))] if ants else []))
    return dooffice.Section(columns=cols)


def _without_antiphons(section):
    """A canticle or the invitatory psalm with its antiphon marked only "Ant."

    (It is the same every day; the antiphon is the day's.)
    """
    cols = []
    for col in section.columns:
        out = []
        for i, line in enumerate(col):
            if _is_ant(line):
                if not (out and out[-1].text == "Ant."):
                    out.append(dooffice.Line(runs=[dooffice.Run(text="Ant.", red=True, italic=True)]))
            else:
                out.append(_copy_line(line, drop_notes=(i == 0 and line.is_heading)))
        cols.append(out)
    return dooffice.Section(columns=cols)


def _matins_kind(lat):
    """A section inside Matins' psalms: a psalm, a lesson (or its blessing), or other."""
    if any(_is_ant(l) or (l.is_rubric and _PSALM_TITLE.search(l.text)) for l in lat):
        return "psalm"
    text = "\n".join(l.text for l in lat)
    if any(w in text for w in _LESSONISH):
        return "lesson"
    return "other"


_LABEL = re.compile(r"^\s*(Ant\.|℟\.\s?br\.|R\.\s?br\.|℣\.|℟\.|V\.|R\.|Benedictio\.|Absolutio\.|"
                    r"\d+\.)\s*")
_PSREF = re.compile(r"\s+Ps\.\s[\d\s,()-]+$")  # the "Ps. 8" the book adds to an antiphon


def _fp(text):
    """A fingerprint of a line of text, blind to accents, j/i, marks and prefixes."""
    t = _PSREF.sub("", _LABEL.sub("", text))
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if c.isalpha()).replace("j", "i")
    return t[:40] if len(t) >= 16 else None


def _fps(lines):
    return [f for f in (_fp(l.text) for l in lines) if f]


def _is_new(section, known, only=None):
    fps = _fps(only if only is not None else section.columns[0])
    if not fps:
        return False
    return sum(f not in known for f in fps) * 2 > len(fps)


def html_fps(html_text):
    """Fingerprints of every line of a dumped section's HTML."""
    out = []
    for piece in re.split(r"<br\s*/?>|\n", html_text or ""):
        f = _fp(html.unescape(re.sub(r"<[^>]*>", "", piece)))
        if f:
            out.append(f)
    return out


# --------------------------------------------------------------------------
# rendering


@dataclass
class Renders:
    week: list  # [(Day, Latin Day or None)] Sunday .. Saturday
    winter: tuple  # (Day, Latin Day or None), a winter Sunday
    advent: list  # [(Day, Latin Day or None)] Monday .. Saturday of Advent, Lauds only
    seasons: list  # [(Season, Day, Latin Day or None)], each season's sample day
    col: int  # the Latin column of the book's own renders (when no Latin Day is needed)
    weeks: dict = field(default_factory=dict)  # season key -> [(date, Day, Latin Day or None)]
    litany: list = field(default_factory=list)  # [(Day, Latin Day or None)], Lauds only
    saturday: tuple = None  # (Day, Latin Day or None): a ferial Saturday, the Ember Saturday


def _plan(year=2026):
    s = samples(year)
    plan = [("week", d, "Omnes") for d in s["week"]]
    plan.append(("winter", s["winter"], "Omnes"))
    plan += [("advent", d, "Laudes") for d in s["advent"]]
    plan += [(("season", se.key), d, "Omnes") for se in s["seasons"] for d in season_week(se)]
    plan += [("litany", d, "Laudes") for d in s["litany"]]
    plan += [("saturday", d, "Omnes") for d in s["saturdays"]]
    return s, plan


def render(engine, version, lang1, lang2, progress=None, cancelled=None, year=2026):
    """Render every sample day (in parallel), in the book's languages.

    When neither language is Latin, each day is also rendered in Latin alone,
    only to read the engine's labels.
    """
    s, plan = _plan(year)
    col = 0 if lang1.startswith("Latin") else (1 if lang2.startswith("Latin") and lang2 != lang1 else None)
    langs = [(lang1, lang2)] + ([("Latin", "Latin")] if col is None else [])
    reqs = [dooffice.Request(date=d, hour=h, version=version, lang1=a, lang2=b, testmode="Temporal")
            for a, b in langs for _k, d, h in plan]
    results = engine.days(reqs, progress=progress, cancelled=cancelled)
    n = len(plan)
    book, latin = results[:n], (results[n:] if col is None else [None] * n)
    for (kind, d, _h), r in zip(plan, book):
        if isinstance(r, Exception) and kind in ("week", "winter", "advent"):
            raise RuntimeError("the engine could not give the office of %s: %s" % (d, r))
    got = {}
    for (kind, d, _h), r, lat in zip(plan, book, latin):
        if not isinstance(r, Exception):
            got.setdefault(kind, []).append((d, r, None if isinstance(lat, Exception) else lat))
    week = [(r, lat) for _d, r, lat in got["week"]]
    winter = [(r, lat) for _d, r, lat in got["winter"]][0]
    advent = [(r, lat) for _d, r, lat in got["advent"]]
    weeks, seasons = {}, []
    for se in s["seasons"]:
        days = got.get(("season", se.key), [])
        if days:
            weeks[se.key] = days
            main = next(((r, lat) for d, r, lat in days if d == se.date), None)
            if main:
                seasons.append((se, main[0], main[1]))
    litany = [(r, lat) for _d, r, lat in got.get("litany", [])]
    c = col if col is not None else 0
    saturday = None
    for _d, r, lat in got.get("saturday", []):
        hymn = next((it for it in day_items(r, lat, c).get("Matutinum", [])
                     if it.label == "Hymnus"), None)
        if hymn is not None and hymn.kind == "psalter":
            saturday = (r, lat)  # the Ember Saturday, not the Office of Our Lady
            break
    return Renders(week=week, winter=winter, advent=advent, seasons=seasons,
                   col=c, weeks=weeks, litany=litany, saturday=saturday)


def _items(pair, col):
    day, lat = pair
    return day_items(day, lat, col)


def week_items(renders, wd):
    """{hour: [Item]} for weekday wd of the week through the year.

    Saturday is the Office of Our Lady there: what it takes from the Common in
    place of the Psalter comes instead from the ferial (Ember) Saturday, where
    the engine gives it from the Psalter.
    """
    items = _items(renders.week[wd], renders.col)
    if wd != 6 or not renders.saturday:
        return items
    ferial = _items(renders.saturday, renders.col)
    merged = {}
    for hkey, its in items.items():
        alt = [x for x in ferial.get(hkey, []) if x.kind == "psalter"]
        out = []
        for it in its:
            if it.kind == "proper":
                sub = next((x for x in alt if x.label == it.label), None) or next(
                    (x for x in alt if x.label.split()[0] == it.label.split()[0]), None)
                # Left over from the Common (Our Lady's Benedictus antiphon ...):
                # not the Psalter's, whatever the engine's label says.
                it = sub or Item(label=it.label, note=it.note, kind="commune", parts=it.parts)
            out.append(it)
        merged[hkey] = out
    return merged


# --------------------------------------------------------------------------
# the Psalter


def _psalter_items(items, proper=None):
    """What of an hour comes from the Psalter, item by item: [(Item, [Section])].

    The invitatory and the canticles give only their antiphon (the psalm and
    the canticle themselves are in the Ordinary). The engine calls a weekday's
    Benedictus and Magnificat antiphons "of the Proper of the Season" even
    where they come from the Psalter's own data; `proper` (fingerprints of the
    Proper of the Season) tells the two apart.
    """
    out = []
    for it in items:
        if it.label.startswith(("Canticum", "Invitatorium")):
            if it.kind not in ("psalter", "proper"):
                continue  # the same every day: in the Ordinary
            ant = _antiphon_of(it)
            ants = [l for l in ant.columns[0] if _is_ant(l)] if ant else []
            if not ants:
                continue
            if it.kind == "psalter" or (proper is not None
                                        and not any(f in proper for f in _fps(ants))):
                out.append((it, [ant]))
            continue
        if it.kind != "psalter":
            continue
        if it.label.startswith("Psalmi cum lectionibus"):
            secs = [_strip_notes(sec) for sec, lat in it.parts if _matins_kind(lat) != "lesson"]
        else:
            secs = [_strip_notes(sec) for sec, _lat in it.parts]
        if secs:
            out.append((it, secs))
    return out


def _psalter_sections(items, proper=None):
    return [sec for _it, secs in _psalter_items(items, proper) for sec in secs]


def _signature(sections):
    return tuple(f for s in sections for f in _fps(s.columns[0]))


def _ascension_chapter(renders):
    """The chapter closing the second Nocturn on a weekday after the Ascension, as
    the engine gives it (columns of Lines, without their heading), or None."""
    for se, day, lat in renders.seasons:
        if se.key != "asc":
            continue
        it = next((x for x in day_items(day, lat, renders.col).get("Matutinum", [])
                   if x.label == "Capitulum" and x.parts), None)
        if it:
            return [c[1:] if c and c[0].is_heading else c
                    for c in _strip_notes(it.parts[0][0]).columns]
    return None


def _saturday_benedictus(items, fixed, langs, entries):
    """A ferial Saturday's Benedictus antiphon, from the Psalter's data (Feria7 Ant 2)."""
    if any(it.label.startswith("Canticum") for it, _ in entries):
        return []
    sec = _fixed(fixed, "major").get("Feria7 Ant 2")
    canticle = next((it for it in items if it.label.startswith("Canticum")), None)
    if not sec or canticle is None:
        return []
    text = _dumped(sec, len(langs.langs) == 2)
    head = _heading_of(canticle)
    if head is None:
        return []
    ant = [[dooffice.Line(runs=[dooffice.Run(text="Ant. ", red=True)] + [dooffice.Run(**vars(r))
                                                                           for r in l.runs])
            for l in col if not l.is_blank][:1] for col in text.columns]
    _append(head, ant)
    return [_block(canticle.label, head)]


def _block(label, section):
    """A section built here, passed through the Psalter like one of the engine's items."""
    return Item(label=label, kind="psalter", parts=[(section, section.columns[0])]), [section]


def psalter_offices(part, renders, langs, proper=None, fixed=None, family="roman"):
    from dobreviary import Office

    blessings = Blessings(fixed, langs, family, sunday_lessons(renders)) if fixed else None
    ascension = _ascension_chapter(renders) if family in ("monastic", "cist") else None
    week = [week_items(renders, wd) for wd in range(len(renders.week))]
    winter = _items(renders.winter, renders.col)
    advent = [_items(p, renders.col) for p in renders.advent]
    offices = []
    for wd, items in enumerate(week):
        hours = []
        for hkey, (la, _en) in HOUR_NAMES.items():
            secs = []
            w_items = {}
            if wd == 0 and hkey in ("Matutinum", "Laudes"):
                w_items = {it.label: s for it, s in _psalter_items(winter.get(hkey, []), proper)}
            entries = _psalter_items(items.get(hkey, []), proper)
            if hkey == "Laudes" and wd == 6 and fixed:
                entries += _saturday_benedictus(items.get("Laudes", []), fixed, langs, entries)
            if hkey == "Matutinum" and blessings:
                # At the end of Matins, what a book gives there for any office of
                # the day: its absolution and blessings, and (monastic) the short
                # lesson and the chapters of the seasons.
                entries.append(_block("Absolutio et Benedictiones", blessings.day(wd)))
                if family in ("monastic", "cist") and wd:
                    entries += [_block(lab, sec)
                                for lab, sec in monastic_matins(fixed, langs, wd, ascension)]
            for it, mine in entries:
                theirs = w_items.get(it.label)
                if theirs and _signature(theirs) != _signature(mine):
                    # Sunday's hymns differ in summer and winter: both are given.
                    secs += _seasoned(mine, "Tempore æstivo", "In summer", langs)
                    secs += _seasoned(theirs, "Tempore hiemali", "In winter", langs)
                    continue
                # Each day in full, what is said as on an earlier day too (the
                # hymns and chapters of the little hours): the Psalter is read
                # without turning to another day.
                secs += mine
            if secs:
                hours.append(dooffice.Hour(key=hkey, heading=la, sections=secs))
            if hkey == "Laudes" and 1 <= wd <= 6:
                # The second scheme of Lauds, for the penitential weekdays.
                adv = [it for it in advent[wd - 1].get("Laudes", [])
                       if it.label == "Psalmi" and it.kind == "psalter"]
                ours = [it for it in items.get("Laudes", []) if it.label == "Psalmi"]
                same = {_signature([x]) for x in _psalter_sections(ours)}
                # Only what differs from the first scheme (in the Monastic
                # psalter, a single canticle).
                secs2 = [x for x in _psalter_sections(adv) if _signature([x]) not in same]
                if secs2 and ours:
                    first = secs2[0]
                    if first.columns and first.columns[0] and not first.columns[0][0].is_heading:
                        head = _heading_of(adv[0])
                        for i, col in enumerate(first.columns):
                            if head and i < len(head.columns):
                                col[0:0] = head.columns[i]
                    _append(first, langs.lines("In feriis temporis pœnitentialis",
                                               "On weekdays of the penitential seasons"),
                            after_heading=True)
                    hours.append(dooffice.Hour(key="Laudes", heading="Ad Laudes II", sections=secs2))
        la, en = WEEKDAYS[wd]
        offices.append(Office(part=part, key="psalter-%d" % wd, title=la, title2=en, hours=hours))
    return offices


def _seasoned(sections, la, en, langs):
    if not sections:
        return sections
    first = sections[0]
    copy = dooffice.Section(columns=[list(c) for c in first.columns])
    _append(copy, langs.lines(la, en), after_heading=True)
    return [copy] + sections[1:]


# --------------------------------------------------------------------------
# the absolutions and blessings of Matins
#
# Which absolution and which blessings are said is decided by the engine's
# code (get_absolutio_et_benedictiones in specmatins.pl, absolutio_benedictio
# in monastic.pl), not by any file; the texts are its data
# (Psalterium/Benedictions.txt). The rules are written out here as a
# breviary's rubrics, each branch of that code in turn:
#
# - nine lessons (twelve, monastic): each Nocturn its own absolution and
#   blessings; in the third the first is "Evangelica lectio"; on saints' feasts
#   "Divinum auxilium" gives way to "Cujus festum colimus ..." (ipse, ipsi,
#   ipsa, ipsæ, Virgo virginum -- cujus_q); a last lesson from a Gospel takes
#   "Per evangelica dicta"; Christmas has its own;
# - three lessons: the absolution by the weekday (dayofweek2i); the blessings
#   of a feria by the weekday, of a saint "Ille nos / Cujus festum / Ad
#   societatem", of a homily day (vigils, Ember days, Lent ...) "Evangelica
#   lectio / Divinum auxilium / Ad societatem", of a Sunday of three lessons
#   "Ille nos / Divinum auxilium / Per evangelica dicta";
# - the monastic short lesson of summer: the weekday's absolution and one
#   blessing, the fourth (or third) of the weekday's Nocturn.

WEEKDAY_GROUPS = (  # dayofweek2i: the absolution and ferial blessings of three-lesson days
    (1, "Dominica, Feria II et Feria V", "Sunday, Monday and Thursday"),
    (2, "Feria III et Feria VI", "Tuesday and Friday"),
    (3, "Feria IV et Sabbato", "Wednesday and Saturday"),
)
FERIA_GROUPS = (  # the same, for the weekdays alone
    (1, "Feria II et Feria V", "Monday and Thursday"),
    (2, "Feria III et Feria VI", "Tuesday and Friday"),
    (3, "Feria IV et Sabbato", "Wednesday and Saturday"),
)
CUJUS = (  # [Nocturn 3] entry replacing "Divinum auxilium" on saints' feasts (cujus_q)
    (3, "De uno Sancto", "Of one Saint"),
    (4, "De pluribus Sanctis", "Of several Saints"),
    (5, "De una Sancta", "Of one holy woman"),
    (6, "De pluribus Sanctis mulieribus", "Of several holy women"),
    (7, "In festis Beatæ Mariæ Virginis", "On feasts of Our Lady"),
    (8, "In festis S. P. N. Benedicti", "On feasts of our holy Father Benedict"),
)
HOMILY = ("Quando prima lectio est homilia in Evangelium (in Vigiliis, Quattuor Temporibus, "
          "feriis Quadragesimæ et Passionis, Feria II Rogationum, infra Octavas Paschæ et "
          "Pentecostes):",
          "When the first lesson is a homily on the Gospel (on vigils, Ember days, the weekdays "
          "of Lent and Passiontide, Rogation Monday, within the octaves of Easter and "
          "Pentecost):")
ORDINALS = ("I", "II", "III")
ORDINALS_EN = ("first", "second", "third")
CAPITULA = (  # the chapter closing the second monastic Nocturn, by season (gettempora)
    ("MM Capitulum", "Per annum", "Through the year"),
    ("MM Capitulum Adv", "Tempore Adventus", "In Advent"),
    ("MM Capitulum Nat", "Tempore Nativitatis", "At Christmastide"),
    ("MM Capitulum Epi", "Tempore Epiphaniæ", "At Epiphanytide"),
    ("MM Capitulum Quad", "Tempore Quadragesimæ", "In Lent"),
    ("MM Capitulum Quad5", "Tempore Passionis", "In Passiontide"),
    ("MM Capitulum Pasch", "Tempore Paschali", "In Eastertide"),
)


def day_index(wd):
    """The engine's dayofweek2i: 1 Sunday, Monday, Thursday; 2 Tuesday, Friday; 3 Wednesday, Saturday."""
    i = wd or 1
    return i - 3 if i > 3 else i


class _Sheet:
    """A section built line by line, in each of the book's columns."""

    def __init__(self, langs):
        self.langs = langs
        self.cols = [[] for _ in langs.langs]

    def heading(self, la, en):
        for i, col in enumerate(self.cols):
            col.append(_heading(self.langs.pick(la, en, i)))

    def rubric(self, la, en):
        for i, col in enumerate(self.cols):
            col.append(_rubric(self.langs.pick(la, en, i)))

    def text(self, label_la, label_en, lines):
        """A text (one Line per column), after a red label ("Absolutio.", "4.")."""
        if not lines:
            return
        for i, col in enumerate(self.cols):
            label = self.langs.pick(label_la, label_en, i)
            runs = [dooffice.Run(text=label + " ", red=True)] if label else []
            col.append(dooffice.Line(runs=runs + [dooffice.Run(**vars(r)) for r in lines[i].runs]))

    def lines(self, cols):
        for i, col in enumerate(self.cols):
            col.extend(_copy_line(l) for l in cols[min(i, len(cols) - 1)])

    def gap(self):
        for col in self.cols:
            if col and not col[-1].is_blank:
                col.append(dooffice.Line())

    def section(self):
        for col in self.cols:
            while col and col[-1].is_blank:
                col.pop()
        return dooffice.Section(columns=self.cols)


class Blessings:
    """The absolutions and blessings of Matins, and the rules for them."""

    def __init__(self, fixed, langs, family, sunday_lessons=9):
        self.langs, self.family, self.sunday_lessons = langs, family, sunday_lessons
        self.monastic = family in ("monastic", "cist")
        self.per = 4 if self.monastic else 3  # blessings in a Nocturn of nine or twelve lessons
        two = len(langs.langs) == 2
        self.texts = {}
        for key, sec in _fixed(fixed, "benedictions").items():
            section = _dumped(sec, two)
            self.texts[key] = [[l for l in col if not l.is_blank] for col in section.columns]
        c10 = _fixed(fixed, "c10").get("Benedictio")
        if c10:  # the Office of Our Lady on Saturday: an absolution and three blessings
            section = _dumped(c10, two)
            self.texts["C10"] = [[l for l in col if not l.is_blank] for col in section.columns]

    def __bool__(self):
        return "Absolutiones" in self.texts

    def entry(self, key, i):
        """Entry i of a list, as one Line per column (the Latin where a translation lacks it)."""
        cols = self.texts.get(key)
        if not cols or i >= len(cols[0]):
            return None
        return [(cols[c] if c < len(cols) and i < len(cols[c]) else cols[0])[i]
                for c in range(len(self.langs.langs))]

    # -- building blocks

    def absolution(self, sh, n):
        if self.family == "op":
            return  # the Dominican office says no absolution
        sh.text("Absolutio.", "Absolution.", self.entry("Absolutiones", n - 1))

    def numbered(self, sh, entries, start=1):
        for k, e in enumerate(e for e in entries if e):
            sh.text("%d." % (start + k), "%d." % (start + k), e)

    def nocturn(self, n):
        """The blessings of Nocturn n in an office of nine (twelve) lessons."""
        if n < 3:
            return [self.entry("Nocturn %d" % n, i) for i in range(self.per)]
        first = [self.entry("Evangelica", 0)]
        rest = [self.entry("Nocturn 3", i) for i in ((0, 1, 2) if self.monastic else (1, 2))]
        return first + rest

    def feria(self, wd):
        """A feria of three lessons on weekday wd (1 Monday .. 6 Saturday)."""
        if self.family == "cist":
            return [self.entry("Feria %d 3L" % wd, i) for i in range(3)]
        return [self.entry("Nocturn %d" % day_index(wd), i) for i in range(3)]

    def saint(self):
        return [self.entry("Nocturn 3", 0), self.entry("Nocturn 3", 3), self.entry("Nocturn 3", 2)]

    def homily(self):
        return [self.entry("Evangelica", 0), self.entry("Nocturn 3", 1), self.entry("Nocturn 3", 2)]

    def sunday(self):
        return [self.entry("Nocturn 3", 0), self.entry("Nocturn 3", 1), self.entry("Evangelica9", 0)]

    def summer(self, wd):
        """The one blessing before the monastic short lesson of summer."""
        if self.family == "cist":
            return self.entry("Feria", wd - 1)
        i = day_index(wd)
        return self.entry("Nocturn %d" % i, 3 - (i == 3))

    def our_lady(self, sh, summer=False):
        """The Office of Our Lady on Saturday (Commune C10): its own absolution and blessings.

        Monastic short lesson: the absolution and the last blessing only
        (absolutio_benedictio: $a[0], $a[3]).
        """
        if not self.entry("C10", 0):
            return False
        if self.family != "op":
            sh.text("Absolutio.", "Absolution.", self.entry("C10", 0))
        if summer:
            sh.text("Benedictio.", "Blessing.", self.entry("C10", 3))
        else:
            self.numbered(sh, [self.entry("C10", i) for i in (1, 2, 3)])
        return True

    def cujus(self, sh):
        for idx, la, en in CUJUS:
            if idx == 8 and not self.monastic:
                continue
            sh.text(la + ":", en + ":", self.entry("Nocturn 3", idx))

    # -- the Ordinary: every case, with its rubric

    def guide(self):
        L = self.langs
        out = []
        sh = _Sheet(L)
        sh.heading("De Absolutionibus et Benedictionibus", "The Absolutions and Blessings")
        sh.rubric("Ante lectiones cujusque Nocturni dicitur secreto Pater noster usque ad ℣. Et ne "
                  "nos indúcas in tentatiónem, ℟. Sed líbera nos a malo; deinde Absolutio, ℟. Amen. "
                  "Ante singulas lectiones: ℣. Jube, domne, benedícere, et Benedictio, ℟. Amen.",
                  "Before the lessons of each Nocturn the Our Father is said silently as far as "
                  "℣. And lead us not into temptation, ℟. But deliver us from evil; then the "
                  "Absolution, ℟. Amen. Before each lesson: ℣. Pray, sir, a blessing, and the "
                  "Blessing, ℟. Amen.")
        if self.family == "op":
            sh.rubric("In Ordine Prædicatorum Absolutio non dicitur.",
                      "In the Dominican office no Absolution is said.")
        out.append(sh.section())

        lessons = "XII" if self.monastic else "IX"
        sh = _Sheet(L)
        sh.heading("In officio %s lectionum" % lessons,
                   "In an office of %s lessons" % ("twelve" if self.monastic else "nine"))
        k = 1
        for n in (1, 2, 3):
            sh.rubric("In %s Nocturno:" % ORDINALS[n - 1], "In the %s Nocturn:" % ORDINALS_EN[n - 1])
            self.absolution(sh, n)
            entries = [e for e in self.nocturn(n) if e]
            self.numbered(sh, entries, k)
            k += len(entries)
            sh.gap()
        sh.rubric("In festis Sanctorum, loco benedictionis Divínum auxílium, pro qualitate festi:",
                  "On saints' feasts, instead of the blessing Divinum auxilium, according to the "
                  "feast:")
        self.cujus(sh)
        sh.gap()
        sh.rubric("Si ultima lectio est de Evangelio (homilia alicujus commemorationis), ultima "
                  "benedictio est:",
                  "If the last lesson is from a Gospel (the homily of a commemoration), the last "
                  "blessing is:")
        sh.text("Benedictio.", "Blessing.", self.entry("Evangelica9", 0))
        christmas = [self.entry("Nocturn 3 12-25", i) for i in range(self.per)]
        if any(christmas):
            sh.gap()
            sh.rubric("In Nativitate Domini, in III Nocturno:",
                      "On Christmas Day, in the third Nocturn:")
            self.numbered(sh, christmas, k - len([e for e in self.nocturn(3) if e]))
        out.append(sh.section())

        sh = _Sheet(L)
        sh.heading("In officio III lectionum", "In an office of three lessons")
        if self.family != "op":
            sh.rubric("Absolutio, secundum diem hebdomadæ:",
                      "The Absolution, according to the day of the week:")
            for i, la, en in WEEKDAY_GROUPS:
                sh.text(la + ":", en + ":", self.entry("Absolutiones", i - 1))
            sh.gap()
        sh.rubric("Benedictiones de Feria, secundum diem hebdomadæ:",
                  "The blessings of a feria, according to the day of the week:")
        if self.family == "cist":
            for wd in range(1, 7):
                sh.rubric(WEEKDAYS[wd][0] + ":", WEEKDAYS[wd][1] + ":")
                self.numbered(sh, self.feria(wd))
        else:
            for i, la, en in FERIA_GROUPS:
                sh.rubric(la + ":", en + ":")
                self.numbered(sh, self.feria(i))
        sh.gap()
        sh.rubric("In festis Sanctorum:", "On saints' feasts:")
        self.numbered(sh, self.saint())
        sh.rubric("(Secunda benedictio pro qualitate festi, ut supra.)",
                  "(The second blessing according to the feast, as above.)")
        sh.gap()
        sh.rubric(*HOMILY)
        self.numbered(sh, self.homily())
        if self.entry("C10", 0):
            sh.gap()
            sh.rubric("In officio Sanctæ Mariæ in Sabbato:", "In the Office of Our Lady on Saturday:")
            self.our_lady(sh)
        if self.sunday_lessons == 3:
            sh.gap()
            sh.rubric("In Dominica:", "On Sunday:")
            self.numbered(sh, self.sunday())
        out.append(sh.section())

        if self.monastic:
            sh = _Sheet(L)
            sh.heading("In Lectione brevi, tempore æstivo", "At the short lesson, in summer")
            sh.rubric("Absolutio ut supra, secundum diem hebdomadæ; deinde una Benedictio:",
                      "The Absolution as above, according to the day of the week; then one "
                      "Blessing:")
            if self.family == "cist":
                for wd in range(1, 7):
                    sh.text(WEEKDAYS[wd][0] + ":", WEEKDAYS[wd][1] + ":", self.summer(wd))
            else:
                for i, la, en in FERIA_GROUPS:
                    sh.text(la + ":", en + ":", self.summer(i))
            if self.entry("C10", 0):
                sh.gap()
                sh.rubric("In officio Sanctæ Mariæ in Sabbato:",
                          "In the Office of Our Lady on Saturday:")
                self.our_lady(sh, summer=True)
            out.append(sh.section())
        return out

    # -- the Psalter: the day's own, for every kind of office said on it

    def day(self, wd):
        L = self.langs
        sh = _Sheet(L)
        sh.heading("Absolutio et Benedictiones", "Absolution and Blessings")
        if wd == 0 and self.sunday_lessons == 3:
            self.absolution(sh, 1)
            self.numbered(sh, self.sunday())
            return sh.section()
        if wd == 0:
            k = 1
            for n in (1, 2, 3):
                sh.rubric("In %s Nocturno:" % ORDINALS[n - 1], "In the %s Nocturn:" % ORDINALS_EN[n - 1])
                self.absolution(sh, n)
                entries = [e for e in self.nocturn(n) if e]
                self.numbered(sh, entries, k)
                k += len(entries)
            sh.rubric("In festis Sanctorum et in aliis casibus, ut in Ordinario.",
                      "On saints' feasts and in the other cases, as in the Ordinary.")
            return sh.section()
        i = day_index(wd)
        if self.family != "op":
            sh.rubric("In officio trium lectionum:", "In an office of three lessons:")
            self.absolution(sh, i)
        sh.rubric("De Feria:", "Of the feria:")
        self.numbered(sh, self.feria(wd))
        sh.rubric("De Sancto (secunda pro qualitate festi, ut in Ordinario):",
                  "Of a Saint (the second according to the feast, as in the Ordinary):")
        self.numbered(sh, self.saint())
        sh.rubric("Cum prima lectio est homilia in Evangelium:",
                  "When the first lesson is a homily on the Gospel:")
        self.numbered(sh, self.homily())
        if self.monastic:
            sh.rubric("Tempore æstivo, ad Lectionem brevem:", "In summer, at the short lesson:")
            sh.text("Benedictio.", "Blessing.", self.summer(wd))
        if wd == 6 and self.entry("C10", 0):
            sh.rubric("De Sancta Maria in Sabbato:", "Of Our Lady on Saturday:")
            self.our_lady(sh)
            if self.monastic:
                sh.rubric("Tempore æstivo, ad Lectionem brevem:", "In summer, at the short lesson:")
                self.our_lady(sh, summer=True)
        return sh.section()


def monastic_matins(fixed, langs, wd, ascension=None):
    """The short lesson of the first Nocturn (summer) and the chapters of the
    second, by season, as a monastic psalter gives them: [(label, Section)].

    ascension: the Ascensiontide chapter, as columns of Lines. The engine keeps
    no seasonal one with the others: it takes it from the office of the day
    after the Ascension (TemporaM), and the versions differ (Apoc 5:12 in 1963,
    1 Pet 4:7-8 in 1930) -- so it is read from the rendered sample day.
    """
    two = len(langs.langs) == 2
    texts = _fixed(fixed, "matutinum")
    out = []
    lb = texts.get("MM LB%d" % wd)
    if lb:
        sh = _Sheet(langs)
        sh.heading("Lectio brevis", "Short lesson")
        sh.rubric("Tempore æstivo, in feriis:", "In summer, on weekdays:")
        sh.lines(_dumped(lb, two).columns)
        out.append(("Lectio brevis", sh.section()))
    if "MM LB Pasch" in texts:
        sh = _Sheet(langs)
        sh.heading("Lectio brevis tempore paschali", "Short lesson in Eastertide")
        sh.lines(_dumped(texts["MM LB Pasch"], two).columns)
        out.append(("Lectio brevis tempore paschali", sh.section()))
    if any(key in texts for key, _la, _en in CAPITULA):
        sh = _Sheet(langs)
        sh.heading("Capitula in fine II Nocturni", "The chapters at the end of the second Nocturn")
        for n, (key, la, en) in enumerate(k for k in CAPITULA if k[0] in texts):
            if n:
                sh.gap()
            sh.rubric(la, en)
            sh.lines(_dumped(texts[key], two).columns)
        if ascension:
            sh.gap()
            sh.rubric("Tempore Ascensionis", "At Ascensiontide")
            sh.lines(ascension)
        out.append(("Capitula", sh.section()))
    return out


def canticle_offices(part, canticles, langs):
    """The canticles the Propers and the Common name at the third Nocturn (Ps. 266 ...)."""
    from dobreviary import Office

    two = len(langs.langs) == 2
    secs = []
    for o in (canticles or {}).get("offices", []):
        for sec in o.get("sections", []):
            s = _dumped(sec, two)
            for col in s.columns:
                # The engine numbers psalms as they fall in an hour ("[3]"); here
                # the number the Propers give ("Ps. 266").
                for i, line in enumerate(col[:1]):
                    if line.runs:
                        line.runs[-1].text = re.sub(r"\s*\[\d+\]\s*$", "", line.runs[-1].text)
                        line.runs.append(dooffice.Run(text=" (Ps. %s)" % sec["key"], red=True,
                                                      size="small"))
            secs.append(s)
    if not secs:
        return []
    head = langs.section("Cantica ad III Nocturnum", "The canticles of the third Nocturn",
                         _heading)
    _append(head, langs.lines(
        "Quæ in Proprio et in Communi ad III Nocturnum indicantur, ordine numerorum.",
        "Those named at the third Nocturn in the Propers and the Common, in the order of their "
        "numbers."))
    return [Office(part=part, key="psalter-cantica", title="Cantica", title2="Canticles",
                   hours=[dooffice.Hour(key="Matutinum", heading="", sections=[head] + secs)])]


def paschal_versicles(fixed, langs):
    """The versicles of the three Nocturns in Eastertide on offices of nine lessons
    whose Matins come from the Psalter (specmatins: "Pasch Ant Feria|Dominica")."""
    two = len(langs.langs) == 2
    texts = _fixed(fixed, "psalmi_matutinum")
    out = []
    for key, la, en in (("Pasch Ant Feria", "In festis", "On feasts"),
                        ("Pasch Ant Dominica", "In Dominicis", "On Sundays")):
        if key not in texts:
            continue
        s = _dumped(texts[key], two)
        cols = [[l for l in col if l.text.startswith(("℣", "℟"))] for col in s.columns]
        if not cols or len(cols[0]) < 6:
            continue
        sh = _Sheet(langs)
        sh.heading("Versus Nocturnorum", "The versicles of the Nocturns")
        sh.rubric(la + ", in officio IX lectionum sine antiphonis propriis ad Matutinum (extra "
                  "Octavam Ascensionis):",
                  en + ", in an office of nine lessons without proper antiphons at Matins "
                  "(outside the octave of the Ascension):")
        for n in range(3):
            sh.rubric("In %s Nocturno:" % ORDINALS[n], "In the %s Nocturn:" % ORDINALS_EN[n])
            sh.lines([c[2 * n:2 * n + 2] for c in cols])
        out.append(sh.section())
    return out


def sunday_lessons(renders):
    """How many lessons Sunday Matins has in this version (3 under the 1960 rubrics)."""
    items = _items(renders.week[0], renders.col).get("Matutinum", [])
    n = sum(1 for it in items for _sec, lat in it.parts for l in lat
            if l.text.startswith("Benedictio"))
    return n or 9


# --------------------------------------------------------------------------
# the Ordinary

ANY = ("Ut in Psalterio vel in Proprio.", "As in the Psalter or the Proper.")
ANT = ("Antiphona ut in Psalterio vel in Proprio.", "The antiphon as in the Psalter or the Proper.")
PRAYER = ("Oratio ut in Proprio.", "The prayer as in the Proper.")
MATINS = ("Psalmi, versus et lectiones ut in Psalterio et in Proprio. Ante lectiones dicitur Pater "
          "noster, deinde absolutio et benedictiones, ut infra; post ultimam lectionem, quando "
          "dicendus est, hymnus Te Deum.",
          "The psalms, versicles and lessons as in the Psalter and the Proper. Before the lessons "
          "the Our Father is said, then the absolution and the blessings, as below; after the "
          "last lesson, when it is said, the Te Deum.")
MARTYROLOGY = ("Legitur Martyrologium; deinde dicitur:", "The Martyrology is read; then is said:")
PRECES = ("Quando dicendæ sunt:", "When they are to be said:")
LENT = ("A Septuagesima usque ad Pascha, loco Allelúja, dicitur:",
        "From Septuagesima to Easter, instead of Alleluia, is said:")
MARIAN = (
    ("Advent", "Ab Adventu usque ad Nativitatem Domini", "From Advent to Christmas"),
    ("Nativiti", "A Nativitate Domini usque ad Purificationem",
     "From Christmas to the Purification"),
    ("Quadragesimae", "A Purificatione usque ad Pascha", "From the Purification to Easter"),
    ("Paschalis", "Tempore Paschali", "In Eastertide"),
    ("Postpentecost", "A festo Sanctissimæ Trinitatis usque ad Adventum",
     "From Trinity Sunday to Advent"),
)


def _fixed(fixed, key):
    for o in (fixed or {}).get("offices", []):
        if o["key"] == key:
            return {s["key"]: s for s in o.get("sections", [])}
    return {}


def _dumped(sec, two):
    import dobreviary
    return dobreviary._lines(sec["l1"], sec["l2"], two)


def _headed(sec, la, en, langs, make=_heading):
    for i, col in enumerate(sec.columns):
        col.insert(0, make(langs.pick(la, en, i)))
    return sec


def ordinary_offices(part, renders, fixed, langs, family):
    from dobreviary import Office

    two = len(langs.langs) == 2
    base = _items(renders.week[1], renders.col)  # a Monday through the year
    extra = [_items(renders.week[0], renders.col)]  # Sunday: the Sunday preces
    lent = None
    for se, day, lat in renders.seasons:
        if se.key == "adv":
            extra.append(day_items(day, lat, renders.col))  # a Wednesday: the ferial preces
        if se.key == "quad":
            lent = day_items(day, lat, renders.col)
    prayers = _fixed(fixed, "prayers")
    marian = _fixed(fixed, "mariaant")

    offices = []
    for hkey, (la, en) in HOUR_NAMES.items():
        secs = []
        for it in base.get(hkey, []):
            if it.kind == "omitted":
                # Not said on a Monday through the year: take it from a day it is said.
                it = next((x for e in extra for x in e.get(hkey, [])
                           if x.label == it.label and x.kind != "omitted" and x.parts), None)
                if it is None:
                    continue
                head = _strip_notes(it.parts[0][0])
                _append(head, langs.lines(*PRECES), after_heading=True)
                secs += [head] + [_strip_notes(s) for s, _ in it.parts[1:]]
                continue
            if not it.label:
                continue
            if it.label.startswith("Regula"):
                # Monastic Prime: the day's portion of the Rule, which a breviary
                # does not print; its blessing does not change.
                head = _heading_of(it)
                first = it.parts[0][0]
                keep = []
                for col in first.columns:
                    n = next((i for i, l in enumerate(col) if l.text.startswith(("℟. Amen", "R. Amen"))), 0)
                    keep.append([_copy_line(l) for l in col[1:n + 1]])
                if head:
                    _append(head, langs.lines(
                        "Legitur Regula S. P. N. Benedicti, pars quæ die illa assignatur; vel "
                        "Lectio brevis, ut in Psalterio.",
                        "The day's portion of the Rule of our holy Father Benedict is read; or "
                        "the short lesson, as in the Psalter."))
                    _append(head, keep)
                    secs.append(head)
                continue
            if it.label.startswith("Martyrologium"):
                head = _heading_of(it)
                if head:
                    secs.append(_append(head, langs.lines(*MARTYROLOGY)))
                secs += [_strip_notes(s) for s, _ in it.parts[1:]]
                continue
            if it.label.startswith("Antiphona finalis") and family in ("roman", "monastic") and marian:
                head = _heading_of(it)
                if head:
                    secs.append(head)
                for key, mla, men in MARIAN:
                    if key in marian:
                        secs.append(_headed(_dumped(marian[key], two), mla, men, langs, make=_rubric))
                # What follows whichever antiphon is said -- ℣. Divinum auxilium
                # (with the monastic rubric before it), then in the older books
                # the Pater, Ave and Credo said silently and the rubric for when
                # Matins follow -- is the same all year: kept from the engine's.
                at = next(((n, i) for n, (_sec, lat) in enumerate(it.parts)
                           for i, l in enumerate(lat) if "ivínum auxílium" in l.text), None)
                if at:
                    n, i = at
                    lat = it.parts[n][1]
                    if i and lat[i - 1].text.startswith("Si "):
                        i -= 1  # "Si descendendum sit a choro, concluditur dicendo:"
                    back = len(lat) - i
                    first = it.parts[n][0]
                    secs.append(dooffice.Section(columns=[[_copy_line(l) for l in c[-back:]]
                                                          for c in first.columns]))
                    secs += [_strip_notes(sec) for sec, _ in it.parts[n + 1:]]
                continue
            if it.kind in ("psalter", "proper"):
                head = _heading_of(it)
                if head is None:
                    continue
                if it.label.startswith(("Invitatorium", "Canticum")):
                    # The psalm or canticle is the same every day; only its antiphon varies.
                    # (A Sunday's invitatory: Monday's antiphon is the psalm's own
                    # first verse, so there the psalm starts at the second.)
                    if it.label.startswith("Invitatorium"):
                        it = next((x for x in extra[0].get(hkey, [])
                                   if x.label.startswith("Invitatorium") and x.parts), it)
                    secs.append(_append(head, langs.lines(*ANT)))
                    body = [_without_antiphons(s) for s, _ in it.parts]
                    if body:  # its heading is already printed above
                        body[0].columns = [c[1:] if c and c[0].is_heading else c
                                           for c in body[0].columns]
                    secs += body
                elif it.label.startswith("Psalmi cum lectionibus"):
                    secs.append(_append(head, langs.lines(*MATINS)))
                    secs += [_strip_notes(s) for s, lat in it.parts
                             if "Pater" in "".join(l.text for l in lat) and _matins_kind(lat) == "lesson"
                             and "Absolutio" in "".join(l.text for l in lat)]
                    guide = Blessings(fixed, langs, family, sunday_lessons(renders))
                    if guide:
                        secs += guide.guide()
                    if "Te Deum" in prayers:
                        secs.append(_headed(_dumped(prayers["Te Deum"], two), "Te Deum",
                                            "Te Deum", langs))
                    if "Te decet" in prayers and family in ("monastic", "cist"):
                        te_decet = _headed(_dumped(prayers["Te decet"], two), "Te decet laus",
                                           "Te decet laus", langs)
                        _append(te_decet, langs.lines(
                            "In Dominicis et festis, post Te Deum, legitur Evangelium: ℣. "
                            "Dominus vobiscum. ℟. Et cum spiritu tuo. Sequentia sancti Evangelii "
                            "secundum N. ℟. Gloria tibi, Domine. Lecto Evangelio, ℟. Amen, et "
                            "dicitur:",
                            "On Sundays and feasts, after the Te Deum, the Gospel is read: ℣. The "
                            "Lord be with you. ℟. And with thy spirit. The continuation of the "
                            "holy Gospel according to N. ℟. Glory be to thee, O Lord. After the "
                            "Gospel, ℟. Amen, and is said:"),
                            after_heading=True)
                        secs.append(te_decet)
                elif it.label.startswith("Oratio"):
                    secs.append(_append(head, langs.lines(*PRAYER)))
                else:
                    secs.append(_append(head, langs.lines(*ANY)))
                continue
            if hkey == "Matutinum" and it.label == "Capitulum" and family in ("monastic", "cist"):
                # The chapter closing the second Nocturn changes with the season:
                # the Psalter gives them all.
                head = _heading_of(it)
                if head:
                    secs.append(_append(head, langs.lines("Pro tempore, ut in Psalterio.",
                                                          "According to the season, as in the "
                                                          "Psalter.")))
                continue
            # Said alike every day: in full.
            parts = [_strip_notes(s) for s, _ in it.parts]
            if it.label == "Incipit" and lent and parts:
                seen = [{l.text for s, _ in it.parts for l in (s.columns[i] if i < len(s.columns) else [])}
                        for i in range(len(langs.langs))]
                other = next((x for x in lent.get(hkey, []) if x.label == "Incipit"), None)
                if other:
                    new = [[_copy_line(l) for s, _ in other.parts for l in
                            (s.columns[i] if i < len(s.columns) else []) if l.text not in seen[i]
                            and not l.is_blank] for i in range(len(langs.langs))]
                    if any(new):
                        _append(parts[-1], [[_rubric(langs.pick(*LENT, i))] + new[i]
                                            for i in range(len(langs.langs))])
            secs += parts
        if secs:
            offices.append(Office(part=part, key="ordo-" + hkey, title=la, title2=en,
                                  hours=[dooffice.Hour(key=hkey, heading="", sections=secs)]))
    litany = _litany(renders, langs)
    if litany:
        offices.append(Office(part=part, key="ordo-litaniae", title="Litaniæ Sanctorum",
                              title2="The Litany of the Saints",
                              hours=[dooffice.Hour(key="Laudes", heading="", sections=litany)]))
    return offices


def _litany(renders, langs):
    """The Litany of the Saints, as the engine says it after Lauds on its days."""
    best = None
    for pair in renders.litany:
        for it in _items(pair, renders.col).get("Laudes", []):
            if it.label.startswith("Conclusio"):
                n = sum(len(sec.columns[0]) for sec, _ in it.parts if sec.columns)
                if n > 60 and (best is None or n > best[0]):
                    best = (n, it)
    if best is None:
        return []
    it = best[1]
    secs = [_strip_notes(sec) for sec, _ in it.parts]
    secs[0].columns = [c[1:] if c and c[0].is_heading else c for c in secs[0].columns]
    head = langs.section("Litaniæ Sanctorum", "The Litany of the Saints", _heading)
    _append(head, langs.lines(
        "In Litaniis majoribus (die 25 Aprilis) et minoribus (in feriis Rogationum), quando "
        "dicuntur, post Laudes, dicto ℣. Benedicamus Domino:",
        "On the Greater Litanies (25 April) and the Lesser (the Rogation days), when they are "
        "said: after Lauds, when ℣. Let us bless the Lord has been said:"))
    return [head] + secs


# --------------------------------------------------------------------------
# the Common of the Seasons

# What the Common of the Seasons never takes: the day's own prayer, the
# martyrology, the Rule, the preces. (An Incipit comes in only where a season
# changes it -- the Dominicans' versicle before Lauds in Eastertide.)
_SKIP = re.compile(r"^(Conclusio|Oratio|Martyrologium|De Officio Capituli|Preces|"
                   r"Commemoratio|Regula)")


def dump_texts(dump):
    """Fingerprints of every line of a dumped part (and the files it borrows from)."""
    out = set()
    for o in (dump or {}).get("offices", []) + (dump or {}).get("referenced", []):
        for sec in o.get("sections", []):
            out.update(html_fps(sec.get("l1")))
    return out


def section_texts(offices):
    """Fingerprints of every line the given offices print (their first column)."""
    return {f for o in offices for h in o.hours for s in h.sections if s.columns
            for f in _fps(s.columns[0])}


def known_texts(renders, tempora=None, fixed=None, extra_sections=()):
    """Fingerprints of what the other parts already hold."""
    known = set()
    for day, _lat in renders.week + [renders.winter]:
        for h in day.hours:
            for s in h.sections:
                if s.columns:
                    known.update(_fps(s.columns[0]))
    for sec in extra_sections:
        if sec.columns:
            known.update(_fps(sec.columns[0]))
    known |= dump_texts(tempora)
    # Of the fixed texts, only those the Ordinary prints: the monastic chapters
    # and short lessons are in the book only through a monastic Psalter (which
    # comes in with extra_sections) -- a Roman book shares their versicles
    # (Veni, Domine, et noli tardare) without printing them.
    printed = {"offices": [o for o in (fixed or {}).get("offices", [])
                           if o["key"] in ("prayers", "benedictions", "mariaant")]}
    known |= dump_texts(printed)
    return known


def _last_verse(col):
    """The last stanza of a hymn, as lines (up to the blank line before it)."""
    lines = list(col)
    while lines and lines[-1].is_blank:
        lines.pop()
    start = max((i for i, l in enumerate(lines) if l.is_blank), default=-1) + 1
    return lines[start:]


def _opening(col):
    return next((_fp(l.text) for l in col if not (l.is_blank or l.is_heading) and _fp(l.text)), None)


def _seasonal_ending(item, year_items):
    """For a hymn also said through the year: its last verse if the season changes
    it (a Section), False if it does not; None if it is a hymn of its own."""
    mine = item.parts[0][0]
    year = next((x for x in year_items if x.label == item.label and x.parts), None)
    if year is None or _opening(mine.columns[0]) != _opening(year.parts[0][0].columns[0]):
        return None
    if _fps(_last_verse(mine.columns[0])) == _fps(_last_verse(year.parts[0][0].columns[0])):
        return False
    return dooffice.Section(columns=[[_copy_line(l) for l in _last_verse(c)]
                                     for c in mine.columns])


def _hymn_ending(ending, hymns, langs):
    """The season's last verse, and the hymns it ends."""
    first = []  # the opening line of each hymn, in the first column
    for h in hymns:
        col = [l for l in h.columns[0] if not l.is_blank and not l.is_heading]
        if col and col[0].text.rstrip(",;:. ") not in first:
            first.append(col[0].text.rstrip(",;:. "))
    names = ", ".join(first)
    sh = _Sheet(langs)
    sh.heading("Conclusio hymnorum", "The ending of the hymns")
    sh.rubric("Hoc tempore in hymnis %s, et aliis ejusdem metri, ultima stropha dicitur:" % names,
              "In this season, in the hymns %s, and others of the same metre, the last verse "
              "is:" % names)
    sec = sh.section()
    for i, col in enumerate(sec.columns):
        col.extend(_copy_line(l) for l in ending.columns[min(i, len(ending.columns) - 1)])
    return sec


def _day_list(days, langs, i):
    """{1, 4} -> "Feria II et Feria V" (or "Monday and Thursday")."""
    english = dooffice.family(langs.langs[i]) == "English"
    names = [WEEKDAYS[d][1 if english else 0] for d in sorted(days)]
    joiner = " and " if english else " et "
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + joiner + names[-1]


def _headed_copy(item, section):
    """A copy of a section, opening with its item's heading."""
    red = _strip_notes(section)
    if red.columns and red.columns[0] and not red.columns[0][0].is_heading:
        head = _heading_of(item)
        for i, col in enumerate(red.columns):
            if head and i < len(head.columns):
                col[0:0] = [_copy_line(l) for l in head.columns[i]]
    return red


def _season_day(items, year, known, langs, endings, versicles=frozenset()):
    """What one day of a season says that the book does not already hold:
    {hour: [Section]}; changed hymn endings are gathered into `endings`.
    versicles: signatures of Nocturn versicles given for earlier seasons."""
    out = {}
    for hkey in HOUR_NAMES:
        secs = []
        for it in items.get(hkey, []):
            if it.kind in ("proper", "omitted") or not it.label or _SKIP.match(it.label):
                continue
            if it.label.startswith(("Canticum", "Invitatorium")):
                ant = _antiphon_of(it)
                only = [l for l in ant.columns[0] if _is_ant(l)] if ant else []
                if ant and _is_new(ant, known, only):
                    secs.append(ant)
                continue
            if it.label.startswith("Psalmi"):
                # The psalms are the Psalter's; a season changes only their antiphons.
                psalms = [(sec, l) for sec, l in it.parts if _matins_kind(l) == "psalm"]
                ants = _antiphon_list(it, psalms)
                only = [l for l in ants.columns[0] if _is_ant(l)] if ants.columns else []
                if psalms and _is_new(ants, known, only):
                    secs.append(ants)
                # The versicles of the Nocturns: the season's where they are not
                # the same weekday's through the year -- even where the text is
                # printed elsewhere (1960, after the Ascension, the feast's own,
                # by weekday), which the book would otherwise never say.
                ordinary = {_signature([_strip_notes(sec)])
                            for x in year.get(hkey, []) if x.label.startswith("Psalmi")
                            for sec, l in x.parts if _matins_kind(l) == "other"}
                for sec, l in it.parts:
                    if _matins_kind(l) != "other":
                        continue
                    red = _strip_notes(sec)
                    if not (red.columns and red.columns[0]) or _signature([red]) in ordinary:
                        continue
                    if not _fps(red.columns[0]) or _signature([red]) in versicles:
                        continue  # already given for an earlier season
                    if not red.columns[0][0].is_heading:
                        _headed(red, "Versus", "Versicle", langs)
                    secs.append(red)
                continue
            if it.label.startswith("Hymnus") and it.parts:
                ending = _seasonal_ending(it, year.get(hkey, []))
                if ending is not None:
                    if ending:
                        red = _strip_notes(it.parts[0][0])
                        endings.setdefault(_signature([ending]), (ending, []))[1].append(red)
                    continue  # the year's own hymn: in the Ordinary or the Psalter
            # What the engine gives "from the Psalter, by season" belongs here
            # wherever it is not the same weekday's through the year -- even if
            # the text is printed elsewhere (the monastic chapter at Terce in
            # Eastertide is Easter's own), unless an earlier season gave it.
            seasonal = "secundum tempora" in it.note
            year_sigs = {_signature([_headed_copy(x, sec)]) for x in year.get(hkey, [])
                         if x.label == it.label for sec, _l in x.parts} if seasonal else set()
            for sec, _l in it.parts:
                red = _headed_copy(it, sec)
                fps = _fps(red.columns[0]) if red.columns else []
                sig = _signature([red])
                if sig in versicles:
                    continue  # given for an earlier season
                # The section as said in the season, whole -- even for one
                # changed line (Qui scandis super sidera at Prime).
                if any(f not in known for f in fps) or (seasonal and fps and sig not in year_sigs):
                    secs.append(red)
        if secs:
            out[hkey] = secs
    return out


def season_offices(part, renders, known, langs, extra=None):
    """extra: {season key: {hour: [Section]}} built from data, added to what the
    sampled days give (the Eastertide versicles of festal Nocturns)."""
    from dobreviary import Office

    offices, versicles = [], set()
    for se, day, lat in renders.seasons:
        days = renders.weeks.get(se.key) or [(se.date, day, lat)]
        endings = {}  # the changed last verse of hymns: signature -> (verse, [hymns])
        found, order = {}, {}  # (hour, signature) -> [Section, {weekdays}]; hour -> [key]
        # A Saturday kept as the Office of Our Lady is no day of the season.
        days = [d for d in days if not re.search(r"Mari(æ|ae) Sabbato", d[1].title or "")] or days
        for date, d_day, d_lat in days:
            wd = (date.weekday() + 1) % 7
            # The same weekday through the year: a hymn said then too, with
            # another ending, is the same hymn with the season's doxology.
            year = week_items(renders, wd)
            news = _season_day(day_items(d_day, d_lat, renders.col), year, known, langs, endings,
                               versicles)
            for hkey, secs in news.items():
                for sec in secs:
                    key = (hkey, _signature([sec]))
                    if key not in found:
                        found[key] = [sec, set()]
                        order.setdefault(hkey, []).append(key)
                    found[key][1].add(wd)
        sampled = {(d.weekday() + 1) % 7 for d, _x, _y in days}
        weekdays = sampled - {0}
        hours, printed = [], []
        for hkey, (hla, _hen) in HOUR_NAMES.items():
            keys = order.get(hkey, [])
            # Said the whole week (or every weekday) first, then what only some
            # days say, each under the days it belongs to.
            every = [k for k in keys if found[k][1] >= weekdays]
            some = [k for k in keys if k not in every]
            secs = [found[k][0] for k in every]
            for k in some:
                sec, on = found[k]
                copy = dooffice.Section(columns=[list(c) for c in sec.columns])
                _append(copy, [[_rubric(_day_list(on, langs, i) + ":")]
                               for i in range(len(langs.langs))], after_heading=True)
                secs.append(copy)
            secs += (extra or {}).get(se.key, {}).get(hkey, [])
            if secs:
                hours.append(dooffice.Hour(key=hkey, heading=hla, sections=secs))
                printed += secs
        # What this season printed is not printed again for a later one.
        for (hkey, sig), (sec, _on) in found.items():
            versicles.add(sig)
            if sec.columns and sec.columns[0] and sec.columns[0][0].text.startswith(
                    ("Versus", "Versicle")):
                versicles.add(_signature([dooffice.Section(columns=[c[1:] for c in sec.columns])]))
        if endings:
            secs = [_hymn_ending(ending, hymns, langs) for ending, hymns in endings.values()]
            hours.append(dooffice.Hour(key="Hymni", heading="De hymnis", sections=secs))
            printed += secs
        for sec in printed:
            known.update(_fps(sec.columns[0]))
        if hours:
            offices.append(Office(part=part, key="season-" + se.key, title=se.la, title2=se.en,
                                  hours=hours))
    return offices


# --------------------------------------------------------------------------
# the Calendar


def roman_date(month, day, year=2027):
    """1 January -> "Kal.", 2 January -> "IV Non.", 14 January -> "XIX Kal." ..."""
    from dobreviary import roman

    nones = 7 if month in (3, 5, 7, 10) else 5
    ides = nones + 8
    if day == 1:
        return "Kal."
    if day < nones:
        n = nones - day + 1
        return "Prid. Non." if n == 2 else "%s Non." % roman(n)
    if day == nones:
        return "Non."
    if day < ides:
        n = ides - day + 1
        return "Prid. Id." if n == 2 else "%s Id." % roman(n)
    if day == ides:
        return "Id."
    n = calendar.monthrange(year, month)[1] - day + 2
    return "Prid. Kal." if n == 2 else "%s Kal." % roman(n)


def dominical_letter(month, day, year=2027):
    """The calendar letter of a day (A for 1 January), counted in a common year."""
    try:
        doy = dt.date(year, month, day).timetuple().tm_yday
    except ValueError:
        return ""
    return "Abcdefg"[(doy - 1) % 7]


def calendar_offices(part, sancti, two):
    from dobreviary import Office

    months = {m: [] for m in range(1, 13)}
    for o in sancti.get("offices", []):
        if not o.get("date"):
            continue
        mm, dd = (int(x) for x in o["date"].split("-"))
        t1, t2 = (o.get("title") or [o["key"], o["key"]])[:2]
        rank = "Comm." if o.get("commemoratio") else re.sub(
            r"\s+", " ", o.get("rankname") or o.get("rank") or "").strip()
        known = (mm, dd) != (2, 29)
        months[mm].append((str(dd), roman_date(mm, dd) if known else "", dominical_letter(mm, dd),
                           t1 or o["key"], (t2 if two and t2 != t1 else ""), rank))
    offices = []
    for m in range(1, 13):
        offices.append(Office(part=part, key="kal-%02d" % m, title=MONTHS_LA[m - 1],
                              title2=dopdf.MONTHS[m - 1], rows=months[m]))
    return offices


# --------------------------------------------------------------------------
# the prayers before and after the Office


PRAYERS = (
    ("Ante", "Ante Divinum Officium", "Before the Divine Office"),
    ("Pater noster", "Pater noster", "The Lord's Prayer"),
    ("Ave Maria", "Ave Maria", "The Hail Mary"),
    ("Credo", "Symbolum Apostolorum", "The Apostles' Creed"),
    ("Post", "Post Divinum Officium", "After the Divine Office"),
)


def prayer_offices(part, fixed, two):
    """Aperi, Domine; the Our Father, the Hail Mary and the Creed; Sacrosanctæ."""
    from dobreviary import Office

    texts = _fixed(fixed, "prayers")
    return [Office(part=part, key="prayer-" + key.lower().replace(" ", "-"), title=la, title2=en,
                   hours=[dooffice.Hour(key="", heading="", sections=[_dumped(texts[key], two)])])
            for key, la, en in PRAYERS if key in texts]


def render_count(lang1, lang2):
    """How many offices render() asks the engine for."""
    n = len(_plan()[1])
    latin = lang1.startswith("Latin") or (lang2.startswith("Latin") and lang2 != lang1)
    return n if latin else 2 * n
