# Meta Model API — integration notes (pinned Oct 26, 2026)
Source: official quickstart as published at dev.meta.ai, cross-checked against
three independent write-ups of the same docs. TO VERIFY on the first live call.

- Base URL: https://api.meta.ai/v1 (OpenAI-SDK compatible)
- Endpoint: POST /v1/responses (Responses API; Chat Completions and Messages
  formats also offered at the same price)
- Auth: Authorization: Bearer <key>; quickstart env var name MODEL_API_KEY
- Model IDs (Standard mode, per million tokens):
  - muse-spark-1.3: $1.25 input / $4.25 output (cached input $0.15)
  - muse-wonder-1.0: $4.00 input / $20.00 output (cached input $0.20)
- Rate limits per team: Wonder 3,000 req/min, 4M tok/min (ample for our eval)
- Account flow (IMPORTANT): "Create API key" stays disabled until a payment
  method is on file under Billing. Matches the hackathon FAQ: a card is
  required to use the credits. Key creation is therefore gated on BOTH the
  $150 credits posting AND the card decision.
- Contributor-mode IDs (muse-spark-1.3-contributor) belong to Muse Code
  subscriptions; the eval uses Standard IDs only.
