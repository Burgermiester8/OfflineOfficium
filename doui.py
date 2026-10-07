"""
The app's look: soft and rounded, in the manner of the Wii's menus -- white
cards on a pale ground, pill-shaped buttons that light up blue under the
pointer -- with a light and a dark mode.

Tk has no rounded widgets, so the shapes are drawn here, pixel by pixel with
soft edges, into small PNG images (zlib and binascii only: the app's bundle
has no Pillow drawing), and the themed (ttk) widgets are given layouts made of
them; each image is stretched from its middle, so one serves every size. The
plain Tk widgets the theme cannot reach (windows, the log, canvases) are
recoloured by a walk over the windows when the mode changes, and on Windows
the title bar follows the mode too.

    install(window)   -- once per window: the theme, its colours, the title bar
    toggle(), set_mode("dark"), mode(), colours()
    mode_button(parent) -- a button that switches the mode, labelled for it
    role(widget, "log" | "paper" | "preview" | "swatch") -- for plain Tk widgets

The choice is kept in ui-settings.json beside the app's other settings; until
one is made, the mode is Windows' own (Settings > Personalisation > Colours).
"""

import binascii
import json
import math
import os
import zlib
import tkinter as tk
from tkinter import ttk

SETTINGS_FILE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                             "DivinumOfficium", "ui-settings.json")
FONT = "Segoe UI"
SYMBOLS = "Segoe UI Symbol"

# The Wii's blue, its pale greys and white; and the same at night.
PALETTES = {
    "light": {
        "bg": "#e7ebf0", "card": "#ffffff", "card_border": "#d6dce3", "shadow": 0.10,
        "text": "#3a414a", "muted": "#7a8592", "heading": "#5d6874",
        "accent": "#2db3e8", "accent_hover": "#4cc3f0", "accent_press": "#1d9cd1",
        "accent_text": "#ffffff", "glow": "#9fdcf5",
        "button_top": "#ffffff", "button_bottom": "#f0f3f6", "button_border": "#c8d0d9",
        "hover_top": "#f5fcff", "hover_bottom": "#e4f5fd", "press": "#d3eefa",
        "field": "#ffffff", "field_border": "#cbd3dc",
        "disabled_fill": "#f1f3f5", "disabled_border": "#e1e5e9", "disabled_text": "#a9b2bc",
        "trough": "#e2e7ec", "thumb": "#b9c2cc", "select": "#cdeefc",
        "error": "#c62828", "warn": "#a35f00", "ok": "#2e7d32",
        "canvas": "#c5ccd4", "log": "#f7f9fb",
    },
    "dark": {
        "bg": "#14161a", "card": "#22262c", "card_border": "#30363d", "shadow": 0.35,
        "text": "#e3e7eb", "muted": "#97a1ac", "heading": "#aab4be",
        "accent": "#2db3e8", "accent_hover": "#4cc3f0", "accent_press": "#1d9cd1",
        "accent_text": "#ffffff", "glow": "#1f6f91",
        "button_top": "#30353c", "button_bottom": "#292d33", "button_border": "#40464f",
        "hover_top": "#2b3a44", "hover_bottom": "#25333c", "press": "#1d3e4d",
        "field": "#1a1d21", "field_border": "#3a4048",
        "disabled_fill": "#24282d", "disabled_border": "#2e3339", "disabled_text": "#5f6873",
        "trough": "#2a2e34", "thumb": "#4a515a", "select": "#1d4b5e",
        "error": "#ef6461", "warn": "#f0b04f", "ok": "#6cc070",
        "canvas": "#0d0f11", "log": "#1a1d21",
    },
}

_state = {"mode": None, "themes": {}, "listeners": []}


# --------------------------------------------------------------------------
# the mode


