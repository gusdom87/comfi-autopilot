# Design spec v2 (Step 4, redo) — the MIX. Gus's pick, Oct 28, 2026
Supersedes spec.md where they conflict. References: redo-gemini-pro.jpg
(the LOOK) and redo-chatgpt.png (the STRUCTURE). Spec wins over both
pictures on any conflict. Must-survive list in current-state.md is
unchanged and binding; the wordmark lockup with golden dots on both
i's (commit cf1b85d) carries over exactly.

## The look (from the Gemini take)
- Deeper pressed depth than v1: inbox rows and cards sit in visible
  bevelled frames; the detail/message area is a debossed tray (inset
  well clearly deeper than a card shadow); the draft sits in its own
  inset well inside the tray.
- Paper grain more present (still legibility-safe), warm beige-cream
  ground #F7F3EB, navy ink #101E3C, amber #C7924B, tan #D9B07A.
- Editorial numbered section heads in Gabarito, uppercase, with full
  stops: "01. TRIAGE." "02. PLAYS." "03. CAMERAS." (our real sections).
- The plays render as a ruled spec table: hairline rules between rows,
  number column (01-06), play name, one-line description, status word.
  No invented run counts or timestamps — the status column carries the
  play's real gate state wording only.

## The structure (from the ChatGPT take)
- Three zones on desktop: navy rail (wordmark, Triage/Plays/Cameras
  anchors, P1 block at the foot) / inbox column / detail column. Phone:
  one column, same order (bar, P1 strip, triage, plays, cameras).
- Triage result organized like ChatGPT's detail panel: property line at
  top, a full-width navy decision band (category + confidence), then
  draft well on the left and the facts (route, reason, send right) as a
  quiet spec list on the right. All existing fields, none added.
- Inbox rows richer: property name, the example's subject line, preview,
  badge, confidence — using only the staged examples already on the page.

## Trims (both pictures invent these; none ship)
No search bar, no greeting header, no extra nav modules (Calendar,
Properties, Reports, Settings), no property photos, no invented stats,
run counts, "Live" chips, or guest names beyond the staged fixtures,
and no "Approve & send" button: gate and send wording stays exactly as
the current build words it.
