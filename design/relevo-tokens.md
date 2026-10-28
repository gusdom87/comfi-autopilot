# Relevo & Vinco — design reference (extracted Oct 28, 2026 from site CSS + visual read)

Direction set by Gus Oct 28: this site's aesthetic, Comfi brand colors,
changes allowed where they fit, aesthetic preserved.

## Their tokens (from the stylesheet)
- Fonts: --font-1 "Gabarito" (display), --font-2 "Instrument Sans" (text). Both Google Fonts.
- Their palette: tan #d4b08f, dark brown #36261a, cream #f7e8d2, mid browns #6b4c34 / #47331f, near-black brown #1f140b.
- Radii: --radius-1 6px (chips, small panels), large panels fluid clamp(18px, 2.4vw, 34px), circles 50%. Nothing pill-shaped in layout panels.
- Shadows: layered "pressed board" stacks — a 1px light offset on one side, 1px dark offset on the other, plus soft ambient (e.g. 12px 17px 22px at low alpha). Inset variants for debossed wells.
- Hero type is fluid and huge (--hero-cap 22.5vw), uppercase, condensed feel, full stops as graphic punctuation ("Stand out. Press in.").
- Background carries a fine speckled paper grain.
- Layout: airy, wide gutters, editorial numbered sections, spec-table rows (label / value) for details, dark panels with light text for emphasis zones. Accent is tonal (burnished board), never a bright hue.

## Translation to Comfi (locked by Gus's brief)
- Ink: Comfi deep navy (brand navy, #101E3C family) replaces dark brown everywhere, including shadow tints.
- Accent: warm tan/amber (#C7924B, #D9B07A) replaces their tan/burnish tones.
- Ground: brand cream (#F7F3EB family) with the same paper grain.
- Keep: Gabarito + Instrument Sans, pressed/debossed depth, oversized uppercase display lines with full stops, small radii, airy editorial spacing, dark navy panels with cream text for emphasis, spec-row detail tables.
- Product semantics stay ours (Confirm = hold for the operator, Act = urgent, Ignore = no action; autonomy P1 approve-first). Only the look transfers.
