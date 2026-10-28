# Design spec (Step 4) — APPROVED by Gus Oct 28, 2026 — ComfiAutopilot console, Relevo direction
Status: contract for the design build (branch design/relevo-skin).
Reference: mockup-desktop-relevo.png, mockup-phone-relevo.png,
relevo-tokens.md. On any conflict, THIS spec wins over the pictures.

## Tokens
- Fonts: display "Gabarito", fallback "Arial", sans-serif; text
  "Instrument Sans", fallback system-ui, sans-serif. Loaded from Google
  Fonts with display=swap; the page must remain fully usable if the font
  CDN is unreachable (fallbacks carry the layout).
- Palette:
  - ink / primary navy: #101E3C
  - navy panel (rail, ACT badge, ACT panel): #0E1A2B to #101E3C range; use #101E3C
  - ground cream: #F7F3EB; panel cream (cards): #FFFDF8; inset well: #F1EAD9 family
  - amber accent (CONFIRM badge, primary button, wordmark dot): #C7924B
  - tan (IGNORE badge, quiet accents, hairlines): #D9B07A; border neutral #D9D4C7
  - success green (completed states only): #2F7D5B
  - danger (alerts only): #B24A3A
  - muted text: #5B6472 on cream; cream text on navy: #F7F3EB
- Paper grain: subtle repeating radial speckle via CSS only (no image
  asset), at very low contrast; must not reduce text legibility.
- Radii: 6px cards/chips/buttons; 14px large panels; 50% avatar/count dots.
- Pressed depth (the signature): cards get the layered stack —
  box-shadow: inset 0 1px 0 rgba(247,243,235,.8), 1px 1px 0 rgba(16,30,60,.10),
  3px 5px 6px rgba(16,30,60,.08), 12px 18px 28px rgba(16,30,60,.10);
  Wells (draft panel, message body) get the inset deboss stack —
  inset 4px 6px 10px rgba(16,30,60,.14), inset 1px 1px 2px rgba(16,30,60,.12),
  inset -1px -1px 0 rgba(247,243,235,.85). Navy panels invert: cream text,
  amber reserved for the one primary action.
- Type scale: display headlines Gabarito 700 uppercase, tight letter
  spacing, full stop at the end ("INBOX.", "OPERATION PLAYS."); H1 40px
  desktop / 30px phone; section heads 24/20; body 16/24 Instrument Sans;
  small 14; caption 12 uppercase tracked for labels ("DRAFTED REPLY").

## Layout (adapts the single-page sandbox; no new modules)
- Desktop (>=1024px): three zones — left navy rail (wordmark, anchor
  index of the page's real sections: Triage, Plays, Cameras, plus the P1
  status block at its foot), inbox column (the staged triage examples as
  pressed rows with badge + confidence), detail column (selected example:
  message, decision panel, draft well, gate state). Plays render as the
  numbered spec strip (01-06) under the console; camera scenarios as
  pressed cards under it. Disclosures sit in the footer, verbatim.
- Phone (<720px): single column in the same order — header, P1 strip,
  triage, plays strip as stacked rows, cameras, disclosures. The primary
  action of a run stays visible without horizontal scroll.
- The current page's sections and controls map 1:1; nothing is added that
  the backend does not have, nothing existing is removed.

## Semantics (ours, not the mockup's inventions)
- Badges: ACT navy bg/cream text; CONFIRM amber bg/navy text; IGNORE tan
  bg/navy text at reduced emphasis. Gate states keep their exact current
  wording (held at P1, simulated, drafted) — style changes, words do not.
- No property photos, no search bar, no "View in Hospitable", no nav
  modules beyond the page's own sections. Example names stay the invented
  fixture names.

## Must-survive (from design/current-state.md, unchanged)
POST /triage round trip and all result fields; property picker values;
every play button wiring (POST /play, POST /event, GET /run polling) and
timeline states; gate/draft/simulated labels exactly as worded; camera
scenario buttons; the disclosure texts unaltered; single-file page +
stdlib server, no build step. Screenshot vs mockup at 1440 and 390 after
each screen; the full test suite green before the branch is proposed.
