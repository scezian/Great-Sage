"""
card_styler.py — Great Sage Plugin
Restyle the four dashboard module cards.
Drop into ~/Documents/Great-Sage/plugins/
"""

PLUGIN_NAME        = "Card Styler"
PLUGIN_ICON        = "◧"
PLUGIN_DESCRIPTION = "Restyle the dashboard cards — frosted glass, neon glow & more"
PLUGIN_VERSION     = "1.3"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#C9A84C"

import re as _re, types as _types
from PyQt6.QtCore    import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QRadialGradient,
                              QBrush, QPen, QPainterPath)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                              QPushButton, QLabel, QSlider, QColorDialog,
                              QSizePolicy)

PRESETS = {
    "Default":       {"mode":"flat",   "bg":"#111116","border":"#252530","radius":4, "glow_alpha":0,  "shimmer":False},
    "Frosted Glass": {"mode":"glass",  "bg":"#1A1A2E","border":"#5A5A8A","radius":14,"glow_alpha":0,  "shimmer":True },
    "Neon Glow":     {"mode":"glow",   "bg":"#0A0A12","border":"#1A1A28","radius":6, "glow_alpha":70, "shimmer":False},
    "Ink & Paper":   {"mode":"paper",  "bg":"#1C1810","border":"#3A3020","radius":2, "glow_alpha":0,  "shimmer":False},
    "Void":          {"mode":"void",   "bg":"#050508","border":"#050508","radius":0, "glow_alpha":0,  "shimmer":False},
    "Custom":        {"mode":"custom", "bg":"#14141E","border":"#303050","radius":8, "glow_alpha":0,  "shimmer":False},
}
PRESET_NAMES = list(PRESETS.keys())

CARD_ACCENTS = {"legion":"#C9A84C","matrix":"#4A90D9","sage":"#8B6FD4","plugins":"#4EC9A4"}
PRESET_TEXT  = {"Default":"#E8E4DC","Frosted Glass":"#E8E4DC","Neon Glow":"#E8E4DC",
                "Ink & Paper":"#D4C8A8","Void":"#C8C8D8","Custom":"#E8E4DC"}
PRESET_TEXT2 = {"Default":"#9090A4","Frosted Glass":"#9A9AB0","Neon Glow":"#9090A4",
                "Ink & Paper":"#8A7A60","Void":"#606070","Custom":"#9090A4"}