def system_mode():
    """Windows' own app mode: "dark" or "light"."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except (OSError, ImportError):
        return "light"


def saved_mode():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            m = json.load(fh).get("mode")
        if m in PALETTES:
            return m
    except (OSError, ValueError, AttributeError):
        pass
    return system_mode()


def setting(key, default=None):
    """One of the settings kept beside the mode (the update check's, say)."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            return json.load(fh).get(key, default)
    except (OSError, ValueError, AttributeError):
        return default


def save_setting(key, value):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data[key] = value
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except OSError:
        pass


def _save_mode(m):
    save_setting("mode", m)


def mode():
    return _state["mode"] or saved_mode()


def colours():
    return PALETTES[mode()]


def on_change(callback):
    """callback(mode) after every change of mode."""
    _state["listeners"].append(callback)


# --------------------------------------------------------------------------
# drawing: shapes with soft edges, into PNG images


def _rgb(hexcolour):
    h = hexcolour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class _Image:
    """An RGBA image painted in layers; each layer a coverage and a colour."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [[0.0, 0.0, 0.0, 0.0] for _ in range(w * h)]  # straight r, g, b (0-255), a (0-1)

    def paint(self, coverage, colour, alpha=1.0, colour_bottom=None):
        top = _rgb(colour)
        bottom = _rgb(colour_bottom) if colour_bottom else top
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            src = tuple(top[i] + (bottom[i] - top[i]) * t for i in range(3))
            for x in range(self.w):
                a = coverage(x + 0.5, y + 0.5) * alpha
                if a <= 0:
                    continue
                p = self.px[y * self.w + x]
                out_a = a + p[3] * (1 - a)
                for i in range(3):
                    p[i] = (src[i] * a + p[i] * p[3] * (1 - a)) / out_a if out_a else 0
                p[3] = out_a

    def png(self):
        rows = bytearray()
        for y in range(self.h):
            rows.append(0)
            for x in range(self.w):
                r, g, b, a = self.px[y * self.w + x]
                rows += bytes((int(r + 0.5), int(g + 0.5), int(b + 0.5), int(a * 255 + 0.5)))

        def chunk(kind, data):
            return (len(data).to_bytes(4, "big") + kind + data
                    + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big"))

        head = self.w.to_bytes(4, "big") + self.h.to_bytes(4, "big") + bytes((8, 6, 0, 0, 0))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
                + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def _rounded(x0, y0, x1, y1, r):
    """Coverage of a rounded rectangle (a signed distance, softened over a pixel)."""
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    r = max(0.0, min(r, hw, hh))

    def cov(px, py):
        qx, qy = abs(px - cx) - (hw - r), abs(py - cy) - (hh - r)
        d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
        return min(1.0, max(0.0, 0.5 - d))

    return cov


def _soft(x0, y0, x1, y1, r, blur):
    """A shadow: the shape's coverage blurred outward over `blur` pixels."""
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2

    def cov(px, py):
        qx, qy = abs(px - cx) - (hw - r), abs(py - cy) - (hh - r)
        d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
        return min(1.0, max(0.0, (blur - d) / (2 * blur))) ** 2

    return cov


def _stroke(points, width):
    """Coverage of a polyline drawn `width` wide, with round ends."""
    segs = list(zip(points, points[1:]))

    def cov(px, py):
        best = 1e9
        for (ax, ay), (bx, by) in segs:
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
        return min(1.0, max(0.0, width / 2 + 0.5 - best))

    return cov


def _photo(png):
    return tk.PhotoImage(data=binascii.b2a_base64(png, newline=False).decode("ascii"), format="png")


def _pill(w, h, r, fill, border, bw=1.0, bottom=None, shadow=0.0, glow=None, pad=0):
    """A rounded shape with a border, and an optional soft shadow under it or a
    glow around it (both drawn into `pad` pixels of margin)."""
    im = _Image(w, h)
    x0, y0, x1, y1 = pad, pad, w - pad, h - pad
    if glow:
        im.paint(_soft(x0 - 0.5, y0 - 0.5, x1 + 0.5, y1 + 0.5, r + 0.5, pad), glow, 0.9)
    if shadow:
        im.paint(_soft(x0, y0 + 1.5, x1, y1 + 1.5, r, max(1.5, pad)), "#000000", shadow)
    im.paint(_rounded(x0, y0, x1, y1, r), border)
    im.paint(_rounded(x0 + bw, y0 + bw, x1 - bw, y1 - bw, r - bw), fill, colour_bottom=bottom)
    return im


# --------------------------------------------------------------------------
# the themes


def _images(c):
    """{name: PNG bytes} for one palette."""
    out = {}
    # Buttons: pills, white (or grey at night) and blue when they are the one to press.
    b = dict(w=40, h=32, r=14, pad=2)
    out["button"] = _pill(fill=c["button_top"], bottom=c["button_bottom"], border=c["button_border"],
                          shadow=c["shadow"] * 0.6, **b)
    out["button-hover"] = _pill(fill=c["hover_top"], bottom=c["hover_bottom"], border=c["accent"],
                                bw=1.6, glow=c["glow"], **b)
    out["button-press"] = _pill(fill=c["press"], border=c["accent"], bw=1.6, **b)
    out["button-disabled"] = _pill(fill=c["disabled_fill"], border=c["disabled_border"], **b)
    out["accent"] = _pill(fill=c["accent_hover"], bottom=c["accent"], border=c["accent_press"],
                          shadow=c["shadow"] * 0.8, **b)
    out["accent-hover"] = _pill(fill=c["accent_hover"], bottom=c["accent_hover"], border=c["accent"],
                                glow=c["glow"], **b)
    out["accent-press"] = _pill(fill=c["accent_press"], border=c["accent_press"], **b)
    out["accent-disabled"] = _pill(fill=c["disabled_fill"], border=c["disabled_border"], **b)
    # Small buttons (the arrows that order the parts, the preview's zoom): lower pills.
    sb = dict(w=28, h=24, r=10, pad=1)
    out["small"] = _pill(fill=c["button_top"], bottom=c["button_bottom"], border=c["button_border"], **sb)
    out["small-hover"] = _pill(fill=c["hover_top"], bottom=c["hover_bottom"], border=c["accent"],
                               bw=1.5, glow=c["glow"], **sb)
    out["small-press"] = _pill(fill=c["press"], border=c["accent"], bw=1.5, **sb)
    out["small-disabled"] = _pill(fill=c["disabled_fill"], border=c["disabled_border"], **sb)
    # The big buttons of the main window: rounded tiles, as the Wii's channels.
    t = dict(w=64, h=64, r=18, pad=3)
    # (flat: Tk repeats an image's middle rather than stretching it, and a
    # gradient there would show a seam across a tall tile)
    out["tile"] = _pill(fill=c["button_top"], border=c["button_border"], shadow=c["shadow"], **t)
    out["tile-hover"] = _pill(fill=c["hover_top"], border=c["accent"], bw=2.0, glow=c["glow"], **t)
    out["tile-press"] = _pill(fill=c["press"], border=c["accent"], bw=2.0, **t)
    out["tile-disabled"] = _pill(fill=c["disabled_fill"], border=c["disabled_border"], **t)
    # Cards: the white panels that hold each group of settings.
    out["card"] = _pill(56, 56, 16, c["card"], c["card_border"], shadow=c["shadow"], pad=3)
    # Fields: entries, drop-down lists and number boxes.
    f = dict(w=30, h=28, r=9, pad=0)
    out["field"] = _pill(fill=c["field"], border=c["field_border"], **f)
    out["field-hover"] = _pill(fill=c["field"], border=c["glow"] if mode_is_dark(c) else c["accent_hover"], **f)
    out["field-focus"] = _pill(fill=c["field"], border=c["accent"], bw=1.8, **f)
    out["field-disabled"] = _pill(fill=c["disabled_fill"], border=c["disabled_border"], **f)
    # Check boxes and option buttons, with a little room before their label.
    for name, on, hover, disabled in (("check", False, False, False), ("check-on", True, False, False),
                                      ("check-hover", False, True, False), ("check-on-hover", True, True, False),
                                      ("check-disabled", False, False, True), ("check-on-disabled", True, False, True)):
        im = _Image(24, 18)
        fill = c["accent_hover" if hover else "accent"] if on else c["field"]
        if disabled:
            fill = c["disabled_border"] if on else c["disabled_fill"]
        border = c["accent"] if (on or hover) and not disabled else c["disabled_border" if disabled else "field_border"]
        im.paint(_rounded(1, 1, 17, 17, 5), border)
        im.paint(_rounded(2.3, 2.3, 15.7, 15.7, 3.8), fill)
        if on:
            im.paint(_stroke([(5.2, 9.3), (7.8, 12.0), (13.0, 6.2)], 2.0),
                     c["accent_text"] if not disabled else c["card"])
        out[name] = im
    for name, on, hover, disabled in (("radio", False, False, False), ("radio-on", True, False, False),
                                      ("radio-hover", False, True, False), ("radio-on-hover", True, True, False),
                                      ("radio-disabled", False, False, True), ("radio-on-disabled", True, False, True)):
        im = _Image(24, 18)
        border = c["accent"] if (on or hover) and not disabled else c["disabled_border" if disabled else "field_border"]
        im.paint(_rounded(1, 1, 17, 17, 8), border)
        im.paint(_rounded(2.3, 2.3, 15.7, 15.7, 6.7), c["disabled_fill"] if disabled else c["field"])
        if on:
            im.paint(_rounded(5, 5, 13, 13, 4), c["disabled_border"] if disabled else c["accent"])
        out[name] = im
    # The drop-down arrow and the number box's arrows: small chevrons.
    for name, colour in (("chevron", c["muted"]), ("chevron-active", c["accent"])):
        im = _Image(20, 16)
        im.paint(_stroke([(6, 6.5), (10, 10.5), (14, 6.5)], 1.8), colour)
        out[name] = im
    for name, colour in (("up", c["muted"]), ("up-active", c["accent"])):
        im = _Image(16, 11)
        im.paint(_stroke([(4.5, 7.5), (8, 4), (11.5, 7.5)], 1.6), colour)
        out[name] = im
        im = _Image(16, 11)
        im.paint(_stroke([(4.5, 3.5), (8, 7), (11.5, 3.5)], 1.6), colour)
        out[name.replace("up", "down")] = im
    # The progress bar: a rounded groove, filled with a rounded blue bar.
    out["trough"] = _pill(30, 12, 6, c["trough"], c["trough"])
    out["pbar"] = _pill(30, 12, 6, c["accent_hover"], c["accent"], bottom=c["accent"])
    # Scroll bars: a slim rounded thumb on a clear track.
    out["vthumb"] = _pill(12, 30, 5, c["thumb"], c["thumb"], pad=1)
    out["hthumb"] = _pill(30, 12, 5, c["thumb"], c["thumb"], pad=1)
    out["vthumb-active"] = _pill(12, 30, 5, c["muted"], c["muted"], pad=1)
    out["hthumb-active"] = _pill(30, 12, 5, c["muted"], c["muted"], pad=1)
    # The main window's icons, drawn in lines like the Wii's channel icons.
    for name, strokes in ICONS.items():
        for state, colour in (("", c["accent"]), ("-disabled", c["disabled_text"])):
            im = _Image(34, 34)
            for line in strokes:
                im.paint(_stroke(line, 2.3), colour)
            out["icon-%s%s" % (name, state)] = im
    return {k: v.png() for k, v in out.items()}


# Line drawings on a 34-pixel square: the Office (a cross), the Mass (a
# chalice and host), a PDF (a page), the breviary (an open book with its ribbon).
ICONS = {
    "office": [[(17, 4), (17, 30)], [(9, 12), (25, 12)]],
    "mass": [[(9, 10), (9.6, 14), (12, 17.6), (17, 19), (22, 17.6), (24.4, 14), (25, 10), (9, 10)],
             [(17, 19), (17, 26)], [(11, 28), (13.5, 26), (20.5, 26), (23, 28), (11, 28)],
             [(17, 3.2), (19.6, 4.3), (20.5, 6.4), (19.6, 8.5), (17, 9.4), (14.4, 8.5), (13.5, 6.4),
              (14.4, 4.3), (17, 3.2)]],
    "pdf": [[(9, 4), (20, 4), (26, 10), (26, 30), (9, 30), (9, 4)], [(20, 4), (20, 10), (26, 10)],
            [(13, 16), (22, 16)], [(13, 20), (22, 20)], [(13, 24), (19, 24)]],
    "breviary": [[(17, 9), (11, 6.5), (4, 7.5), (4, 26), (11, 25), (17, 27.5)],
                 [(17, 9), (23, 6.5), (30, 7.5), (30, 26), (23, 25), (17, 27.5)],
                 [(17, 9), (17, 27.5)], [(22, 7), (22, 15), (24, 13), (26, 15), (26, 7.2)]],
}


def icon(name):
    """The image option for a button showing an icon: blue, grey when disabled."""
    images = _state["themes"].get(_theme_name(mode()), {})
    if "icon-" + name not in images:
        return ""
    return (images["icon-" + name], "disabled", images["icon-%s-disabled" % name])


def set_icon(button, name):
    """Show an icon above the button's text, in the colours of each mode."""
    button.configure(image=icon(name), compound="top")
    on_change(lambda _m: button.configure(image=icon(name)))


def mode_is_dark(c):
    return c is PALETTES["dark"]


def _build_theme(style, name, c):
    images = {k: _photo(v) for k, v in _images(c).items()}
    _state["themes"][name] = images  # Tk shows an image only while it is kept
    style.theme_create(name, parent="clam")
    style.theme_use(name)
    i = images
    ec = style.element_create

    def states(base, disabled=None, active=None, pressed=None, focus=None):
        spec = []
        if disabled:
            spec.append(("disabled", i[disabled]))
        if pressed:
            spec.append(("pressed", i[pressed]))
        if focus:
            spec.append(("focus", i[focus]))
        if active:
            spec.append(("active", i[active]))
        return [i[base]] + spec

    ec("Wii.Button.bg", "image", *states("button", "button-disabled", "button-hover", "button-press"),
       border=15, padding=(8, 3), sticky="nsew")
    ec("Wii.Accent.bg", "image", *states("accent", "accent-disabled", "accent-hover", "accent-press"),
       border=15, padding=(8, 3), sticky="nsew")
    ec("Wii.Small.bg", "image", *states("small", "small-disabled", "small-hover", "small-press"),
       border=11, padding=(5, 1), sticky="nsew")
    ec("Wii.Tile.bg", "image", *states("tile", "tile-disabled", "tile-hover", "tile-press"),
       border=22, padding=(10, 8), sticky="nsew")
    ec("Wii.Card", "image", i["card"], border=20, padding=(6, 4, 6, 7), sticky="nsew")
    ec("Wii.Field", "image", i["field"], ("disabled", i["field-disabled"]), ("focus", i["field-focus"]),
       ("active", i["field-hover"]), border=10, padding=(8, 3, 4, 3), sticky="nsew")
    ec("Wii.Checkbutton.indicator", "image", i["check"],
       ("disabled selected", i["check-on-disabled"]), ("disabled", i["check-disabled"]),
       ("active selected", i["check-on-hover"]), ("selected", i["check-on"]),
       ("active", i["check-hover"]), sticky="")
    ec("Wii.Radiobutton.indicator", "image", i["radio"],
       ("disabled selected", i["radio-on-disabled"]), ("disabled", i["radio-disabled"]),
       ("active selected", i["radio-on-hover"]), ("selected", i["radio-on"]),
       ("active", i["radio-hover"]), sticky="")
    ec("Wii.Combobox.downarrow", "image", i["chevron"], ("active", i["chevron-active"]),
       ("pressed", i["chevron-active"]), sticky="")
    ec("Wii.Spinbox.uparrow", "image", i["up"], ("active", i["up-active"]), sticky="")
    ec("Wii.Spinbox.downarrow", "image", i["down"], ("active", i["down-active"]), sticky="")
    ec("Wii.Horizontal.Progressbar.trough", "image", i["trough"], border=(6, 5), padding=0, sticky="nsew")
    ec("Wii.Horizontal.Progressbar.pbar", "image", i["pbar"], border=(6, 5), padding=0, sticky="nsew")
    ec("Wii.Vertical.Scrollbar.thumb", "image", i["vthumb"], ("active", i["vthumb-active"]),
       ("pressed", i["vthumb-active"]), border=(5, 6), padding=0, sticky="nsew")
    ec("Wii.Horizontal.Scrollbar.thumb", "image", i["hthumb"], ("active", i["hthumb-active"]),
       ("pressed", i["hthumb-active"]), border=(6, 5), padding=0, sticky="nsew")

    def button_layout(bg):
        return [(bg, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]

    style.layout("TButton", button_layout("Wii.Button.bg"))
    style.layout("Accent.TButton", button_layout("Wii.Accent.bg"))
    style.layout("Tile.TButton", button_layout("Wii.Tile.bg"))
    style.layout("Small.TButton", button_layout("Wii.Small.bg"))
    style.layout("TLabelframe", [("Wii.Card", {"sticky": "nsew"})])
    style.layout("Card.TFrame", [("Wii.Card", {"sticky": "nsew"})])
    style.layout("TEntry", [("Wii.Field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    style.layout("TCombobox", [("Wii.Field", {"sticky": "nsew", "children": [
        ("Wii.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
    style.layout("TSpinbox", [("Wii.Field", {"sticky": "nsew", "children": [
        ("null", {"side": "right", "sticky": "ns", "children": [
            ("Wii.Spinbox.uparrow", {"side": "top", "sticky": "e"}),
            ("Wii.Spinbox.downarrow", {"side": "bottom", "sticky": "e"})]}),
        ("Spinbox.padding", {"sticky": "nsew", "children": [("Spinbox.textarea", {"sticky": "nsew"})]})]})])
    for kind in ("Checkbutton", "Radiobutton"):
        style.layout("T" + kind, [("%s.padding" % kind, {"sticky": "nsew", "children": [
            ("Wii.%s.indicator" % kind, {"side": "left", "sticky": ""}),
            ("%s.label" % kind, {"side": "left", "sticky": "nsew"})]})])
    style.layout("Horizontal.TProgressbar", [("Wii.Horizontal.Progressbar.trough", {"sticky": "nsew", "children": [
        ("Wii.Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Wii.Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nsew"})]})])
    style.layout("Horizontal.TScrollbar", [("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [
        ("Wii.Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nsew"})]})])

    body = (FONT, 9)
    style.configure(".", background=c["card"], foreground=c["text"], font=body,
                    troughcolor=c["card"], bordercolor=c["card_border"], lightcolor=c["card"],
                    darkcolor=c["card"], focuscolor=c["accent"], selectbackground=c["select"],
                    selectforeground=c["text"], fieldbackground=c["field"], insertcolor=c["text"],
                    arrowcolor=c["muted"])
    style.map(".", foreground=[("disabled", c["disabled_text"])])
    style.configure("TFrame", background=c["card"])
    style.configure("Window.TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["bg"])
    style.configure("TLabelframe", background=c["bg"], labeloutside=True, labelmargins=(8, 0, 0, 5))
    style.configure("TLabelframe.Label", background=c["bg"], foreground=c["heading"],
                    font=(FONT, 10, "bold"))
    style.configure("TLabel", background=c["card"], foreground=c["text"])
    for name, fg in (("Muted", "muted"), ("Error", "error"), ("Warn", "warn"), ("Ok", "ok")):
        style.configure("%s.TLabel" % name, foreground=c[fg])
        style.configure("Window.%s.TLabel" % name, background=c["bg"], foreground=c[fg])
    style.configure("Strong.TLabel", font=(FONT, 9, "bold"))
    style.configure("Window.TLabel", background=c["bg"], foreground=c["text"])
    style.configure("Title.TLabel", background=c["bg"], foreground=c["text"], font=(FONT, 20))
    style.configure("Heading.TLabel", background=c["card"], foreground=c["heading"], font=(FONT, 10, "bold"))
    for kind in ("TButton", "Accent.TButton", "Tile.TButton", "Small.TButton"):
        style.configure(kind, background=c["card"], anchor="center", justify="center",
                        foreground=c["accent_text"] if kind == "Accent.TButton" else c["text"])
        style.map(kind, foreground=[("disabled", c["disabled_text"])], background=[("active", c["card"])])
    style.configure("TButton", padding=(10, 2))
    style.configure("Accent.TButton", padding=(14, 2), font=(FONT, 9, "bold"))
    style.configure("Tile.TButton", padding=(14, 10), font=(FONT, 11))
    style.configure("Window.TButton", background=c["bg"])
    style.configure("Small.TButton", padding=(8, 0))
    for kind in ("TCheckbutton", "TRadiobutton"):
        style.configure(kind, background=c["card"], padding=(0, 2))
        style.map(kind, background=[("active", c["card"])])
    style.configure("Window.TCheckbutton", background=c["bg"])
    for kind in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(kind, foreground=c["text"], fieldbackground=c["field"], padding=(2, 1),
                        background=c["card"], insertcolor=c["text"])
    style.map("TCombobox", fieldbackground=[("readonly", c["field"])],
              selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["text"])],
              foreground=[("disabled", c["disabled_text"])])
    style.map("TEntry", foreground=[("disabled", c["disabled_text"])])
    style.map("TSpinbox", foreground=[("disabled", c["disabled_text"])])
    style.configure("Horizontal.TProgressbar", background=c["card"], thickness=12)
    for kind in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(kind, troughcolor=c["card"], background=c["card"], borderwidth=0, arrowsize=12)
    style.configure("Window.Vertical.TScrollbar", troughcolor=c["bg"])
    style.configure("TSeparator", background=c["card_border"])


def _theme_name(m):
    return "wii-" + m


def _use(root, m):
    style = ttk.Style(root)
    name = _theme_name(m)
    if name not in style.theme_names():
        _build_theme(style, name, PALETTES[m])
    style.theme_use(name)
    c = PALETTES[m]
    # The drop-down lists of the combo boxes are plain Tk lists.
    for pattern, value in (("*TCombobox*Listbox.background", c["field"]),
                           ("*TCombobox*Listbox.foreground", c["text"]),
                           ("*TCombobox*Listbox.selectBackground", c["accent"]),
                           ("*TCombobox*Listbox.selectForeground", c["accent_text"]),
                           ("*TCombobox*Listbox.font", (FONT, 9))):
        root.option_add(pattern, value)


def install(window):
    """Give a window the theme (built for the app on its first window)."""
    root = window._root()
    m = mode()
    if _state["mode"] != m or _theme_name(m) not in ttk.Style(root).theme_names():
        _state["mode"] = m
        _use(root, m)
    _recolour(window, PALETTES[m])
    _title_bar(window, m == "dark")


def present(window, over=None, size=None, fade=True):
    """Show a window that was built withdrawn, all at once: placed (centred on
    `over`, else on the screen, and kept on it), its title bar in the mode
    while it is still invisible, then faded in. A window that draws itself in
    view -- widget by widget, the empty frame first, in the corner where
    Windows puts it -- looks unfinished; this shows it finished."""
    window.update_idletasks()
    w, h = size or (window.winfo_reqwidth(), window.winfo_reqheight())
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    if over is not None and over.winfo_viewable():
        cx = over.winfo_rootx() + over.winfo_width() // 2
        cy = over.winfo_rooty() + over.winfo_height() // 2
    else:
        cx, cy = sw // 2, sh // 2
    x = max(0, min(cx - w // 2, sw - w))
    y = max(0, min(cy - h // 2, sh - h - 48))  # above the taskbar
    window.geometry(("%dx%d" % (w, h) if size else "") + "+%d+%d" % (x, y))
    try:
        window.attributes("-alpha", 0.0)
    except tk.TclError:
        fade = False
    window.deiconify()
    _title_bar(window, mode() == "dark")
    window.update_idletasks()
    if not fade:
        window.attributes("-alpha", 1.0)
        return
    steps = (0.25, 0.5, 0.75, 0.92, 1.0)

    def step(k=0):
        try:
            window.attributes("-alpha", steps[k])
        except tk.TclError:
            return  # closed meanwhile
        if k + 1 < len(steps):
            window.after(28, step, k + 1)

    window.after(10, step)


def busy(window, on):
    """The waiting cursor over a window while another is being made."""
    try:
        window.config(cursor="watch" if on else "")
        window.update_idletasks()
    except tk.TclError:
        pass


def set_mode(m, root=None, save=True):
    if m not in PALETTES:
        return
    _state["mode"] = m
    if save:
        _save_mode(m)
    if root is None:
        root = tk._default_root
    if root is None:
        return
    _use(root, m)
    c = PALETTES[m]
    for w in [root] + _toplevels(root):
        _recolour(w, c)
        _title_bar(w, m == "dark")
    for callback in list(_state["listeners"]):
        try:
            callback(m)
        except tk.TclError:
            _state["listeners"].remove(callback)


def toggle(root=None):
    set_mode("light" if mode() == "dark" else "dark", root)


def mode_button(parent, style="TButton"):
    """A button that switches between the modes, named for the one it gives."""
    btn = ttk.Button(parent, style=style, command=lambda: toggle(parent._root()))

    def label(m=None):
        btn.config(text="☀  Light" if (m or mode()) == "dark" else "☾  Dark")

    label()
    on_change(label)
    return btn


# --------------------------------------------------------------------------
# what the theme does not reach


def role(widget, name):
    """Mark a plain Tk widget for recolouring: "log" (a text field), "paper"
    (a sample on white paper: only its frame changes), "preview" (the page
    preview's backdrop)."""
    widget._doui_role = name
    _recolour_one(widget, colours())
    return widget


def _toplevels(root):
    out, todo = [], list(root.winfo_children())
    while todo:
        w = todo.pop()
        if isinstance(w, tk.Toplevel):
            out.append(w)
        todo.extend(w.winfo_children())
    return out


def _recolour_one(w, c):
    r = getattr(w, "_doui_role", None)
    try:
        if isinstance(w, (tk.Tk, tk.Toplevel)):
            w.configure(background=c["bg"])
        elif r == "log":
            w.configure(background=c["log"], foreground=c["text"], insertbackground=c["text"],
                        selectbackground=c["select"], selectforeground=c["text"],
                        highlightbackground=c["field_border"], highlightcolor=c["accent"])
        elif r == "paper":
            w.configure(highlightbackground=c["field_border"])
        elif r == "preview":
            w.configure(background=c["canvas"])
        elif r == "card-bg":
            w.configure(background=c["card"])
        elif isinstance(w, ttk.Combobox):
            # Only a list already opened: one not yet made takes the colours
            # of the option database when it is (making each here was slow).
            pop = str(w) + ".popdown"
            if int(w.tk.call("winfo", "exists", pop)):
                w.tk.call(pop + ".f.l", "configure", "-background", c["field"], "-foreground", c["text"],
                          "-selectbackground", c["accent"], "-selectforeground", c["accent_text"])
    except tk.TclError:
        pass


def _recolour(window, c):
    todo = [window]
    while todo:
        w = todo.pop()
        _recolour_one(w, c)
        todo.extend(w.winfo_children())


def _title_bar(window, dark):
    """Windows 10 and 11 draw a window's title bar dark when asked."""
    if os.name != "nt":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = int(window.wm_frame(), 16)
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, and its number before 20H1
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # Redraw the frame now (SWP_NOMOVE | NOSIZE | NOZORDER | NOACTIVATE | FRAMECHANGED).
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x2 | 0x1 | 0x4 | 0x10 | 0x20)
    except Exception:  # an older Windows, or no window yet: the default frame
        pass
