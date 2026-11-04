# Eval dataset — how the ground truth was built (Oct 26, 2026)

Source (pre-event background, disclosed): the operator's own guest
messages and his grading of them (the week of Oct 7–14, 2026). He graded
guest messages (drawn from the
operator's message history, Jan 22 to Oct 9, 2026) with a fine-grained
category plus confidence-based action bands; the operator then hand-reviewed
every decision as correct, unsure, or wrong. 109 reviews, 107 unique messages.

Redaction (before any eval use): guest names replaced with stable pseudonyms,
sender names in quoted headers replaced, street addresses replaced with an
[ADDRESS] token, identifiers removed, message/account IDs dropped,
properties pseudonymized. Verification scan: zero residual emails, phone
numbers, or full names. Three rows kept a city, state, and ZIP after the
[ADDRESS] token until Oct 29, 2026, when the address redaction was
completed. The eval runs of record were measured on the file before that
completion; the only difference is those three location strings. The
mapping file is private and is never published; the dataset itself is NOT
in this repo.

Label derivation (this is the part to argue with):
Only messages the operator graded "correct" carry a label (89 of 107); the
7 "wrong" and 11 "unsure" are held out for operator re-labeling. Those
fine-grained category maps onto this build's three-way vocabulary:
- Ignore: spam_or_noise
- Act: urgent_maintenance, checkin_checkout_access,
  booking_change_cancellation, complaint_review_risk
- Confirm: booking_inquiry, house_rules_question, owner_or_personal
Resulting label spread: Confirm 33, Act 31, Ignore 25.

Update Oct 27, 2026: the operator re-labeled all 18 held-out rows with his
own Confirm / Act / Ignore calls. Those rows now carry ground truth from
the regrade (marked human_verdict=relabeled in the dataset file), bringing
the scored set to n=107: Confirm 39, Act 32, Ignore 36.

Note on counts: the roughly 300 messages the operator hand-labeled are a
separate teaching set, not the source of the eval set — only 2 of the 107
eval messages appear in it. The 109 graded reviews come from those
graded record above, and the 107-row final eval set is drawn from those
reviews. The final eval ran
on this 107-row set with the shipped prompt (n=101 scored, 6 few-shot
examples excluded): overall accuracy 80.2%, decisive accuracy 82.3%,
high-confidence accuracy 79.5% (run final-107-live), the model's raw record
without the guard. The shipped engine always applies the stay-change/money
guard; its run on the same set (final-107-guard) scored 77.2% overall with
zero Confirm-to-Act errors, and the README leads with it.

Known limits: n=89 (now 107) is small; labels inherit the category definitions he set then;
message previews are truncated by the source system. Every reported number
carries its n, and the confidence ledger treats this set as a floor, not a
ceiling. Beyond this set, a log-only shadow mode scores live inbound guest
messages and never sends. Its decisions are labeled only when reviewed,
by the operator or by one of the operator's standing rules. Its record is
shown on the dashboard as a live record, not claimed as an eval result.
