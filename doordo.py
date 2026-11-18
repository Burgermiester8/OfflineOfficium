"""
The Ordo: what the Office of each day of one year is, and where it stands in
this book.

The days come from the engine itself (engine_dump.pl --part ordo, which
follows the engine's own Ordo page, kalendar/ordo.pl): the office that wins
the day, its rank and colour, its commemorations, and how Vespers concur.
Nothing here decides any of that. What the book adds is the page: each
office and commemoration is looked up among the offices this book (or this
volume) prints, by the engine's file, and given that office's page.

Each month is an office of the part (a table, like the Calendar's months),
set by markup().
"""

import datetime as dt
import re

import dopdf

MONTHS_LA = ("Januarius", "Februarius", "Martius", "Aprilis", "Majus", "Junius", "Julius",
             "Augustus", "September", "October", "November", "December")
DAYS_LA = ("Dom.", "F. II", "F. III", "F. IV", "F. V", "F. VI", "Sabb.")
DAYS_EN = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
# The engine's colours (dopsalter.LITURGICAL_COLOURS: black for white, grey for black).
COLOURS = {"black": "white", "blue": "white", "grey": "black", "purple": "violet",
           "red": "red", "green": "green", "rose": "rose"}
# The Vespers notes the engine gives (concurrence), in English.
VESPERS_EN = (
    (r"^Vespera de sequenti", "Vespers of the following"),
    (r"^Vespera de pr(?:æ|ae)cedenti", "Vespers of the preceding"),
    (r"^A capitulo de sequenti", "From the chapter of the following"),
    (r"^Vespera de Officio occurente", "Vespers of the occurring office"),
    (r"^Vespera de Tempore occurente", "Vespers of the season"),
    (r"nihil de pr(?:æ|ae)cedenti", "nothing of the preceding"),
    (r"nihil de sequenti", "nothing of the following"),
    (r"commemoratio de sequenti Dominica", "commemoration of the following Sunday"),
    (r"commemoratio de sequenti", "commemoration of the following"),
    (r"commemoratio de pr(?:æ|ae)cedenti", "commemoration of the preceding"),
    (r"commemoratio de off\. priv\. tantum", "commemoration of the privileged office only"),
    (r"Commemoratio Sanctorum crastinorum tantum", "commemoration of tomorrow's saints only"),
)
COMM_EN = (("Commemoratio ad Laudes tantum", "Commemoration at Lauds only"),
           ("Commemoratio ad Laudes", "Commemoration at Lauds"),
           ("Commemoratio", "Commemoration"), ("Scriptura", "Scripture"))


# The days of the season in English, for the Ordo's English column: the engine
# names most of them in Latin in its English files too. Only these forms; a
# title that does not match one wholly gets no English (never a wrong one).
_ORD = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9,
        "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17,
        "XVIII": 18, "XIX": 19, "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24,
        "Prima": 1, "Secunda": 2, "Tertia": 3, "Quarta": 4, "Quinta": 5, "Sexta": 6}
_FERIA = {"II": "Monday", "III": "Tuesday", "IV": "Wednesday", "V": "Thursday", "VI": "Friday",
          "secunda": "Monday", "tertia": "Tuesday", "quarta": "Wednesday", "quinta": "Thursday",
          "sexta": "Friday"}
_SEASON = (  # (Latin, English: "the week" form, "the Sunday" form)
    (r"Adventus", "of Advent"), (r"post Epiphaniam", "after Epiphany"),
    (r"in Quadragesima", "of Lent"), (r"Passionis", "of Passiontide"),
    (r"post Octavam Pasch(?:æ|ae)", "after the Octave of Easter"), (r"Post Pascha", "after Easter"),
    (r"post Octavam Pentecostes", "after the Octave of Pentecost"),
    (r"Post Pentecosten", "after Pentecost"), (r"post Ascensionem", "after the Ascension"),
    (r"Nativitatis", "after Christmas"))
