# Eval dataset — how the ground truth was built (Oct 26, 2026)

Source (pre-event background, disclosed): the operator's Week-1 shadow pilot
(Oct 7–14, 2026). The pilot system scored guest messages with a fine-grained
category plus confidence-based action bands; the operator then hand-reviewed
every decision as correct, unsure, or wrong. 109 reviews, 107 unique messages.

Redaction (before any eval use): guest names replaced with stable pseudonyms,
sender names in quoted headers replaced, addresses and identifiers removed,
message/account IDs dropped, properties pseudonymized. Verification scan:
zero residual emails, phone numbers, or full names. The mapping file is
private and is never published; the dataset itself is NOT in this repo.

Label derivation (this is the part to argue with):
Only messages the operator graded "correct" carry a label (89 of 107); the
7 "wrong" and 11 "unsure" are held out for operator re-labeling. The pilot's
fine-grained category maps onto this build's three-way vocabulary:
- Ignore: spam_or_noise
- Act: urgent_maintenance, checkin_checkout_access,
  booking_change_cancellation, complaint_review_risk
- Confirm: booking_inquiry, house_rules_question, owner_or_personal
Resulting label spread: Confirm 33, Act 31, Ignore 25.

Update Oct 27, 2026: the operator re-labeled all 18 held-out rows with his
own Confirm / Act / Ignore calls. Those rows now carry ground truth from
the regrade (marked human_verdict=relabeled in the dataset file), bringing
the scored set to n=107: Confirm 39, Act 32, Ignore 36. The final eval ran
on this 107-row set with the shipped prompt (n=101 scored, 6 few-shot
examples excluded): overall accuracy 80.2%, decisive accuracy 82.3%,
high-confidence accuracy 79.5%. The README numbers are from this run.

Known limits: n=89 (now 107) is small; labels inherit the pilot's category definitions;
message previews are truncated by the source system. Every reported number
carries its n, and the confidence ledger treats this set as a floor, not a
ceiling: live decisions during the event add fresh labeled rows daily.
