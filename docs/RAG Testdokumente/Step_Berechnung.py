#!/usr/bin/env python3
"""
Step_Berechnung.py – Janome Mehrlagenspur Generator v1.0.0
Flask-Webtool zur automatisierten Erstellung von Janome-Roboterprogrammen.
Berechnet mehrlagige Dosierspuren aus DXF- oder cp2d-Konturdaten.

Ausgabe: CSV für JR C-Points II (Typ, X, Y, Z, R)
"""

import sys

# ---------------------------------------------------------------------------
# Abhängigkeiten prüfen – fehlende Pakete klar benennen und beenden
# ---------------------------------------------------------------------------
_fehlend = []
for _pkg in ("flask", "ezdxf", "shapely"):
    try:
        __import__(_pkg)
    except ImportError:
        _fehlend.append(_pkg)

if _fehlend:
    print()
    for _pkg in _fehlend:
        print(f"  \u274c '{_pkg}' fehlt: pip install {_pkg}")
    print()
    sys.exit(1)

# ---------------------------------------------------------------------------
# Importe (alle Pakete sind garantiert verfügbar)
# ---------------------------------------------------------------------------
import math
import os
import io
import logging
import tempfile
import threading
import webbrowser
from math import ceil

from flask import Flask, request, send_file, render_template_string
import ezdxf
from shapely.geometry import LinearRing, Point, Polygon

VERSION = "1.2.0"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Nadelgrößen – nie ändern (maschinenspezifisch, hardcoded by design)
NEEDLE_IDS = [0.33, 0.41, 0.58, 0.61, 0.84]  # mm

UPLOAD_FOLDER = tempfile.gettempdir()

# Letztes berechnetes CSV-Ergebnis (single-user local tool)
_last_csv = None

# ---------------------------------------------------------------------------
# Segment-Datenstruktur
# ---------------------------------------------------------------------------
# LINE: {'type': 'LINE', 'start': (x, y), 'end': (x, y)}
# ARC:  {'type': 'ARC',  'center': (x, y), 'radius': float,
#         'start_angle': float (rad), 'end_angle': float (rad),
#         'ccw': bool, 'start': (x, y), 'end': (x, y)}


# ---------------------------------------------------------------------------
# Hilfsfunktionen (intern)
# ---------------------------------------------------------------------------

def _dist(p1, p2):
    """Euklidischer Abstand zweier 2D-Punkte."""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _arc_point(center, radius, angle_rad):
    """Punkt auf Kreisbahn bei gegebenem Winkel."""
    return (center[0] + radius * math.cos(angle_rad),
            center[1] + radius * math.sin(angle_rad))


def _arc_mid_angle(sa, ea, ccw):
    """
    Berechnet den Mittenwinkel eines Bogens von sa nach ea.
    Zirkuläre Mittelwertberechnung – korrekt auch beim Überqueren von 0°/360°.
    """
    if ccw:
        span = (ea - sa) % (2 * math.pi)
        return sa + span / 2.0
    else:
        span = (sa - ea) % (2 * math.pi)
        return sa - span / 2.0


def _circumscribed_circle(p1, p2, p3):
    """
    Berechnet Mittelpunkt und Radius des Umkreises durch 3 Punkte.
    Gibt (center, radius) zurück, oder (None, None) bei Kollinearität.
    """
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    D = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(D) < 1e-10:
        return None, None
    ux = ((ax**2 + ay**2) * (by - cy)
          + (bx**2 + by**2) * (cy - ay)
          + (cx**2 + cy**2) * (ay - by)) / D
    uy = ((ax**2 + ay**2) * (cx - bx)
          + (bx**2 + by**2) * (ax - cx)
          + (cx**2 + cy**2) * (bx - ax)) / D
    radius = math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2)
    return (ux, uy), radius


def _is_ccw_arc(center, radius, sa, ea, midpoint):
    """
    Prüft ob der Bogen von sa nach ea über midpoint CCW verläuft.
    Fix D: Kreuzprodukt-Methode – korrekt für Bögen >180° und an der 0°/360°-Grenze.
    Kreuzprodukt (midpoint - start) × (end - start):
      > 0 → CCW, < 0 → CW, = 0 → ValueError (Ambiguität, z.B. exakt 180°).
    """
    sx, sy = _arc_point(center, radius, sa)
    ex, ey = _arc_point(center, radius, ea)
    mx, my = midpoint

    cross = (mx - sx) * (ey - sy) - (my - sy) * (ex - sx)

    if abs(cross) < 1e-10:
        raise ValueError(
            "Bogen-Richtung ist nicht eindeutig bestimmbar "
            "(Kreuzprodukt ~0, z.B. exakt 180-Grad-Bogen oder kollineare Punkte). "
            "Kontur pruefen."
        )
    return cross > 0