_FIXED = {
    "Dominica Resurrectionis": "Easter Sunday", "Dominica Pentecostes": "Pentecost Sunday",
    "Dominica Sanctissimæ Trinitatis": "Trinity Sunday", "Dominica in Palmis": "Palm Sunday",
    "Dominica II Passionis seu in Palmis": "Palm Sunday", "Dominica de Passione": "Passion Sunday",
    "Dominica in Albis in Octava Paschæ": "Low Sunday", "Dominica in Septuagesima": "Septuagesima Sunday",
    "Dominica in Sexagesima": "Sexagesima Sunday", "Dominica in Quinquagesima": "Quinquagesima Sunday",
    "Dominica post Ascensionem": "Sunday after the Ascension",
    "Dominica Secunda Nativitatis": "Second Sunday after Christmas",
    "Feria IV Cinerum": "Ash Wednesday", "Feria Quarta Cinerum": "Ash Wednesday",
    "Feria V post Cineres": "Thursday after Ash Wednesday",
    "Feria VI post Cineres": "Friday after Ash Wednesday",
    "Sabbato post Cineres": "Saturday after Ash Wednesday",
    "Feria Secunda Hebdomadæ Sanctæ": "Monday of Holy Week",
    "Feria Tertia Hebdomadæ Sanctæ": "Tuesday of Holy Week",
    "Feria Quarta Hebdomadæ Sanctæ": "Wednesday of Holy Week",
    "Feria Quinta in Cœna Domini": "Maundy Thursday", "Feria V in Cena Domini": "Maundy Thursday",
    "Feria Sexta in Passione et Morte Domini": "Good Friday", "Sabbato Sancto": "Holy Saturday",
    "Sabbato in Albis": "Saturday in Easter Week", "In Ascensione Domini": "The Ascension of the Lord",
    "In Vigilia Ascensionis": "Vigil of the Ascension", "In Vigilia Pentecostes": "Vigil of Pentecost",
    "Sabbato in Vigilia Pentecostes": "Vigil of Pentecost",
    "Festum Sanctissimi Corporis Christi": "Corpus Christi",
    "Feria V post Ascensionem": "Thursday after the Ascension",
    "Feria Secunda in Rogationibus": "Rogation Monday", "Feria Tertia in Rogationibus": "Rogation Tuesday",
    "Feria Quarta in Rogationibus": "Rogation Wednesday",
    "Die Octavæ Nativitatis Domini": "Octave Day of Christmas",
}
_EMBER = {"Adventus": "of Advent", "Quadragesimæ": "of Lent", "Pentecostes": "of Pentecost",
          "Septembris": "of September"}
_MONTHS_GEN = {"Januarii": "January", "Februarii": "February", "Martii": "March", "Aprilis": "April",
               "Maii": "May", "Junii": "June", "Julii": "July", "Augusti": "August",
               "Septembris": "September", "Octobris": "October", "Novembris": "November",
               "Decembris": "December"}
_OCTAVES = {"Nativitatis": "Christmas", "Paschæ": "Easter", "Pentecostes": "Pentecost",
            "Epiphaniæ": "the Epiphany", "Ascensionis": "the Ascension"}


def _nth(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def season_english(title):
    """"Feria III infra Hebdomadam II post Epiphaniam" -> "Tuesday of the 2nd
    week after Epiphany"; "" for a title of any other form."""
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"\s+[IVX]+\.\s+(?:%s)$" % "|".join(_MONTHS_GEN), "", t)  # a monastic week of Scripture
    if t in _FIXED:
        return _FIXED[t]
    season = "|".join("(?:%s)" % la for la, _en in _SEASON)

    def where(text):
        for la, en in _SEASON:
            if re.fullmatch(la, text, re.I):
                return en
        return None

    m = re.fullmatch(r"Dominica (\w+) (%s)" % season, t, re.I)
    if m and m.group(1) in _ORD and where(m.group(2)):
        return "%s Sunday %s" % (_nth(_ORD[m.group(1)]), where(m.group(2)))
    m = re.fullmatch(r"(Feria (\w+)|Sabbato) infra Hebdomadam (?:(\w+) )?(%s)" % season, t, re.I)
    if m:
        day = "Saturday" if m.group(1) == "Sabbato" else _FERIA.get(m.group(2)) or _FERIA.get(
            (m.group(2) or "").lower())
        place = where(m.group(4))
        if day and place:
            if m.group(3) is None:
                return "%s %s" % (day, place.replace("of ", "in ", 1) if place.startswith("of") else
                                  "of the week " + place)
            if m.group(3) in _ORD:
                return "%s of the %s week %s" % (day, _nth(_ORD[m.group(3)]), place)
    m = re.fullmatch(r"(Feria (\w+)|Sabbato) Quattuor Temporum (\w+)", t, re.I)
    if m and m.group(3) in _EMBER:
        day = "Saturday" if m.group(1) == "Sabbato" else _FERIA.get(m.group(2)) or _FERIA.get(m.group(2).lower())
        if day:
            return "Ember %s %s" % (day, _EMBER[m.group(3)])
    m = re.fullmatch(r"Die (\w+) infra [Oo]ctavam (\w+)", t)
    if m and m.group(2) in _OCTAVES and m.group(1) in _ORD:
        return "%s day in the Octave of %s" % (_nth(_ORD[m.group(1)]), _OCTAVES[m.group(2)])
    m = re.fullmatch(r"Feria (\w+) infra [Oo]ctavam (\w+)", t)
    if m and m.group(2) in _OCTAVES and (_FERIA.get(m.group(1)) or _FERIA.get(m.group(1).lower())):
        return "%s in the Octave of %s" % (_FERIA.get(m.group(1)) or _FERIA[m.group(1).lower()],
                                           _OCTAVES[m.group(2)])
    m = re.fullmatch(r"Die (\w+) (%s)" % "|".join(_MONTHS_GEN), t)
    if m and m.group(1) in _ORD:
        return "%s %s" % (_MONTHS_GEN[m.group(2)], _nth(_ORD[m.group(1)]))
    return ""


