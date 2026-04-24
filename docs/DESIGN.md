# Zerberus Design System

**Status:** Patch 151 (L-001) — eingeführt am 2026-04-24

## Leitregel

Wenn eine Designentscheidung für ein UI-Element getroffen wird, gilt sie
projektübergreifend für **alle ähnlichen Elemente** in Nala **und** Hel.
Vor einer CSS-/UI-Änderung erst bestehende Patterns prüfen. Keine neuen
Patterns erfinden, wenn es schon ein bestehendes gibt.

## Design-Tokens

Zentrale Datei: [`zerberus/static/css/shared-design.css`](../zerberus/static/css/shared-design.css).

Wird von **beiden** Apps geladen:

- `zerberus/app/routers/nala.py` → `<link rel="stylesheet" href="/static/css/shared-design.css">`
- `zerberus/app/routers/hel.py`  → identischer Link im `<head>`

Enthält Custom-Properties für:

| Kategorie   | Prefix            | Beispiele                                  |
|-------------|-------------------|--------------------------------------------|
| Farben      | `--zb-*`          | `--zb-primary`, `--zb-danger`, `--zb-border` |
| Dark-Theme  | `--zb-dark-*`     | `--zb-dark-bg-primary`                     |
| Spacing     | `--zb-space-*`    | `xs=4px`, `sm=8px`, `md=16px`, `lg=24px`   |
| Radius      | `--zb-radius-*`   | `sm=4px`, `md=8px`, `lg=16px`, `pill=999px` |
| Schatten    | `--zb-shadow-*`   | `sm`, `md`, `lg`                           |
| Typography  | `--zb-font-*`     | `family`, `size-sm/md/lg`                  |
| Touch       | `--zb-touch-min`  | `44px` (minimum)                           |

Neue Token gehören in `shared-design.css`, nicht in lokale Stylesheets.

## Touch-Targets

**Minimum 44×44 px** für alle klickbaren Elemente. Das ist der Apple HIG
/ Material Accessibility-Standard und deckt 95 % der Daumen-Größen.

Die `shared-design.css` setzt automatisch `min-height: var(--zb-touch-min)`
auf `button`, `select`, `input[type="range"]`, `input[type="checkbox"]`,
`input[type="radio"]` — aber nur unter `@media (hover: none) and (pointer: coarse)`,
damit Desktop-Layouts nicht unnötig wachsen.

## Konsistenz-Checkliste

Vor jeder UI-Änderung:

1. **Gibt es das gleiche Element auch anderswo?** (Button, Dropdown,
   Slider, Toggle, Card). Wenn ja: gleicher Style.
2. **Mobile-first?** `:active` statt `:hover`, Touch-Targets ≥ 44 px.
3. **Dark-Theme-sicher?** Bei Bubble-/Panel-Hintergründen an Text-Kontrast
   denken (Patch 140 Auto-Kontrast greift bei Nala-Bubbles automatisch).
4. **Neue Farbe?** Erst prüfen, ob ein existierender Token passt.

## Gemeinsame Klassen

| Klasse               | Zweck                                         |
|----------------------|-----------------------------------------------|
| `.zb-btn`            | Standard-Button — Padding + Radius + Touch    |
| `.zb-btn-primary`    | Hervorgehoben (Primär-Aktion)                 |
| `.zb-btn-danger`     | Zerstörende Aktion (Löschen, Abmelden)        |
| `.zb-btn-ghost`      | Transparenter Button (sekundär)               |
| `.zb-select`         | Einheitlicher Dropdown-Stil                   |
| `.zb-slider`         | Range-Input mit Touch-Mindesthöhe             |
| `.zb-toggle`         | Ein-/Aus-Schalter                             |

## Legacy-CSS

Die bestehenden inline-Styles in `nala.py` und `hel.py` werden schrittweise
auf die Tokens migriert. Neue Code-Stellen sollen sofort die Tokens nutzen.

## Weiterführende Lessons

Siehe [`lessons.md`](../lessons.md) → Abschnitt *"Design-Konsistenz-Regel
(L-001, Patch 151)"* für die ausführliche Begründung und den Verweis auf
die Touch-Target-Prüfung durch Loki.