def _flip_segment(seg):
    """Kehrt die Richtung eines Segments um."""
    if seg['type'] == 'LINE':
        return {'type': 'LINE', 'start': seg['end'], 'end': seg['start']}
    elif seg['type'] == 'ARC':
        return {
            'type': 'ARC',
            'center': seg['center'],
            'radius': seg['radius'],
            'start_angle': seg['end_angle'],
            'end_angle': seg['start_angle'],
            'ccw': not seg['ccw'],
            'start': seg['end'],
            'end': seg['start'],
        }
    return seg


def _seg_to_polyline(seg, arc_steps=16):
    """Konvertiert ein Segment in eine Liste von 2D-Punkten (für Shapely)."""
    if seg['type'] == 'LINE':
        return [seg['start'], seg['end']]
    elif seg['type'] == 'ARC':
        sa = seg['start_angle']
        ea = seg['end_angle']
        ccw = seg['ccw']
        if ccw and ea < sa:
            ea += 2 * math.pi
        elif not ccw and sa < ea:
            sa += 2 * math.pi
        steps = max(arc_steps, int(abs(ea - sa) / (math.pi / 8)))
        pts = []
        for k in range(steps + 1):
            angle = sa + (ea - sa) * k / steps
            pts.append(_arc_point(seg['center'], seg['radius'], angle))
        return pts
    return []


def _ring_to_shapely(ring_segs):
    """Baut einen Shapely-LinearRing aus einer geschlossenen Segmentliste."""
    pts = []
    for seg in ring_segs:
        pts.extend(_seg_to_polyline(seg)[:-1])
    if not pts:
        return None
    pts.append(pts[0])
    return LinearRing(pts)


# ---------------------------------------------------------------------------
# Kernfunktionen
# ---------------------------------------------------------------------------

def select_needle(width):
    """Wählt die nächstkleinere Nadel-ID für die gegebene Spurbreite."""
    suitable = [n for n in NEEDLE_IDS if n <= width]
    return max(suitable) if suitable else NEEDLE_IDS[0]


def read_dxf_contour(path, layer="1"):
    """
    Liest LINE/ARC-Entitäten aus einem DXF-Layer.
    Gibt eine Segmentliste zurück.

    path  : Pfad zur DXF-Datei
    layer : DXF-Layer-Name (Standard: "1")
    """
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    segs = []

    for entity in msp.query('LINE ARC'):
        if entity.dxf.layer != layer:
            continue
        if entity.dxftype() == 'LINE':
            segs.append({
                'type': 'LINE',
                'start': (entity.dxf.start.x, entity.dxf.start.y),
                'end':   (entity.dxf.end.x,   entity.dxf.end.y),
            })
        elif entity.dxftype() == 'ARC':
            cx, cy = entity.dxf.center.x, entity.dxf.center.y
            r = entity.dxf.radius
            sa = math.radians(entity.dxf.start_angle)
            ea = math.radians(entity.dxf.end_angle)
            # Fix C: ccw aus Winkelspanne ableiten, nicht pauschal True setzen.
            # DXF-Bögen laufen von sa nach ea; Spanne < π → CCW, sonst CW.
            ccw = (ea - sa) % (2 * math.pi) < math.pi
            segs.append({
                'type': 'ARC',
                'center': (cx, cy),
                'radius': r,
                'start_angle': sa,
                'end_angle': ea,
                'ccw': ccw,
                'start': _arc_point((cx, cy), r, sa),
                'end':   _arc_point((cx, cy), r, ea),
            })

    logger.info(f"read_dxf_contour: {len(segs)} Segmente aus Layer '{layer}' gelesen")
    return segs