def default_year(today=None):
    """The year an Ordo is wanted for: from November, the next."""
    today = today or dt.date.today()
    return today.year + 1 if today.month >= 11 else today.year


def norm(path):
    """An engine file as the book names it (dobreviary._norm): SanctiM/01-01 -> Sancti/01-01."""
    return re.sub(r"^(Sancti|Tempora|Commune)(?:M|OP|Cist)?/", r"\1/", path or "")


def _vespers_en(text):
    parts = [p.strip() for p in re.split(r";", text) if p.strip()]
    out = []
    for p in parts:
        for pat, en in VESPERS_EN:
            m = re.search(pat, p, re.I)
            if m:
                rest = p[m.end():].strip(" .")
                out.append(en + ((" " + rest) if rest else ""))
                break
        else:
            out.append(p)
    return "; ".join(out) + ("." if text.rstrip().endswith(".") else "")


def _comm_kind(kind, english):
    kind = re.sub(r"\s+", " ", kind or "Commemoratio").strip()
    if not english:
        return kind
    for la, en in COMM_EN:
        if kind.lower() == la.lower():
            return en
    return kind


def _rank(rank, latin, monastic):
    m = re.search(r"\b(IV|III|II|I)\.? classis\b", rank or "")
    if not m:
        return re.sub(r"\s+", " ", rank or "").strip()
    if m.group(1) == "IV" and monastic:
        return "Memoria" if latin else "Memorial"
    return "%s classis" % m.group(1) if latin else "%s Class" % m.group(1)


def ordo_offices(part, dump, lang1, lang2, monastic, office_cls):
    """The months of the year's Ordo, as offices of `part`: office.table is
    ("ordo", {"year", "month", "days": [day dicts]})."""
    import dooffice

    if not dump:
        return []
    o = (dump.get("offices") or [{}])[0]
    year = o.get("year")
    english = (dooffice.family(lang1) == "English", dooffice.family(lang2) == "English")
    two = lang1 != lang2
    months = {m: [] for m in range(1, 13)}
    for d in o.get("days", []):
        if d.get("error") or not d.get("date"):
            continue
        y, m, day = (int(x) for x in d["date"].split("-"))
        la, en = (d.get("title") or ["", ""])[:2]
        if not en or en == la:
            en = season_english(la) or ""
        t1, t2 = ((en or la), la) if english[0] else (la, en if english[1] else la)
        r1, r2 = (d.get("rank") or ["", ""])[:2]
        saint = norm(d.get("file")).startswith("Sancti/")
        vesp = d.get("vespers") or ""
        months[m].append({
            "day": day, "dow": int(d.get("dow", dt.date(y, m, day).isoweekday() % 7)),
            "title": (t1, t2 if two and t2 != t1 else ""),
            "rank": _rank(r1, not english[0], monastic and saint),  # a feria's IV is no Memorial
            "colour": COLOURS.get(d.get("color") or "", ""),
            "file": norm(d.get("file")),
            "comms": [{"kind": (_comm_kind(c.get("kind"), english[0]),
                                _comm_kind(c.get("kind"), english[1])),
                       "title": tuple((c.get("title") or ["", ""])[::-1] if english[0]
                                      else (c.get("title") or ["", ""])),
                       "file": norm(c.get("file"))}
                      for c in d.get("comms", [])],
            "vespers": (_vespers_en(vesp) if english[0] else vesp,
                        (_vespers_en(vesp) if english[1] else vesp) if two else ""),
            "anchor": "", "anchors": [],
        })
    offices = []
    for m in range(1, 13):
        if not months[m]:
            continue
        la = "%s %d" % (MONTHS_LA[m - 1], year)
        en = "%s %d" % (dopdf.MONTHS[m - 1], year)
        offices.append(office_cls(part=part, key="ordo-%02d" % m, title=en if english[0] else la,
                                  title2=la if english[0] else en,
                                  table=("ordo", {"year": year, "month": m, "days": months[m]})))
    return offices


