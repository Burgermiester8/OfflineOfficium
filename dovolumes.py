"""
A breviary in volumes, divided by the liturgical year.

Each volume holds the Calendar, the Ordinary, the Psalter and the Common
entire, and of the Proper of the Season and the Proper of the Saints what
falls in its part of the year. Where that part begins on a movable day (the
first Sunday of Lent moves with Easter), a fixed feast near it falls in one
volume in some years and in the other in others: printed breviaries give it in
both, and so does this. So do the Sundays after Pentecost against a volume
that begins on the first Sunday of a month, and the Sundays after Epiphany
that, in years with more than twenty-four Sundays after Pentecost, are said
again in November.

Which volume holds an office is found by walking the days of every kind of
liturgical year, naming each day's office as the engine names it (Date.pm:
getweek, monthday), and noting the volume the day falls in. A year's offices
depend only on the weekday it begins on, whether it is a leap year and the
date of Easter: seventy kinds, each found among the Gregorian years. The four volumes
of the Roman Breviary come out as printed: winter from Advent, spring from the
first Sunday of Lent, summer from Trinity Sunday, autumn from the first Sunday
of September, the saints of 26-27 November to 12 March in the first, and so on.
"""

import datetime as dt
import functools
import re

D = dt.timedelta


def easter(year):
    """Easter Sunday (Gregorian), as Date.pm geteaster."""
    g, c = year % 19, year // 100
    h = (c - c // 4 - (8 * c + 13) // 25 + 19 * g + 15) % 30
    i = h - (h // 28) * (1 - (h // 28) * (29 // (h + 1)) * ((21 - g) // 11))
    j = (year + year // 4 + i + 2 - c + c // 4) % 7
    lv = i - j
    month = 3 + (lv + 40) // 44
    return dt.date(year, month, lv + 28 - 31 * (month // 4))


def _dow(d):
    """0 for Sunday, as the engine counts."""
    return (d.weekday() + 1) % 7


def advent(year):
    """The first Sunday of Advent in this civil year."""
    christmas = dt.date(year, 12, 25)
    return christmas - D(days=(_dow(christmas) or 7) + 21)


def first_sunday(year, month, modern):
    """The first Sunday of a month, as the engine reckons it for the weeks of
    August to November: the Sunday nearest the first, or (1960) the first
    Sunday in the month."""
    first = dt.date(year, month, 1)
    dow = _dow(first)
    sunday = first - D(days=dow)
    if dow >= 4 or (dow and modern):
        sunday += D(days=7)
    return sunday


def week(d):
    """The engine's week for a day (Date.pm getweek): Adv1, Nat, Epi3, Quadp1,
    Quad6, Pasc7, Pent05, Pent24, or Epi4 for a Sunday after Epiphany said
    again in November."""
    y = d.year
    adv = advent(y)
    if d >= adv:
        if d < dt.date(y, 12, 25):
            return "Adv%d" % (1 + (d - adv).days // 7)
        return "Nat"
    jan6 = dt.date(y, 1, 6)
    ordtime = jan6 + D(days=7 - _dow(jan6))
    if d.month == 1 and d < ordtime:
        return "Nat"
    e = easter(y)
    if d < e - D(days=63):
        return "Epi%d" % ((d - ordtime).days // 7 + 1)
    for n, back in ((1, 56), (2, 49), (3, 42)):
        if d < e - D(days=back):
            return "Quadp%d" % n
    if d < e:
        return "Quad%d" % (1 + (d - (e - D(days=42))).days // 7)
    if d < e + D(days=56):
        return "Pasc%d" % ((d - e).days // 7)
    n = (d - (e + D(days=49))).days // 7
    if n < 23:
        return "Pent%02d" % n
    wdist = ((adv - d).days + 6) // 7
    if wdist < 2:
        return "Pent24"
    if n == 23:
        return "Pent23"
    return "Epi%d" % (8 - wdist)


def month_week(d, modern):
    """The engine's week of the months August to November (Date.pm monthday):
    "081-0" is the first Sunday of August; None outside them."""
    if d.month < 7:
        return None
    firsts, lit_month = [], 0
    for m in range(8, 13):
        firsts.append(first_sunday(d.year, m, modern))
        if d >= firsts[-1]:
            lit_month = m
        else:
            break
    if not lit_month:
        return None
    adv = advent(d.year)
    if lit_month > 10 and d >= adv:
        return None
    wk = (d - firsts[lit_month - 8]).days // 7
    if lit_month == 10 and modern and wk >= 2 and firsts[2].day >= 4:
        wk += 1  # 1960: the third week of October vanishes in some years
    if lit_month == 11 and (wk > 0 or modern):
        wk = 4 - ((adv - d).days - 1) // 7  # the second week of November mostly vanishes
        if modern and wk == 1:
            wk = 0
    return "%02d%d-%d" % (lit_month, wk + 1, _dow(d))


def tempora_keys(d, modern):
    """The keys of the Proper of the Season's offices said on a day."""
    w = week(d)
    keys = []
    if w == "Nat":
        if _dow(d) == 0:  # the Sundays of Christmastide: within the octave, after it
            keys.append("Nat1-0" if d.month == 12 else "Nat2-0")
    else:
        keys.append("%s-%d" % (w, _dow(d)))
    mw = month_week(d, modern)
    if mw:
        keys.append(mw)
    return keys


# Where a volume may begin: key -> (Latin "a ...", Latin "ad ...", English,
# the day in the liturgical year that ends in `year`).
def _first_sunday_seam(month):
    return lambda year, modern: first_sunday(year, month, modern)


SEAMS = {
    "advent": ("Adventu", "Adventum", "Advent", lambda y, m: advent(y - 1)),
    "christmas": ("Nativitate Domini", "Nativitatem Domini", "Christmas",
                  lambda y, m: dt.date(y - 1, 12, 25)),
    "epiphany": ("Epiphania Domini", "Epiphaniam Domini", "the Epiphany",
                 lambda y, m: dt.date(y, 1, 6)),
    "septuagesima": ("Dominica in Septuagesima", "Dominicam in Septuagesima", "Septuagesima Sunday",
                     lambda y, m: easter(y) - D(days=63)),
    "lent": ("Dominica I Quadragesimæ", "Dominicam I Quadragesimæ", "the first Sunday of Lent",
             lambda y, m: easter(y) - D(days=42)),
    "passion": ("Dominica de Passione", "Dominicam de Passione", "Passion Sunday",
                lambda y, m: easter(y) - D(days=14)),
    "easter": ("Dominica Resurrectionis", "Dominicam Resurrectionis", "Easter Sunday",
               lambda y, m: easter(y)),
    "pentecost": ("Dominica Pentecostes", "Dominicam Pentecostes", "Pentecost",
                  lambda y, m: easter(y) + D(days=49)),
    "trinity": ("Dominica Sanctissimæ Trinitatis", "Dominicam Sanctissimæ Trinitatis",
                "Trinity Sunday", lambda y, m: easter(y) + D(days=56)),
    "august": ("Dominica I Augusti", "Dominicam I Augusti", "the first Sunday of August",
               _first_sunday_seam(8)),
    "september": ("Dominica I Septembris", "Dominicam I Septembris", "the first Sunday of September",
                  _first_sunday_seam(9)),
    "october": ("Dominica I Octobris", "Dominicam I Octobris", "the first Sunday of October",
                _first_sunday_seam(10)),
    "november": ("Dominica I Novembris", "Dominicam I Novembris", "the first Sunday of November",
                 _first_sunday_seam(11)),
}
SEAM_ORDER = list(SEAMS)
# The window's short names for them (Advent always begins the first volume).
SHORT_NAMES = {"christmas": "Christmas", "epiphany": "Epiphany", "septuagesima": "Septuagesima",
               "lent": "Lent I", "passion": "Passion Sunday", "easter": "Easter",
               "pentecost": "Pentecost", "trinity": "Trinity Sunday", "august": "August",
               "september": "September", "october": "October", "november": "November"}

# The divisions offered for each number of volumes (where the second, third
# ... begin). Four is the Roman Breviary's, which is also the most even; two
# and three are the most even (measured on a Monastic 1963 book: the Propers
# of each volume within 7% for two, 17% for three, 8% for four).
DIVISIONS = {2: ["trinity"], 3: ["easter", "september"], 4: ["lent", "trinity", "september"]}
# Names of the four seasons' volumes, when the division is the Roman one.
SEASON_NAMES = (("Pars Hiemalis", "Winter"), ("Pars Verna", "Spring"),
                ("Pars Æstiva", "Summer"), ("Pars Autumnalis", "Autumn"))
ORDINALS = (("Pars Prima", "Part One"), ("Pars Secunda", "Part Two"),
            ("Pars Tertia", "Part Three"), ("Pars Quarta", "Part Four"),
            ("Pars Quinta", "Part Five"), ("Pars Sexta", "Part Six"))
ROMANS = ("I", "II", "III", "IV", "V", "VI")

# The Common of the Seasons: each season's days (from, to) in a liturgical year.
SEASON_SPANS = {
    "adv": lambda y: (advent(y - 1), dt.date(y - 1, 12, 24)),
    "quadp": lambda y: (easter(y) - D(days=63), easter(y) - D(days=47)),
    "quad": lambda y: (easter(y) - D(days=46), easter(y) - D(days=15)),
    "quad5": lambda y: (easter(y) - D(days=14), easter(y) - D(days=1)),
    "pasch": lambda y: (easter(y), easter(y) + D(days=38)),
    "asc": lambda y: (easter(y) + D(days=39), easter(y) + D(days=48)),
}

@functools.lru_cache(maxsize=1)
def years():
    """One year of each kind (first weekday, leap year, Easter): seventy."""
    kinds = {}
    for y in range(1584, 5584):
        leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
        e = easter(y)
        kinds.setdefault((dt.date(y, 1, 1).weekday(), leap, e.month, e.day), y)
    return sorted(kinds.values())


@functools.lru_cache(maxsize=2)
def _days(modern):
    """For each year of years(): [(day, its tempora keys, "MM-DD")], from Advent
    of the year before to the eve of this year's Advent."""
    out = []
    for y in years():
        d, end, days = advent(y - 1), advent(y), []
        while d < end:
            days.append((d, tempora_keys(d, modern), d.strftime("%m-%d")))
            d += D(days=1)
        out.append((y, days))
    return out


def check(seams):
    """None if these seams (where the second, third ... volumes begin) are in
    the year's order every year, else a message."""
    unknown = [s for s in seams if s not in SEAMS]
    if unknown:
        return "Unknown dividing point: %s" % ", ".join(unknown)
    if len(set(seams)) != len(seams):
        return "Each volume must begin at a different point."
    for modern in (False, True):
        for y in years():
            days = [SEAMS["advent"][3](y, modern)] + [SEAMS[s][3](y, modern) for s in seams]
            for i in range(1, len(days)):
                if days[i] <= days[i - 1]:
                    before = "Advent" if i == 1 else SEAMS[seams[i - 2]][2]
                    return ("The volumes must begin in the order of the year: %s can come "
                            "before %s." % (SEAMS[seams[i - 1]][2], before))
    return None


@functools.lru_cache(maxsize=32)
def membership(seams, modern):
    """For seams (a tuple of SEAMS keys where the second ... volumes begin):
    ({tempora key: {volume indexes}}, {"MM-DD": {...}}, {season key: {...}}).
    Volumes count from 0."""
    tempora, sancti, seasons = {}, {}, {}
    for y, days in _days(modern):
        starts = [SEAMS["advent"][3](y, modern)] + [SEAMS[s][3](y, modern) for s in seams]
        end = advent(y)
        v = 0
        for d, keys, mmdd in days:
            while v + 1 < len(starts) and d >= starts[v + 1]:
                v += 1
            for k in keys:
                tempora.setdefault(k, set()).add(v)
            sancti.setdefault(mmdd, set()).add(v)
        for key, span in SEASON_SPANS.items():
            a, b = span(y)
            for i, s in enumerate(starts):
                nxt = starts[i + 1] if i + 1 < len(starts) else end
                if a < nxt and b >= s:
                    seasons.setdefault(key, set()).add(i)
    return tempora, sancti, seasons


def volumes_of(office, seams, modern, fallback):
    """The volumes (indexes) holding an office of the book, or `fallback` for
    one that has no day (a part every volume holds gets them all)."""
    tempora, sancti, seasons = membership(tuple(seams), modern)
    key, part = office.key, office.part.key
    if part == "missa":
        # A Sunday's Mass goes where its Sunday does; a feast kept on a
        # Sunday (Christ the King), by the days it can take, as a saint.
        day = getattr(office, "day", "")
        if day:
            return sancti.get(day) or fallback
        part = "tempora"
    if part == "tempora":
        # The second week of November never comes under the 1960 rubrics, but
        # its office is in the data: where the older reckoning puts it.
        return tempora.get(key) or membership(tuple(seams), not modern)[0].get(key) or fallback
    if part == "sancti":
        m = re.match(r"(\d\d-\d\d)", getattr(office, "day", "") or key)
        return (sancti.get(m.group(1)) if m else None) or fallback
    if part == "temporis":
        return seasons.get(key.replace("season-", "")) or set(range(len(seams) + 1))
    return set(range(len(seams) + 1))


def saints_order(seams, i, modern):
    """{"MM-DD": place} for volume i: its saints in the order of its time, from
    the first day it can begin (the Winter Part's from late November)."""
    _tempora, sancti, _seasons = membership(tuple(seams), modern)
    days = [(dt.date(2000, 1, 1) + D(days=k)).strftime("%m-%d") for k in range(366)]
    inside = [i in sancti.get(d, ()) for d in days]
    start = next((k for k in range(366) if inside[k] and not inside[k - 1]), 0)
    return {d: (k - start) % 366 for k, d in enumerate(days)}


def names(seams):
    """[(Latin name, English name, Latin span, English span)] of each volume."""
    n = len(seams) + 1
    bounds = ["advent"] + list(seams) + ["advent"]
    out = []
    for i in range(n):
        a, b = SEAMS[bounds[i]], SEAMS[bounds[i + 1]]
        if list(seams) == DIVISIONS[4] and n == 4:
            la, en = SEASON_NAMES[i][0], "The %s Part" % SEASON_NAMES[i][1]
        else:
            la, en = ORDINALS[i]
        prep = "Ab" if a[0][0] in "AEIOUÆ" else "A"
        out.append((la, en, "%s %s ad %s" % (prep, a[0], b[1]),
                    "From %s to %s" % (a[2], b[2])))
    return out


def file_suffix(seams, i):
    """" - I. Pars Hiemalis" for volume i, for the file's name."""
    return " - %s. %s" % (ROMANS[i], names(seams)[i][0])