def parse_cp2d(path):
    """
    Parst eine cp2d-Datei (Janome proprietäres Binärformat) → Segmentliste.

    Format: Binär, UTF-16 Big Endian (kein BOM).
    Koordinaten-Block zwischen 'Points_CSV' und 'SMC_CAM_FUNC'.

    Punkttypen:
      CP_S  – Continuous Path Start (Pfadanfang)
      CP_E  – Continuous Path End   (Pfadende)
      CP_P  – CP-Zwischenpunkt      (Gerade)
      ARC   – Bogenmittelpunkt      (3-Punkt-Bogen: prev, ARC, next)
      CP_CS – Vollkreis-Punkt auf Kreis  → zwei Halbkreis-ARCs
      CP_CC – Vollkreis-Mittelpunkt      → kombiniert mit CP_CS

    Rückgabe: (segs, circle_count) – Segmentliste + Anzahl Vollkreise
    """
    with open(path, "rb") as f:
        raw = f.read()

    # cp2d-Dateien sind UTF-16-BE (kein BOM).
    # Der Start-Marker "JR Points_CSV" hat in der Binärdatei einen Nicht-ASCII-Prefix
    # vor "R Points_CSV" – daher suchen wir nach dem stabilen Teil "Points_CSV".
    # Gleiches gilt für "JSMC_CAM_FUNC" → Suche nach "SMC_CAM_FUNC".
    text = None
    for enc in ("utf-16-be", "utf-16-le"):
        candidate = raw.decode(enc, errors="ignore")
        if "Points_CSV" in candidate:
            text = candidate
            logger.info(f"parse_cp2d: Encoding erkannt: {enc}")
            break

    if text is None:
        raise ValueError(
            f"Weder 'Points_CSV' in UTF-16-BE noch UTF-16-LE gefunden. "
            "Ist es eine gültige cp2d-Datei?"
        )

    start_idx = text.find("Points_CSV")
    end_idx = text.find("SMC_CAM_FUNC", start_idx)
    if end_idx == -1:
        end_idx = text.find("Points_CSV", start_idx + 10)
    if end_idx == -1:
        raise ValueError("End-Marker 'SMC_CAM_FUNC' nicht gefunden. Datei möglicherweise beschädigt.")

    csv_block = text[start_idx + len("Points_CSV"):end_idx]

    # --- CSV-Block parsen ---
    raw_points = []
    for line in csv_block.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 5:
            logger.debug(f"parse_cp2d: Zeile übersprungen (zu wenig Felder): {line!r}")
            continue

        ptype = parts[0].strip()
        try:
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
            r = float(parts[4])
        except ValueError:
            logger.debug(f"parse_cp2d: Zeile übersprungen (ungültige Zahlen): {line!r}")
            continue

        if ptype not in ('CP_S', 'CP_E', 'CP_P', 'ARC', 'CP_CS', 'CP_CC'):
            logger.warning(f"parse_cp2d: Unbekannter Punkttyp '{ptype}' – übersprungen.")
            continue

        raw_points.append({'type': ptype, 'x': x, 'y': y, 'z': z, 'r': r})

    logger.info(f"parse_cp2d: {len(raw_points)} Punkte aus '{os.path.basename(path)}' gelesen")

    if len(raw_points) < 2:
        raise ValueError(
            f"Zu wenige Punkte ({len(raw_points)}) für Segmentbildung. "
            "Enthält die Datei eine gültige Kontur?"
        )

    # --- Punkte → Segmente ---
    # Logik:
    #   CP_CS + CP_CC (paarweise) → zwei Halbkreis-ARCs (Vollkreis)
    #   ARC zwischen zwei Ankerpunkten → 3-Punkt-Bogen
    #   Zwei aufeinanderfolgende Ankerpunkte → Linie
    segs = []
    circle_count = 0
    i = 0
    n = len(raw_points)

    while i < n - 1:
        current = raw_points[i]

        # Vollkreis: CP_CS gefolgt von CP_CC → zwei Halbkreis-ARCs
        if (current['type'] == 'CP_CS'
                and raw_points[i + 1]['type'] == 'CP_CC'):

            cs = current
            cc = raw_points[i + 1]
            cs_pt = (cs['x'], cs['y'])
            cc_pt = (cc['x'], cc['y'])
            radius = _dist(cs_pt, cc_pt)

            if radius < 1e-6:
                logger.warning(f"parse_cp2d: Vollkreis-Radius ≈ 0 bei Index {i} – übersprungen.")
                i += 2
                continue

            sa = math.atan2(cs_pt[1] - cc_pt[1], cs_pt[0] - cc_pt[0])
            mid_angle = sa + math.pi
            ea = sa + 2 * math.pi
            opp_pt = _arc_point(cc_pt, radius, mid_angle)

            # Erste Hälfte: CS-Punkt → gegenüberliegender Punkt
            segs.append({
                'type': 'ARC',
                'center': cc_pt,
                'radius': radius,
                'start_angle': sa,
                'end_angle': mid_angle,
                'ccw': True,
                'start': cs_pt,
                'end': opp_pt,
            })
            # Zweite Hälfte: gegenüberliegender Punkt → CS-Punkt
            segs.append({
                'type': 'ARC',
                'center': cc_pt,
                'radius': radius,
                'start_angle': mid_angle,
                'end_angle': ea,
                'ccw': True,
                'start': opp_pt,
                'end': cs_pt,
            })

            circle_count += 1
            logger.info(
                f"parse_cp2d: Vollkreis bei Index {i} → Mittelpunkt {cc_pt}, "
                f"Radius {radius:.3f} mm"
            )
            i += 2

        # Vollkreis nur wenn CP_CS direkt von CP_CC gefolgt wird
        elif current['type'] == 'CP_CS':
            raise ValueError(
                f"CP_CS an Index {i} muss direkt von CP_CC gefolgt werden, "
                f"nicht von '{raw_points[i + 1]['type']}'. Datei fehlerhaft."
            )

        # Bogen: ARC-Midpoint zwischen zwei Ankerpunkten → 3-Punkt-Bogen
        # Fix E: Prüfen dass i+2 existiert und kein weiterer ARC ist (kein Waisen-ARC).
        elif raw_points[i + 1]['type'] == 'ARC':
            if i + 2 >= n:
                raise ValueError(
                    f"Kontur endet abrupt: ARC-Midpoint an Index {i + 1} "
                    "ohne End-Ankerpunkt. Datei fehlerhaft."
                )
            if raw_points[i + 2]['type'] in ('ARC',):
                raise ValueError(
                    f"Ungültige Punktfolge: ARC-Midpoint an Index {i + 1} "
                    f"gefolgt von weiterem ARC an Index {i + 2} ohne Ankerpunkt."
                )

            arc_mid = raw_points[i + 1]
            next_pt = raw_points[i + 2]

            p1 = (current['x'], current['y'])
            p2 = (arc_mid['x'], arc_mid['y'])
            p3 = (next_pt['x'], next_pt['y'])

            center, radius = _circumscribed_circle(p1, p2, p3)

            if center is None or radius < 1e-6:
                logger.warning(
                    f"parse_cp2d: Bogenberechnung fehlgeschlagen bei Index {i} "
                    "(kollineare Punkte oder Radius ≈ 0) – LINE-Fallback."
                )
                segs.append({'type': 'LINE', 'start': p1, 'end': p3})
            else:
                sa = math.atan2(p1[1] - center[1], p1[0] - center[0])
                ea = math.atan2(p3[1] - center[1], p3[0] - center[0])
                ccw = _is_ccw_arc(center, radius, sa, ea, p2)
                segs.append({
                    'type': 'ARC',
                    'center': center,
                    'radius': radius,
                    'start_angle': sa,
                    'end_angle': ea,
                    'ccw': ccw,
                    'start': p1,
                    'end': p3,
                })

            i += 2  # ARC-Midpoint verbraucht

        else:
            # Gerade Verbindung
            p1 = (current['x'], current['y'])
            p2 = (raw_points[i + 1]['x'], raw_points[i + 1]['y'])
            segs.append({'type': 'LINE', 'start': p1, 'end': p2})
            i += 1

    logger.info(
        f"parse_cp2d: {len(segs)} Segmente erzeugt "
        f"({sum(1 for s in segs if s['type'] == 'LINE')} LINE, "
        f"{sum(1 for s in segs if s['type'] == 'ARC')} ARC, "
        f"{circle_count} Vollkreis(e))"
    )
    return segs, circle_count