def _base(file):
    """A file not printed under its own name: a week's day without an office of
    its own is read from the week's Sunday (Pent23-5 -> Pent23-0); a variant
    (Nat2-0r, 01-14cc) from its plain file."""
    out = []
    m = re.match(r"^(Tempora/[A-Za-z]+\d+)-\d", file)
    if m:
        out.append(m.group(1) + "-0")
    plain = re.sub(r"(?<=\d)[a-z]+$", "", file)
    if plain != file:
        out.append(plain)
    return out


def anchor_ordo(offices, label):
    """Each day's office and commemorations with the page of its office in
    this book or volume (label(office) gives that office's label): by the
    engine's file, else a variant of it, else (a commemoration the engine
    names without its file) by the title among the Proper of the Saints."""
    printed, by_title = {}, {}
    for o in offices:
        if o.untitled or o.rows is not None or o.table is not None or o.include:
            continue
        for f in (o.file,) + tuple(o.files or ()):
            if f:
                printed.setdefault(norm(f), o)
        if o.part.key == "sancti":
            for t in (o.title, o.title2):
                by_title.setdefault(re.sub(r"\W+", "", (t or "").lower()), o)

    def find(file, titles=()):
        for f in [file] + _base(file) if file else []:
            if f in printed:
                return label(printed[f])
        for t in titles:
            o = by_title.get(re.sub(r"\W+", "", (t or "").lower()))
            if o:
                return label(o)
        return ""

    for o in offices:
        if not (o.table and o.table[0] == "ordo"):
            continue
        for d in o.table[1]["days"]:
            d["anchor"] = find(d["file"])
            d["anchors"] = [find(c["file"], c["title"]) for c in d["comms"]]
            # A saint the engine has no English title for: the book's own.
            mine = printed.get(d["file"])
            if mine is not None and d["title"][1] == "" and mine.title2 and mine.title2 != mine.title \
                    and mine.title == d["title"][0]:
                d["title"] = (d["title"][0], mine.title2)


def markup(office, writer):
    """The Typst for a month of the Ordo, after its heading: a row a day -- its
    day and weekday, the office (then its commemorations and the Vespers, in
    smaller type) in each language, its rank, colour and page."""
    q = dopdf.q
    import dooffice

    meta, layout = writer.meta, writer.layout
    two = meta.lang2 != meta.lang1
    english = (dooffice.family(meta.lang1) == "English", dooffice.family(meta.lang2) == "English")
    names = writer.colour_names()

    def colour(key):
        fill, la, en = names[key]
        return "litcol(%s, %s)" % ('rgb("%s")' % fill if layout.red_in_print else "none",
                                   q(en if english[0] else la))

    def cell(d, c):
        """The office in column c, its commemorations and the Vespers below it."""
        lines = ["text(%s)" % q(d["title"][c])] if c == 0 else ['text(style: "italic", %s)' % q(d["title"][c])]
        for comm, anchor in zip(d["comms"], d["anchors"]):
            t = comm["title"][c] or comm["title"][0]
            # Its page after it, in the first column (the day's own is the row's last).
            page = (" #" + writer.page_ref(anchor, c, "", bare=True)) if anchor and c == 0 else ""
            lines.append('text(size: 0.84em)[#text(style: "italic", %s) #%s%s]'
                         % (q(comm["kind"][c] + ":"), q(t), page))
        if d["vespers"][c]:
            lines.append('text(size: 0.8em, fill: luma(70), style: "italic", %s)' % q(d["vespers"][c]))
        return "stack(dir: ttb, spacing: 0.25em, %s)" % ", ".join(lines)

    cells = []
    for d in office.table[1]["days"]:
        dow = (DAYS_EN if english[0] else DAYS_LA)[d["dow"]]
        row = [q(str(d["day"])),
               "text(size: 0.84em, %s%s)" % ("fill: rub, " if d["dow"] == 0 else "", q(dow)), "[]",
               cell(d, 0)]
        if two:
            row.append(cell(d, 1) if d["title"][1] or d["vespers"][1] or d["comms"] else "[]")
        row.append("text(size: 0.84em, hyphenate: false, %s)" % q(d["rank"]))
        row.append(colour(d["colour"]) if d["colour"] in names else "[]")
        row.append(writer.page_ref(d["anchor"], 0, bare=True) if d["anchor"] else "[]")
        cells.append(", ".join(row))
    out = ["#[", "#set par(justify: false)"]
    if cells:
        out.append("#caltable(%s,\n  %s)" % ("true" if two else "false", ",\n  ".join(cells)))
    out.append("]")
    return "\n".join(out)
