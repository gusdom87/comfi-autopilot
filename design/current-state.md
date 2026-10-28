# Comfi Autopilot — current state inventory (Design Step 1, Oct 27, 2026)

Reflects the committed build `31471eb`. One page exists today: the judge
sandbox (`sandbox/index.html`, served by `sandbox/server.py`, no build
step). There are no separate inbox / detail / dispatch screens yet; the
page's sections are the screens this design pass covers.

## Screens (sections of the one page)

- **Header + intro**: product name, what it is, the staged-world and
  pre-existing-work disclosure. Sets every claim the demo is allowed to make.
- **Triage**: staged example chips, a property picker, a message box, a
  Triage button, and a result card (category, confidence bar, draft reply,
  route, reason, tier path / cost / send-right line).
- **Plays**: a staged-world disclosure box, then one section per play:
  early check-in (property + date pickers, message box, run button,
  timeline, cleaner-done event button), cleaner-done (two scenario
  buttons, timeline), and the camera plays (scenario buttons, timeline).

## Shared pieces

Chips (example messages), big play buttons, native selects and textareas,
the result card, the confidence bar, run timelines (run header + steps
with state classes: done, held, waiting, failed, skipped, pending), kind
badges (action / wait), the approval gate box, the draft block, the staged
disclosure box, fine-print meta lines, error text.

## Colors and fonts in use today

Font: `system-ui, sans-serif` everywhere (browser defaults for headings).
Layout: single column, max-width 760px. Colors: card border #ccc, radius
8px; confidence track #eee, fill #2a7 (green); amber #d9a62e for the
staged box, gates and held steps (backgrounds #fff7e6 / #fff3d1 / #fffdf4,
held text #9a6d00); waiting blue #36c (background #f4f8ff, badge #d6e6ff);
failed red #c33 (background #fff5f5, error text #b00); neutrals #ddd, #bbb,
#f7f7f7, #fbfbfb, #e8e8e8, #555, #666. No brand palette, no webfont, no
logo: this is the undesigned state the design pass replaces.

## Must survive (wired to live behavior; cannot break in the restyle)

- `POST /triage` round trip and every result field rendering (category,
  confidence, draft, route, reason, tier path, cost, send right).
- The property picker values (fixture property ids the plays depend on).
- Every play button's wiring (`POST /play`, `POST /event`, `GET /run`
  polling) and every timeline state rendering.
- The gate, draft, and simulated labels exactly as worded (claim
  discipline: nothing may look sent or charged when it is held/simulated).
- The camera scenario buttons and their outputs.
- The staged-world and pre-existing-work disclosure text, unaltered.
- Single-file page + stdlib server, no build step.

## Screenshots (design/screenshots/)

- `current-desktop-triage.png` — 1440px, triage with a live Act result.
- `current-desktop-play.png` — 1440px, cleaner-done timeline after a run.
- `current-phone-triage.png` — 390px, triage with a live Act result.
- `current-phone-full.png` — 390px, full page.

Refresh this file at the Nov 1 freeze: camera batch 3 lands after this
inventory, and the skin build (Design Step 5) works from `design/spec.md`.