def sort_segments(segs):
    """
    Sortiert Segmente zu einer geschlossenen Kontur (Nearest-Neighbor auf Endpunkte).
    Segmente werden ggf. umgekehrt, wenn die Richtung besser passt.
    """
    if not segs:
        return segs

    result = [segs[0]]
    remaining = list(segs[1:])

    while remaining:
        last_end = result[-1]['end']
        best_idx = None
        best_dist = float('inf')
        best_flip = False

        for i, seg in enumerate(remaining):
            d_start = _dist(last_end, seg['start'])
            d_end = _dist(last_end, seg['end'])
            if d_start < best_dist:
                best_dist = d_start
                best_idx = i
                best_flip = False
            if d_end < best_dist:
                best_dist = d_end
                best_idx = i
                best_flip = True

        seg = remaining.pop(best_idx)
        if best_flip:
            seg = _flip_segment(seg)
        result.append(seg)

    # Fix F: Offene Kontur erkennen – Abstand zwischen letztem Ende und erstem Start prüfen.
    gap = _dist(result[0]['start'], result[-1]['end'])
    if gap > 1e-5:
        raise ValueError(
            f"Kontur ist nicht geschlossen: Lücke von {gap:.4f} mm "
            "zwischen Endpunkt und Startpunkt. "
            "Kontur im CAD schließen oder Segmente prüfen."
        )

    return result


