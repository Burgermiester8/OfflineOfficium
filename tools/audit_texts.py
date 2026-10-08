"""Audit the texts of the breviary's variable parts, version by version.

    python tools/audit_texts.py                         # all fifteen versions
    python tools/audit_texts.py --version "Monastic - 1963" --details

Reads what the breviary prints of the Proper of the Season, the Common of the
Seasons, the Proper of the Saints and the Common of the Saints, Latin beside
English, through the builder itself (dobreviary.gather_text and gather, the
English the data lacks already filled from translations-en.json) -- no
typesetting, so a version takes under a minute. It looks for:

  latin in english   a line of the English column that is still Latin: the
                     same as a line of the Latin column beside it, or Latin
                     by its words (a line too short for a key is skipped)
  english in latin   a line of the Latin column that is English by its words
  reference          a reference the engine left as it is ("@Sancti/12-25:...")
  engine message     what the engine prints where it finds no text
                     ("Psalm not found", "Commune/C1a:Lectio8 is missing!")
  other alphabet     a Cyrillic, Arabic or other letter among the Latin ones
                     (they look alike on the page, and break the lookups)
  lesson length      a lesson whose English is more than 2.2 times the
                     Latin's length or less than 0.45 of it (the Latin at
                     least 300 letters): one column holding a whole lesson,
                     the other a part of it

Exit status 1 if anything but lesson lengths is found: some of those are
wordier translations.
"""
import argparse
import collections
import concurrent.futures
import os
import re
import sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

PARTS = ["tempora", "temporis", "sancti", "commune"]
KINDS = ("latin in english", "english in latin", "reference", "engine message", "other alphabet", "lesson length")

_LA = set("et est non ad cum qui quae quod sed ut ab de ex per pro super sicut eius ejus eum ei nos vos tu te "
          "tibi mihi ego enim autem ergo quia nobis vobis sunt erat esse hoc haec ille ipse dominus domine deus "
          "dei deo domini dominum nostri nostrum nostra tuam tuae tua tuo tuis suis sua suo atque nec neque iam "
          "jam etiam quoque inter post ante sine omnes omnia quam quoniam quid cuius cujus cui ubi tunc nunc "
          "semper secundum sancti sancte sanctus beati beatus".split())
_EN = set("the and of to that is was he his him it they them their this with for not be shall have hath "
          "which who unto our we you ye thou thee thy thine my me lord god from by as all upon are were but "
          "so when then there what an".split())
_SAME = {"alleluia", "alleluja", "amen"}  # the English is the Latin
_REF = re.compile(r"@[A-Za-z]+/[\w ./-]*:?")
_ENGINE = re.compile(r"^Psalm\S* not found$|^\S+/\S+.* missing!?$|^\S+ (?:Ant|Versus|Versum) \d+ missing$"
                     r"| is missing!?$|not found!$")
_ALPHABET = re.compile(r"[Ͱ-ϿЀ-ӿ֐-׿؀-ۿࠀ-ࣿ�]")


def _words(text):
    t = unicodedata.normalize("NFD", text.lower().replace("æ", "ae").replace("œ", "oe"))
    return re.findall(r"[a-z]+", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def is_latin(text):
    w = _words(text)
    if len(w) < 3:
        return False
    la = sum(x in _LA for x in w) + sum(x.endswith(("orum", "arum", "ibus", "atur", "itur", "erunt")) for x in w)
    en = sum(x in _EN for x in w) + sum(x.endswith(("eth", "ing")) for x in w)
    return la > en and la >= 2 and en < 3


_EN_ONLY = _EN - {"an", "me", "as", "so", "to", "be", "my"}  # Latin words too


def is_english(text):
    w = _words(text)
    en = sum(x in _EN_ONLY for x in w)
    la = sum(x in _LA for x in w)
    return len(w) >= 4 and en >= 3 and en > 2 * la


def _plain(lines):
    return " ".join(l.text for l in lines).strip()


def audit(version):
    """{kind: [(where, text)]} for one version."""
    import dobreviary
    import dopdf
    import dotranslate
    web, perl = dopdf._default_paths()
    libs = dopdf._default_libs(perl)
    with tempfile.TemporaryDirectory(prefix="audit-texts-") as work:
        dumps, renders = dobreviary.gather_text(PARTS, version, "Latin", "English", perl,
                                                dobreviary._default_dumper(), web, work,
                                                perl_libs=libs)
        offices = dobreviary.gather(PARTS, version, "Latin", "English", dumps, renders)
    found = collections.defaultdict(list)
    for o in offices:
        for h in o.hours:
            for s in h.sections:
                if len(s.columns) != 2:
                    continue
                la, en = s.columns
                head = next((l.text.strip() for l in la if l.text.strip()), "")[:40]
                where = "%s/%s | %s | %s" % (o.part.key, o.key, h.key, head)
                la_keys = {dotranslate.key(l.text) for l in la} - {None}
                for l in en:
                    k = dotranslate.key(l.text)
                    body = dotranslate._SUFFIX.sub("", dotranslate.split_prefix(l.text)[1])
                    if k and not set(_words(body)) <= _SAME and (k in la_keys or is_latin(body)):
                        found["latin in english"].append((where, l.text.strip()))
                for l in la:
                    if dotranslate.key(l.text) and is_english(l.text):
                        found["english in latin"].append((where, l.text.strip()))
                for col in (la, en):
                    for l in col:
                        # (NFC: the Greek question mark some files type for a semicolon is one)
                        t = unicodedata.normalize("NFC", l.text.strip())
                        if _REF.search(t):
                            found["reference"].append((where, t))
                        if _ENGINE.search(t):
                            found["engine message"].append((where, t))
                        if _ALPHABET.search(t):
                            found["other alphabet"].append((where, t))
                lt, et = _plain(la), _plain(en)
                if re.match(r"(Lectio|Lesson)\b", head) and len(lt) >= 300 and et:
                    r = len(et) / len(lt)
                    if r > 2.2 or r < 0.45:
                        found["lesson length"].append((where, "English %.2f times the Latin" % r))
    return dict(found)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="append", help="a version (default: all)")
    ap.add_argument("--details", action="store_true", help="list what is found, not only count it")
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.version:
        versions = args.version
    else:
        import dooffice
        import dopdf
        versions = [c.value for c in dooffice.read_choices(dopdf._default_paths()[0])["versions"]]
    results = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(audit, v): v for v in versions}
        for fut in concurrent.futures.as_completed(futs):
            results[futs[fut]] = fut.result()
    width = max(len(v) for v in versions)
    print("%-*s  %s" % (width, "version", "  ".join(KINDS)))
    for v in versions:
        print("%-*s  %s" % (width, v, "  ".join("%*d" % (len(k), len(results[v].get(k, [])))
                                                  for k in KINDS)))
    if args.details:
        for kind in KINDS:
            seen = collections.OrderedDict()
            for v in versions:
                for where, text in results[v].get(kind, []):
                    seen.setdefault((where, text), []).append(v)
            if seen:
                print("\n%s: %d" % (kind, len(seen)))
                for (where, text), vs in seen.items():
                    print("   %-60s %s  [%d versions]" % (where[:60], text[:110], len(vs)))
    bad = any(results[v].get(k) for v in versions for k in KINDS if k != "lesson length")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