def _patch_card(card, key, preset, preset_name, opacity):
    accent = CARD_ACCENTS.get(key, "#C9A84C")
    mode   = preset["mode"]
    bg     = preset["bg"]
    border = preset["border"]
    radius = int(preset["radius"])
    shimmer= bool(preset.get("shimmer", False))
    galpha = int(preset.get("glow_alpha", 0))
    textc  = PRESET_TEXT.get(preset_name,  "#E8E4DC")
    text2c = PRESET_TEXT2.get(preset_name, "#9090A4")

    card._cs_preset  = dict(preset)
    card._cs_accent  = accent
    card._cs_opacity = opacity
    card._cs_shimmer = shimmer

    if not card.objectName():
        card.setObjectName(f"gs_card_{key}")
    obj = card.objectName()

    c = QColor(bg)
    c.setAlphaF(opacity)
    r,g,b,a = c.red(),c.green(),c.blue(),c.alpha()

    if mode == "void":
        ss = (f"QWidget#{obj}{{background:{bg};border:none;"
              f"border-bottom:2px solid {accent};border-radius:0px;}}"
              f"QWidget#{obj}:hover{{background:#0D0D14;}}")
    elif mode in ("glass","glow"):
        ss = (f"QWidget#{obj}{{background:transparent;border:1px solid {border};"
              f"border-radius:{radius}px;}}"
              f"QWidget#{obj}:hover{{border-color:{accent};}}")
    else:
        ss = (f"QWidget#{obj}{{background-color:rgba({r},{g},{b},{a});"
              f"border:1px solid {border};border-radius:{radius}px;}}"
              f"QWidget#{obj}:hover{{border-color:{accent};"
              f"background-color:rgba({min(r+8,255)},{min(g+8,255)},{min(b+8,255)},{a});}}")
    card.setStyleSheet(ss)

    for child in card.findChildren(QLabel):
        # Only restyle labels that Great Sage hasn't explicitly named —
        # named labels carry intentional accent colours we must not wipe.
        if child.objectName():
            continue
        existing = child.styleSheet()
        clean = _re.sub(r'color\s*:[^;]+;?', '', existing).strip()
        child.setStyleSheet(f"background:transparent;color:{textc};{clean}")

    for child in card.findChildren(QPushButton):
        if not child.objectName():
            child.setStyleSheet(
                f"QPushButton{{background:transparent;border:1px solid {border};"
                f"color:{text2c};font-size:10px;letter-spacing:1.5px;"
                f"padding:7px 14px;border-radius:2px;text-align:left;}}"
                f"QPushButton:hover{{border-color:{accent};color:{accent};}}")

    if mode in ("glass","glow","paper","void"):
        def _paint(self_w, _event):
            p = QPainter(self_w)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            W,H = self_w.width(), self_w.height()
            pr  = getattr(self_w,"_cs_preset",{})
            acc = getattr(self_w,"_cs_accent","#C9A84C")
            m   = pr.get("mode","flat")
            op  = getattr(self_w,"_cs_opacity",1.0)
            shm = getattr(self_w,"_cs_shimmer",False)
            rad = float(pr.get("radius",4))
            path = QPainterPath()
            path.addRoundedRect(QRectF(0,0,W,H), rad, rad)
            p.setClipPath(path)
            ac = QColor(acc)
            if m == "glass":
                base = QColor(pr.get("bg","#1A1A2E")); base.setAlpha(int(210*op))
                p.fillPath(path, QBrush(base))
                dep = QLinearGradient(0,0,0,H)
                dep.setColorAt(0,QColor(255,255,255,22)); dep.setColorAt(.5,QColor(255,255,255,5))
                dep.setColorAt(1,QColor(0,0,0,18)); p.fillPath(path,QBrush(dep))
                if shm:
                    sh = QLinearGradient(QPointF(0,0),QPointF(W*.7,H*.45))
                    sh.setColorAt(0,QColor(255,255,255,0)); sh.setColorAt(.4,QColor(255,255,255,28))
                    sh.setColorAt(.7,QColor(255,255,255,10)); sh.setColorAt(1,QColor(255,255,255,0))
                    p.fillPath(path,QBrush(sh))
                tl = QLinearGradient(0,0,W,0)
                tl.setColorAt(0,QColor(255,255,255,0)); tl.setColorAt(.3,QColor(255,255,255,60))
                tl.setColorAt(.7,QColor(255,255,255,60)); tl.setColorAt(1,QColor(255,255,255,0))
                p.setPen(QPen(QBrush(tl),1)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(int(rad),0,int(W-rad),0)
                bc = QColor(pr.get("border","#5A5A8A")); bc.setAlpha(180)
                p.setPen(QPen(bc,1)); p.drawPath(path)
            elif m == "glow":
                base = QColor(pr.get("bg","#0A0A12")); base.setAlpha(int(255*op))
                p.fillPath(path,QBrush(base))
                ga = int(pr.get("glow_alpha",70)*op)
                # Top-centre bloom — tight and bright
                grd = QRadialGradient(QPointF(W*.5, 0), W*.65)
                grd.setColorAt(0,    QColor(ac.red(),ac.green(),ac.blue(), min(255, ga*2)))
                grd.setColorAt(0.35, QColor(ac.red(),ac.green(),ac.blue(), ga))
                grd.setColorAt(0.7,  QColor(ac.red(),ac.green(),ac.blue(), ga//4))
                grd.setColorAt(1,    QColor(ac.red(),ac.green(),ac.blue(), 0))
                p.fillPath(path,QBrush(grd))
                # Bottom accent line
                p.setPen(QPen(QColor(ac.red(),ac.green(),ac.blue(),180),2))
                p.setBrush(Qt.BrushStyle.NoBrush); p.drawLine(int(rad),H-1,int(W-rad),H-1)
                # Glowing border
                p.setPen(QPen(QColor(ac.red(),ac.green(),ac.blue(),160),1.5)); p.drawPath(path)
                # Inner soft halo
                p.setPen(QPen(QColor(ac.red(),ac.green(),ac.blue(),40),4)); p.drawPath(path)
            elif m == "paper":
                base = QColor(pr.get("bg","#1C1810")); base.setAlpha(int(255*op))
                p.fillPath(path,QBrush(base))
                sheen = QLinearGradient(0,0,0,H*.4)
                sheen.setColorAt(0,QColor(255,240,200,14)); sheen.setColorAt(1,QColor(255,240,200,0))
                p.fillPath(path,QBrush(sheen))
                p.setPen(QPen(QColor(pr.get("border","#3A3020")),1))
                p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
            elif m == "void":
                base = QColor(pr.get("bg","#050508")); base.setAlpha(int(255*op))
                p.fillPath(path,QBrush(base))
            p.end()
        card.paintEvent = _types.MethodType(_paint, card)
    else:
        card.__dict__.pop("paintEvent", None)
    card.update()


def _get_dashboard(w):
    """Walk up the widget tree looking for _page_objs, then return the dashboard page.
    Falls back to scanning all top-level windows so QStackedWidget's hidden intermediate
    layers can't push MainWindow out of reach."""
    root = w
    # Fast path: parent-chain walk (limit raised — QStackedWidget adds hidden layers)
    cur = w
    for _ in range(50):
        if cur is None:
            break
        if hasattr(cur, "_page_objs"):
            dash = cur._page_objs.get("dashboard")
            if dash is None:
                import sys
                print("[CardStyler] _page_objs found but no 'dashboard' key", file=sys.stderr)
            return dash
        cur = cur.parent()
    # Fallback: scan every top-level window — handles detached/not-yet-shown widgets
    from PyQt6.QtWidgets import QApplication
    for win in QApplication.topLevelWidgets():
        if hasattr(win, "_page_objs"):
            dash = win._page_objs.get("dashboard")
            if dash is not None:
                return dash
    import sys
    print(f"[CardStyler] _get_dashboard: could not find _page_objs from {root}", file=sys.stderr)
    return None


def build_page(parent, api):
    saved_preset  = api.load_plugin_data("preset")  or "Default"
    saved_custom  = api.load_plugin_data("custom")  or {}
    saved_opacity = api.load_plugin_data("opacity") or 100
    if saved_preset not in PRESETS: saved_preset = "Default"
    # Non-destructive merge — avoids stale state if module is reloaded
    if saved_custom: PRESETS["Custom"] = {**PRESETS["Custom"], **saved_custom}
    state = {"preset": saved_preset, "opacity": int(saved_opacity)}

    BG     = "#0C0C0E"
    BG2    = "#111116"
    BORDER = "#252530"
    ACCENT = "#C9A84C"
    MUTED  = "#606070"
    TEXT2  = "#A0A0B4"

    # ── Page: flat layout, everything in one QVBoxLayout ──────────────────────
    page = QWidget()
    page.setStyleSheet(f"background:{BG2};")
    page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = QWidget()
    hdr.setFixedHeight(52)
    hdr.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};")
    hh = QHBoxLayout(hdr)
    hh.setContentsMargins(28,0,28,0)
    ht = QLabel("◧  CARD STYLER")
    ht.setStyleSheet(f"color:{ACCENT};font-size:14px;font-weight:bold;letter-spacing:3px;")
    hh.addWidget(ht); hh.addStretch()
    lay.addWidget(hdr)

    # ── Content area — solid background, direct children ─────────────────────
    content = QWidget()
    content.setStyleSheet(f"background:{BG};")
    content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    cv = QVBoxLayout(content)
    cv.setContentsMargins(32, 28, 32, 28)
    cv.setSpacing(20)
    cv.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.addWidget(content, 1)

    def _sec(txt):
        l = QLabel(txt)
        l.setStyleSheet(f"color:{MUTED};font-size:10px;letter-spacing:3px;")
        l.setFixedHeight(20)
        return l

    def _row_lbl(txt):
        l = QLabel(txt)
        l.setStyleSheet(f"color:{TEXT2};font-size:13px;")
        return l

    # ── PRESET label ──────────────────────────────────────────────────────────
    cv.addWidget(_sec("PRESET"))

    # ── Tile grid — 3×2, each tile explicitly sized ───────────────────────────
    tiles = {}

    def _tile_ss(sel, obj):
        border = "#C9A84C" if sel else "#3A3A52"
        bg_t   = "#252538" if sel else "#1A1A26"
        return (f"QWidget#{obj}{{background:{bg_t};border:2px solid {border};"
                f"border-radius:8px;}}"
                f"QWidget#{obj}:hover{{border-color:#8A8A9A;}}")

    def _refresh_tile(tile, sel):
        obj = tile.objectName()
        tile.setStyleSheet(_tile_ss(sel, obj))
        labels = tile.findChildren(QLabel)
        if len(labels) >= 2:
            labels[0].setStyleSheet(
                f"color:{'#F0EDE8' if sel else '#C8C8DC'};"
                f"font-size:13px;font-weight:bold;")
            labels[1].setStyleSheet(
                f"color:{'#C9A84C' if sel else '#7A7A8A'};"
                f"font-size:10px;letter-spacing:1px;")

    def _on_click(name):
        state["preset"] = name
        for n, t in tiles.items():
            _refresh_tile(t, n == name)
        custom_box.setVisible(name == "Custom")
        _apply(); _save()

    # Build grid rows manually (2 rows × 3 cols) for maximum layout control
    for row_i in range(2):
        row_w = QWidget()
        row_w.setStyleSheet(f"background:{BG};")
        row_w.setFixedHeight(72)
        rh = QHBoxLayout(row_w)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(10)

        for col_i in range(3):
            idx  = row_i * 3 + col_i
            if idx >= len(PRESET_NAMES):
                rh.addStretch(1)
                continue
            name = PRESET_NAMES[idx]
            sel  = (name == saved_preset)
            obj  = f"cstile_{''.join(c if c.isalnum() else '_' for c in name)}"

            tile = QWidget()
            tile.setObjectName(obj)
            tile.setFixedHeight(68)
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.setStyleSheet(_tile_ss(sel, obj))
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            tl = QVBoxLayout(tile)
            tl.setContentsMargins(14, 10, 14, 10)
            tl.setSpacing(4)

            nl = QLabel(name)
            nl.setStyleSheet(
                f"color:{'#F0EDE8' if sel else '#C8C8DC'};"
                f"font-size:13px;font-weight:bold;")
            sl = QLabel(PRESETS[name]["mode"].capitalize())
            sl.setStyleSheet(
                f"color:{'#C9A84C' if sel else '#7A7A8A'};"
                f"font-size:10px;letter-spacing:1px;")

            tl.addWidget(nl)
            tl.addWidget(sl)

            tile.mousePressEvent = (lambda e, n=name: _on_click(n))
            tiles[name] = tile
            rh.addWidget(tile, 1)

        cv.addWidget(row_w)

    # ── Custom options ─────────────────────────────────────────────────────────
    custom_box = QWidget()
    custom_box.setObjectName("cs_cust")
    custom_box.setStyleSheet(
        f"QWidget#cs_cust{{background:{BG2};border:1px solid {BORDER};border-radius:6px;}}")
    custom_box.setVisible(saved_preset == "Custom")
    cbv = QVBoxLayout(custom_box)
    cbv.setContentsMargins(20,16,20,16); cbv.setSpacing(12)
    ch = QLabel("CUSTOM OPTIONS")
    ch.setStyleSheet(f"color:{MUTED};font-size:9px;letter-spacing:2px;")
    cbv.addWidget(ch)

    def _swatch(hex_col, on_change):
        btn = QPushButton()
        btn.setFixedSize(38, 24)
        btn._hex = hex_col
        def _set(h):
            btn.setStyleSheet(f"background:{h};border:1px solid #454555;"
                              f"border-radius:3px;")
        _set(hex_col)
        def _pick():
            c = QColorDialog.getColor(QColor(btn._hex), btn, "Pick colour")
            if c.isValid():
                btn._hex = c.name()
                _set(btn._hex)
                on_change(btn._hex)
        btn.clicked.connect(_pick)
        return btn

    def _sw_row(label, key, cur):
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_row_lbl(label))
        def _ch(h, k=key): PRESETS["Custom"][k]=h; _apply(); _save()
        row.addWidget(_swatch(cur, _ch)); row.addStretch()
        return row

    cbv.addLayout(_sw_row("Background:", "bg", PRESETS["Custom"].get("bg","#14141E")))
    cbv.addLayout(_sw_row("Border:", "border", PRESETS["Custom"].get("border","#303050")))

    rrow = QHBoxLayout()
    rrow.setSpacing(12)
    rrow.addWidget(_row_lbl("Corner radius:"))
    rsl = QSlider(Qt.Orientation.Horizontal)
    rsl.setRange(0,24); rsl.setValue(PRESETS["Custom"].get("radius",8)); rsl.setFixedWidth(140)
    rsl.setStyleSheet("QSlider::groove:horizontal{background:#252530;height:3px;border-radius:2px;}"
                      "QSlider::sub-page:horizontal{background:#C9A84C;border-radius:2px;}"
                      "QSlider::handle:horizontal{background:#C9A84C;width:12px;height:12px;margin:-5px 0;border-radius:6px;}")
    rlbl = QLabel(f"{PRESETS['Custom'].get('radius',8)}px")
    rlbl.setStyleSheet(f"color:{TEXT2};font-size:11px;min-width:30px;")
    def _rch(v): PRESETS["Custom"]["radius"]=v; rlbl.setText(f"{v}px"); _apply(); _save()
    rsl.valueChanged.connect(_rch)
    rrow.addWidget(rsl); rrow.addWidget(rlbl); rrow.addStretch()
    cbv.addLayout(rrow)
    cv.addWidget(custom_box)

    # ── Opacity ────────────────────────────────────────────────────────────────
    cv.addWidget(_sec("CARD OPACITY"))
    oprow = QHBoxLayout()
    oprow.setSpacing(12)
    opsl = QSlider(Qt.Orientation.Horizontal)
    opsl.setRange(40,100); opsl.setValue(state["opacity"]); opsl.setFixedWidth(200)
    opsl.setStyleSheet("QSlider::groove:horizontal{background:#252530;height:3px;border-radius:2px;}"
                       "QSlider::sub-page:horizontal{background:#C9A84C;border-radius:2px;}"
                       "QSlider::handle:horizontal{background:#C9A84C;width:12px;height:12px;margin:-5px 0;border-radius:6px;}")
    oplbl = QLabel(f"{state['opacity']}%")
    oplbl.setStyleSheet(f"color:{TEXT2};font-size:12px;min-width:36px;")
    def _opch(v): state["opacity"]=v; oplbl.setText(f"{v}%"); _apply(); _save()
    opsl.valueChanged.connect(_opch)
    oprow.addWidget(opsl); oprow.addWidget(oplbl); oprow.addStretch()
    cv.addLayout(oprow)

    hint = QLabel("Opacity affects the card background fill. "
                  "Lower values let the ambient background show through.")
    hint.setStyleSheet(f"color:{MUTED};font-size:11px;")
    hint.setWordWrap(True)
    cv.addWidget(hint)

    # ── Apply / save ───────────────────────────────────────────────────────────
    def _apply():
        dash = _get_dashboard(page)
        if not dash or not hasattr(dash,"_card_widgets"): return
        name = state["preset"]; preset = dict(PRESETS[name]); op = state["opacity"]/100.0
        for k, cw in dash._card_widgets.items():
            _patch_card(cw, k, preset, name, op)

    def _apply_with_retry(attempts=8, delay=150):
        """Try _apply up to `attempts` times with exponential back-off.
        Stops as soon as it succeeds. Covers slow startup and hot-reloads."""
        dash = _get_dashboard(page)
        if dash and hasattr(dash, "_card_widgets"):
            _apply()
            return
        if attempts > 1:
            next_delay = min(delay * 2, 2000)
            QTimer.singleShot(delay, lambda: _apply_with_retry(attempts - 1, next_delay))

    def _save():
        api.save_plugin_data("preset",  state["preset"])
        api.save_plugin_data("opacity", state["opacity"])
        api.save_plugin_data("custom",  {k:PRESETS["Custom"][k]
                                         for k in ("bg","border","radius")
                                         if k in PRESETS["Custom"]})

    QTimer.singleShot(300, lambda: _apply_with_retry())
    return page


def refresh(page):
    pass