def split_into_rings(segs, tol=1e-5):
    """
    Teilt eine flache Segmentliste in zusammenhängende Ringe (Komponenten).
    Verbindung = Endpunkt-Koinzidenz innerhalb von tol (Default: 1e-5 mm,
    konsistent zu Fix F in sort_segments).

    Rückgabe: Liste von Segmentlisten, eine pro Ring. Reihenfolge der Segmente
    innerhalb eines Rings bleibt erhalten. Bei leerer Eingabe: [].
    Bei einem einzigen Ring: [segs] – cp2d-Pipeline bleibt unverändert.
    """
    if not segs:
        return []

    n = len(segs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Paarweiser Endpunktvergleich – O(n²), unkritisch bei typischer Konturgröße.
    for i in range(n):
        for j in range(i + 1, n):
            pts_i = (segs[i]['start'], segs[i]['end'])
            pts_j = (segs[j]['start'], segs[j]['end'])
            if any(_dist(pi, pj) <= tol for pi in pts_i for pj in pts_j):
                union(i, j)

    groups = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(segs[i])

    rings = list(groups.values())
    logger.info(f"split_into_rings: {len(rings)} Ring(e) aus {n} Segmenten erkannt")
    return rings


def choose_euler_start(ring_segs, other_rings_shapely):
    """
    Wählt den Segment-Startpunkt eines Rings, der einem anderen Ring geometrisch
    am nächsten liegt. Bei geschlossenen Ringen ist Start = Ende, daher landet
    der Zapfen am gewählten Punkt – der unter einem Nachbarring verschwindet.

    ring_segs            : sortierte, geschlossene Segmentliste (Output von sort_segments)
    other_rings_shapely  : Liste vorab gebauter Shapely-LinearRings der übrigen Ringe

    Rückgabe: (x, y) – Punkt auf ring_segs. Bei leerer other-Liste:
    der vorhandene erste Startpunkt (No-Op).
    """
    if not other_rings_shapely:
        return ring_segs[0]['start']

    best_pt = ring_segs[0]['start']
    best_dist = float('inf')
    for seg in ring_segs:
        pt = seg['start']
        d = min(other.distance(Point(pt)) for other in other_rings_shapely)
        if d < best_dist:
            best_dist = d
            best_pt = pt
    logger.info(
        f"choose_euler_start: Startpunkt {best_pt} gewählt, "
        f"Abstand zum Nachbarring {best_dist:.4f} mm"
    )
    return best_pt


def rotate_ring_start(ring_segs, target_point, tol=1e-5):
    """
    Rotiert die Segmentliste zyklisch, sodass target_point der neue Startpunkt ist.
    target_point muss ein bestehender Segment-Startpunkt sein (innerhalb tol).
    """
    for i, seg in enumerate(ring_segs):
        if _dist(seg['start'], target_point) <= tol:
            return ring_segs[i:] + ring_segs[:i]
    raise ValueError(
        f"rotate_ring_start: Punkt {target_point} nicht in Ring-Endpunkten "
        f"gefunden (Toleranz {tol} mm)."
    )


def orientation(segs):
    """
    Prüft Konturorientierung via Flächenvorzeichen (Shoelace-Formel).
    Gibt 'CCW' (mathematisch positiv) oder 'CW' zurück.
    """
    points = [seg['start'] for seg in segs]
    n = len(points)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return 'CCW' if area > 0 else 'CW'


def offset_segments(segs, dist, side='left'):
    """
    Versetzt Kontur parallel um dist mm via Shapely LinearRing.

    side='left'  bei CCW-Kontur → nach innen
    side='right' bei CCW-Kontur → nach außen
    """
    # Offset unter Schwellwert → als Null behandeln (numerischer Jitter, kein echter Versatz)
    if abs(dist) < 1e-7:
        return segs

    pts = []
    for seg in segs:
        pts.extend(_seg_to_polyline(seg)[:-1])  # letzten Punkt weglassen (Dopplung)
    if len(pts) < 3:
        return segs

    pts.append(pts[0])  # Ring schließen
    ring = LinearRing(pts)

    # Degenerierte/kollineare Kontur: Polygon-Fläche fast Null → Offset geometrisch sinnlos.
    # LinearRing.area ist in Shapely immer 0, daher Polygon() verwenden.
    if abs(Polygon(pts).area) < 1e-10:
        raise ValueError(
            "offset_segments: Kontur hat nahezu keine Flaeche (degeneriert oder kollinear). "
            "Offset nicht moeglich – Kontur pruefen."
        )

    offset_geom = ring.parallel_offset(dist, side, join_style=2, mitre_limit=5.0)

    # Fix B: Degenerierte Geometrie explizit prüfen statt AttributeError abfangen.
    if offset_geom.is_empty:
        raise ValueError(
            f"offset_segments: Offset-Kontur kollabiert bei {dist:.3f} mm "
            f"(Seite '{side}'). Offset zu groß für diese Geometrie."
        )
    if offset_geom.geom_type not in ('LineString', 'LinearRing'):
        raise ValueError(
            f"offset_segments: Shapely-Offset hat Kontur zerschnitten "
            f"({offset_geom.geom_type}). Offset pro Lage reduzieren oder Kontur prüfen."
        )

    coords = list(offset_geom.coords)

    result = []
    for i in range(len(coords) - 1):
        result.append({'type': 'LINE', 'start': coords[i], 'end': coords[i + 1]})
    return result


def generate_points(segs, z):
    """
    Erzeugt Janome-Punktliste aus einer Segmentliste.

    Rückgabe: Liste von Dicts {'type', 'x', 'y', 'z', 'r'}
    Erster Punkt: CP_S, letzter Punkt: CP_E,
    Zwischenpunkte: CP_P (Linie) oder ARC + CP_P (Bogen).
    """
    points = []
    n = len(segs)

    for i, seg in enumerate(segs):
        is_last = (i == n - 1)

        if i == 0:
            x, y = seg['start']
            points.append({'type': 'CP_S', 'x': x, 'y': y, 'z': z, 'r': 0.0})

        if seg['type'] == 'LINE':
            x, y = seg['end']
            ptype = 'CP_E' if is_last else 'CP_P'
            points.append({'type': ptype, 'x': x, 'y': y, 'z': z, 'r': 0.0})

        elif seg['type'] == 'ARC':
            mid_angle = _arc_mid_angle(seg['start_angle'], seg['end_angle'], seg['ccw'])
            mx, my = _arc_point(seg['center'], seg['radius'], mid_angle)
            points.append({'type': 'ARC', 'x': mx, 'y': my, 'z': z, 'r': 0.0})

            x, y = seg['end']
            ptype = 'CP_E' if is_last else 'CP_P'
            points.append({'type': ptype, 'x': x, 'y': y, 'z': z, 'r': 0.0})

    return points


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------

def points_to_csv(all_layer_points):
    """
    Serialisiert eine Liste von Lagen-Punktlisten als JR C-Points II CSV.
    Format: Typ,X,Y,Z,R (exakt einhalten!)
    """
    output = io.StringIO()
    output.write("Typ,X,Y,Z,R\n")
    for layer_pts in all_layer_points:
        for pt in layer_pts:
            output.write(
                f"{pt['type']},"
                f"{pt['x']:.6f},"
                f"{pt['y']:.6f},"
                f"{pt['z']:.6f},"
                f"{pt['r']:.2f}\n"
            )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Lagenberechnung (Kernlogik)
# ---------------------------------------------------------------------------

def calculate_layers(segs, total_height, needle_id, offset_per_layer,
                     safety_z=0.0, reference_start=None):
    """
    Berechnet alle Lagen und gibt eine Liste von Punktlisten zurück.

    Lagenformel:
      step_height_max = 0.75 × needle_id
      layers = ceil(total_height / step_height_max)
      z_step = total_height / layers
      offset = layer_index × offset_per_layer

    Flip-Parität (top-down): die oberste Lage (zuletzt gefahren) wird nie geflippt,
    damit ihr Start/Endpunkt = gewählter Euler-Startpunkt bleibt. Tiefere Lagen
    alternieren – Boustrophedon bezogen auf die Oberkante, nicht auf Lage 0.

    reference_start : optionaler (x, y)-Punkt. Wenn gesetzt, wird die Offset-Kontur
    jeder Lage zyklisch so rotiert, dass ihr Startpunkt am nächsten an
    reference_start liegt. Notwendig für Multi-Ring Euler-Optimierung, weil
    Shapelys parallel_offset den Startpunkt nicht garantiert bewahrt.

    Nach der letzten Lage wird ein PTP-Punkt auf safety_z (Standard 0.0 mm)
    angehängt, damit die Nadel nach dem Dosieren sicher angehoben wird.
    """
    step_height_max = 0.75 * needle_id
    num_layers = ceil(total_height / step_height_max)
    z_step = total_height / num_layers

    orient = orientation(segs)
    side = 'left' if orient == 'CCW' else 'right'

    all_layer_points = []

    for layer_idx in range(num_layers):
        z = z_step * (layer_idx + 1)
        offset = layer_idx * offset_per_layer

        layer_segs = offset_segments(segs, offset, side) if offset > 0 else segs

        # Offset verschiebt u.U. den Startpunkt – auf reference_start rotieren,
        # damit Euler-Startpunkt auch auf inneren Lagen erhalten bleibt.
        if reference_start is not None and offset > 0 and layer_segs:
            best_i = min(
                range(len(layer_segs)),
                key=lambda k: _dist(layer_segs[k]['start'], reference_start),
            )
            if best_i > 0:
                layer_segs = layer_segs[best_i:] + layer_segs[:best_i]

        # Top-down Flip-Parität: oberste Lage nie geflippt, Boustrophedon von oben.
        dist_from_top = (num_layers - 1) - layer_idx
        if dist_from_top % 2 == 1:
            layer_segs = [_flip_segment(s) for s in reversed(layer_segs)]

        layer_pts = generate_points(layer_segs, z)

        # Fix A: PTP-Lift zwischen den Lagen (Crash-Vermeidung beim Lagenwechsel).
        # Zwei PTPs: erst senkrecht hoch auf safety_z, dann XY zur neuen Startposition.
        if layer_idx > 0 and all_layer_points:
            last_layer = all_layer_points[-1]
            last_pt = last_layer[-1]
            first_pt_new = layer_pts[0]
            last_layer.append({
                'type': 'PTP',
                'x': last_pt['x'],
                'y': last_pt['y'],
                'z': safety_z,
                'r': 0.0,
            })
            last_layer.append({
                'type': 'PTP',
                'x': first_pt_new['x'],
                'y': first_pt_new['y'],
                'z': safety_z,
                'r': 0.0,
            })

        all_layer_points.append(layer_pts)

    # Sicherheitshub: PTP-Punkt auf safety_z nach der letzten Lage
    if all_layer_points and all_layer_points[-1]:
        last_pt = all_layer_points[-1][-1]
        all_layer_points[-1].append({
            'type': 'PTP',
            'x': last_pt['x'],
            'y': last_pt['y'],
            'z': safety_z,
            'r': 0.0,
        })

    return all_layer_points, num_layers, z_step


# ---------------------------------------------------------------------------
# Flask Web-UI
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Janome Mehrlagenspur Generator</title>
  <style>
    :root { --blue: #2980b9; --dark: #2c3e50; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', Arial, sans-serif;
      background: #f0f2f5;
      padding: 40px 20px;
      color: #333;
    }
    .card {
      background: white;
      max-width: 680px;
      margin: 0 auto;
      border-radius: 8px;
      padding: 32px 36px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.1);
    }
    h1 { color: var(--dark); font-size: 1.4em; margin-bottom: 4px; }
    .subtitle { color: #888; font-size: 0.9em; margin-bottom: 24px; }
    .message {
      padding: 13px 16px;
      border-radius: 4px;
      margin-bottom: 20px;
      font-size: 0.92em;
      line-height: 1.7;
    }
    .message.error {
      background: #fdecea;
      border-left: 4px solid #c0392b;
      color: #7b2d2d;
    }
    .message.success {
      background: #eafaf1;
      border-left: 4px solid #27ae60;
      color: #1a5c38;
    }
    hr { border: none; border-top: 1px solid #eee; margin: 20px 0; }
    label {
      display: block;
      margin-top: 16px;
      font-size: 0.88em;
      font-weight: 600;
      color: #555;
    }
    .hint { font-size: 0.8em; color: #aaa; margin-top: 3px; font-weight: normal; }
    input[type=file], input[type=number] {
      display: block;
      width: 100%;
      margin-top: 5px;
      padding: 8px 10px;
      border: 1px solid #d0d5dd;
      border-radius: 4px;
      font-size: 0.95em;
      color: #333;
      background: #fafafa;
    }
    input[type=number] { max-width: 180px; }
    .btn {
      display: inline-block;
      margin-top: 22px;
      padding: 10px 26px;
      background: var(--blue);
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.95em;
      text-decoration: none;
    }
    .btn:hover { background: #1c6ea4; }
    .btn-dl { background: #27ae60; margin-left: 10px; }
    .btn-dl:hover { background: #1e8449; }
    footer { text-align: right; margin-top: 28px; font-size: 0.78em; color: #ccc; }
  </style>
</head>
<body>
<div class="card">
  <h1>Janome Mehrlagenspur Generator</h1>
  <p class="subtitle">DXF (Layer &ldquo;1&rdquo;) oder cp2d &rarr; CSV f&uuml;r JR&nbsp;C-Points&nbsp;II</p>

  {% if error %}
  <div class="message error"><strong>Fehler:</strong> {{ error }}</div>
  {% endif %}

  {% if result %}
  <div class="message success">{{ result | safe }}</div>
  <a href="/download" class="btn btn-dl">&#8627; CSV herunterladen</a>
  <script>setTimeout(function(){ window.location.href = '/download'; }, 700);</script>
  <hr>
  {% endif %}

  <form method="post" enctype="multipart/form-data">
    <label>Konturdatei (DXF oder cp2d)
      <input type="file" name="file" accept=".dxf,.cp2d,.csv,.txt" required>
    </label>

    <label>Gesamt&shy;h&ouml;he [mm]
      <input type="number" name="total_height" step="0.01" min="0.01" value="2.00" required>
    </label>

    <label>Nadel-ID [mm]
      <input type="number" name="needle_id" step="0.01" min="0.1" value="0.58" required>
      <span class="hint">Verf&uuml;gbare Nadeln: {{ needles }}</span>
    </label>

    <label>Offset pro Lage [mm]
      <input type="number" name="offset_per_layer" step="0.01" min="0" value="0.50" required>
    </label>

    <button type="submit" class="btn">Berechnen</button>
  </form>

  <footer>v{{ version }}</footer>
</div>
</body>
</html>
"""


@app.route('/', methods=['GET', 'POST'])
def index():
    global _last_csv
    needles_str = " / ".join(str(n) for n in NEEDLE_IDS)

    if request.method == 'GET':
        return render_template_string(
            HTML_TEMPLATE, error=None, result=None,
            needles=needles_str, version=VERSION
        )

    # --- Datei-Upload ---
    file = request.files.get('file')
    if not file or not file.filename:
        return render_template_string(
            HTML_TEMPLATE, error="Keine Datei ausgewählt.",
            result=None, needles=needles_str, version=VERSION
        )

    filename = os.path.basename(file.filename)  # Path-Traversal-Schutz
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    # --- Parameter einlesen ---
    try:
        total_height = float(request.form['total_height'])
        needle_id = float(request.form['needle_id'])
        offset_per_layer = float(request.form['offset_per_layer'])
    except (KeyError, ValueError) as e:
        return render_template_string(
            HTML_TEMPLATE, error=f"Ungültiger Parameter: {e}",
            result=None, needles=needles_str, version=VERSION
        )

    if needle_id not in NEEDLE_IDS:
        needle_id = select_needle(needle_id)

    # --- Datei parsen ---
    try:
        ext = os.path.splitext(filename)[1].lower()
        circle_count = 0
        if ext == '.dxf':
            segs = read_dxf_contour(save_path)
        else:
            segs, circle_count = parse_cp2d(save_path)
    except Exception as e:
        logger.exception("Fehler beim Parsen")
        return render_template_string(
            HTML_TEMPLATE,
            error=f"Datei konnte nicht eingelesen werden: {e}",
            result=None, needles=needles_str, version=VERSION
        )

    if not segs:
        return render_template_string(
            HTML_TEMPLATE,
            error="Keine Segmente in der Datei gefunden. "
                  "Bei DXF: Ist die Kontur im Layer \"1\" gezeichnet?",
            result=None, needles=needles_str, version=VERSION
        )

    # --- Ringe erkennen und einzeln zu geschlossenen Konturen sortieren ---
    try:
        rings = split_into_rings(segs)
        rings = [sort_segments(r) for r in rings]
    except Exception as e:
        logger.exception("Fehler bei Ring-Erkennung/Sortierung")
        return render_template_string(
            HTML_TEMPLATE,
            error=f"Fehler bei der Kontur-Analyse: {e}",
            result=None, needles=needles_str, version=VERSION
        )

    # --- Euler-Startpunkt-Optimierung (nur bei >1 Ring) ---
    # Für jeden Ring den Startpunkt auf den dem Nachbarring nächsten Knoten legen,
    # damit der Zapfen beim Überfahren durch eine andere Spur plattgedrückt wird.
    if len(rings) > 1:
        ring_shapes = [_ring_to_shapely(r) for r in rings]
        for i, ring in enumerate(rings):
            others = [ring_shapes[j] for j in range(len(rings)) if j != i]
            target = choose_euler_start(ring, others)
            rings[i] = rotate_ring_start(ring, target)

    # --- Segment-Vorschau aufbauen ---
    lines_total = sum(sum(1 for s in ring if s['type'] == 'LINE') for ring in rings)
    arcs_total = sum(sum(1 for s in ring if s['type'] == 'ARC') for ring in rings)
    seg_count = sum(len(r) for r in rings)
    ring_info = f"{len(rings)} Ringe" if len(rings) > 1 else "1 Ring"
    seg_summary = (
        f"{seg_count} Segmente ({ring_info}, "
        f"{lines_total} Linien, {arcs_total} Bögen"
    )
    if circle_count:
        seg_summary += f", {circle_count} Vollkreise"
    seg_summary += ")"

    # --- Lagen berechnen (Strategie A: Ring-weise – alle Lagen Ring 1, dann Ring 2) ---
    try:
        all_layer_points = []
        num_layers = 0
        z_step = 0.0
        for idx, ring in enumerate(rings):
            ref_start = ring[0]['start'] if len(rings) > 1 else None
            ring_layers, ring_num_layers, ring_z_step = calculate_layers(
                ring, total_height, needle_id, offset_per_layer,
                reference_start=ref_start,
            )
            # PTP-Übergang beim Ringwechsel: XY zur neuen Startposition auf safety_z.
            # Das Sicherheitshub-PTP am Ende des Vorgängerrings liegt bereits an.
            if idx > 0 and all_layer_points:
                first_new = ring_layers[0][0]
                all_layer_points[-1].append({
                    'type': 'PTP',
                    'x': first_new['x'],
                    'y': first_new['y'],
                    'z': 0.0,
                    'r': 0.0,
                })
            all_layer_points.extend(ring_layers)
            num_layers = ring_num_layers
            z_step = ring_z_step
        csv_content = points_to_csv(all_layer_points)
    except Exception as e:
        logger.exception("Fehler bei der Lagenberechnung")
        return render_template_string(
            HTML_TEMPLATE,
            error=f"Fehler bei der Lagenberechnung: {e}",
            result=None, needles=needles_str, version=VERSION
        )

    total_pts = sum(len(pts) for pts in all_layer_points)
    layers_info = (
        f"{num_layers} Lagen &times; {len(rings)} Ringe"
        if len(rings) > 1 else f"{num_layers} Lagen"
    )
    result_msg = (
        f"\u2705 {seg_summary}<br>"
        f"{layers_info} &middot; "
        f"Z-Schritt {z_step:.2f}&nbsp;mm &middot; "
        f"Nadel-ID {needle_id}&nbsp;mm &middot; "
        f"{total_pts} Punkte &mdash; Download startet automatisch."
    )

    _last_csv = csv_content
    return render_template_string(
        HTML_TEMPLATE, error=None, result=result_msg,
        needles=needles_str, version=VERSION
    )


@app.route('/download')
def download():
    """Liefert das zuletzt berechnete CSV zur direkten Ausführung."""
    if _last_csv is None:
        return "Kein Ergebnis vorhanden. Bitte zuerst eine Datei berechnen.", 404
    buf = io.BytesIO(_last_csv.encode('utf-8'))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name='Lagenprogramm.csv',
        mimetype='text/csv',
    )


# ---------------------------------------------------------------------------
# Direktstart
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # Browser nach 1 Sekunde öffnen – wartet, bis Flask gestartet ist
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host='127.0.0.1', port=5000, debug=False)
