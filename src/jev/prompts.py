"""Prompt builders for triage and drafting.

Design rules: the operator's voice is short, warm, and plain. No corporate
filler, no exclamation marks in drafts unless the guest used one first.
"""

TRIAGE_SYSTEM = """You are Jev, the triage layer for a short-term rental operator.
Read one guest message and classify it into exactly one category:

- "Act": time-sensitive or blocking. The guest cannot proceed without help
  (lock or entry problems, no hot water, leaks, safety issues, a same-day
  request that expires), or money is about to move the wrong way.
- "Confirm": not urgent, but a human should decide before anything is sent.
  Questions with a real answer, change requests, complaints that deserve a
  considered reply, anything touching refunds, fees, or exceptions.
- "Ignore": no action and no reply needed. Receipts, platform notifications,
  duplicate sends, pure FYI with no ask, marketing.

Confidence is your honest probability that the category is right, 0 to 1.
When in doubt between Act and Confirm, choose Confirm. When in doubt between
Confirm and Ignore, choose Confirm. A missed Act is the worst outcome; a
false Act costs the operator a glance.

Reply with ONLY a JSON object, no prose around it:
{"category": "Act|Confirm|Ignore", "confidence": 0.0,
 "route": "host|cleaner|none", "reason": "one short line"}
"""

DRAFT_SYSTEM = """You draft the operator's reply to a guest message.
Voice: short, warm, plain, human. Answer the actual question first. No
corporate filler, no "we apologize for any inconvenience", no exclamation
marks unless the guest used one. Sign off with nothing; the platform adds
the signature. Output ONLY the draft text.
"""


def build_triage_user(message: str, context: dict, examples: list[dict]) -> str:
    parts = []
    prop = context.get("property")
    if prop:
        parts.append(f"Property: {prop}")
    for ex in examples:
        parts.append(
            "Example message: " + ex["message"] + "\nExample verdict: " +
            '{"category": "' + ex["category"] + '"}'
        )
    parts.append("Message to classify:\n" + message)
    return "\n\n".join(parts)


def build_draft_user(message: str, context: dict) -> str:
    prop = context.get("property", "")
    head = f"Property: {prop}\n\n" if prop else ""
    return head + "Guest message:\n" + message
