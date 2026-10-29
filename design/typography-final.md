# Typography, final (approved by Gus, Oct 29, 2026)

- **Family:** Libre Franklin, single-family system. Display and text both.
  Display treatment per the font sheet Gus picked (Round 1, cell A):
  weight 900 for wordmark, masthead, section titles, category and
  confidence numerals; natural tracking (0); section titles 52px desktop.
- **Masthead:** the wordmark sits on a navy plaque (cream letters, amber
  tittles and trailing dot), the sheet treatment Gus pointed at.
- **Self-hosted, always:** the 12 static faces (400-900, latin +
  latin-ext; sources in design/fonts/) are embedded in sandbox/index.html
  as data URIs. Reason: the sandbox browser could not reach Google
  Fonts, so CDN-linked previews silently rendered a fallback face while
  reporting success. Embedded faces render identically on any machine,
  including a judge's. Verify a webfont by canvas measureText against a
  static-file render, never by document.fonts.check alone.
- Supersedes the Gabarito + Instrument Sans pairing in spec-v2.md and
  the Big Shoulders + IBM Plex Sans pairing tried Oct 29 (commit 9b005a7).
