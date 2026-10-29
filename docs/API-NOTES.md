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

## Image input (verified live 2026-10-27)
Responses endpoint accepts image input as Responses-native content parts:
{"type": "input_text", "text": ...} and {"type": "input_image", "image_url": "data:<media>;base64,..."} (image_url is a plain string).
The chat-completions shape ({"type": "image_url", "image_url": {"url": ...}}) returns HTTP 400 "input[1].content did not match any supported type".
Verified: muse-spark-1.3 classified a test image and the strict parser accepted the result (~$0.007/call at test size).

## Baseten: Muse Spark Open (verified live 2026-10-28)
Serves the `open_escalate` eval arm's first pass (client: src/jev/baseten_client.py).
- Base URL: https://inference.baseten.co/v1 (OpenAI-compatible); env BASETEN_API_BASE_URL
- Endpoint: POST /chat/completions with {"model", "messages": [system, user], "max_tokens"};
  auth Authorization: Bearer <key>; env BASETEN_API_KEY
- Model name, exactly: meta-models/muse-spark-open-1026 (env BASETEN_MODEL_OPEN). The
  hackathon key is restricted to this one model.
- The model always reasons before answering, and reasoning tokens may be billed inside
  completion_tokens, so the client asks for max_tokens 6000 with a 120 s timeout (raised
  from 2000 after 12 of 101 eval messages spent the whole 2000-token budget reasoning and
  returned empty content). A tiny
  max_tokens=60 test call returned HTTP 200.
- Response translation: choices[0].message.content becomes "output_text" and usage
  {prompt_tokens, completion_tokens} becomes {input_tokens, output_tokens}, the Responses
  shape parse.py already reads. Missing usage is omitted (usage_cost then estimates). A null
  content (all tokens spent reasoning) becomes "", which parses to no decision (fail-safe).
  Transport errors, timeouts, non-200s, and bodies with no choices[0].message return None.
- Cost: usage is metered against a $100 hackathon credit, not a published per-token price.
  parse.usage_cost prices it at parity with muse-spark-1.3 ($1.25 input / $4.25 output per
  1M tokens). That is an at-par ESTIMATE; reported costs for this tier are not invoices.
