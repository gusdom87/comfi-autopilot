# Fable audit: Quo comms floor (branch `quo-comms`, commit 2768f27)

Audited Thu Oct 29, 2026 (evening CT) by Claude Fable 5.1 at High effort,
adversarially, against design-package-final-2026-10-29.md (1b, 1c, 1d,
2.6 floor, L7, L10, Appendix B and C) and design-additions-audited
(2.1, 2.3, 2.7). The code was not modified. Every probe ran from
scratch copies under /tmp; the only writes into the worktree are this
file and pytest's cache.

## Verdict

**Merge-ready after named fixes.** Six fixes (F1 to F6) go in before the
Mon Nov 2 merge. They are small, each is specified below with the file
and line, and each needs one test. Four more (F7 to F10) are required
before any live crew transport exists and do not block the merge. Two
report corrections (F11, F12) and one out-of-scope observation (F13).

The safety claims that matter hold: nothing in this branch can text
anyone but the single configured operator number, and only with all of
`--send`, `a2p_confirmed: true` (JSON true only), a valid E.164 operator
number that is not the sending line and not a crew number, and an
existing CLI path. The crew path cannot reach a network in this
codebase. The 12 L7 operations raise, and everything else is denied by
default. The two digest bugs (F1, F2) are about the digest telling the
truth, which is the beat ("a held queue that is never silent"), so they
are fixed before the merge rather than after.

## Evidence base

| Check | Result |
|---|---|
| Full suite on the worktree | **558 passed** (2.6 s) |
| Baseline at the branch point 5f62011 (git archive to /tmp, run there) | 414 passed |
| New tests collected in the three comms test files | 144 |
| Files changed by the branch commit | 18 added, 0 modified (verified by `git diff --stat 5f62011 2768f27`) |
| Independent mutation pass (51 gate-breaking mutations in scratch copies, script at /tmp/fable-audit/qc_mutate.py) | 51/51 turned the comms tests red |
| Build-only smoke run on the real shadow ledger, `--no-advance`, out_dir under /tmp (mode 700, files 600), then deleted | exit 0; 2 own decisions in the 24 h window (Confirm 1, Ignore 1), 0 waiting, 1 client row left out, 0 no-ID, 0 unreadable; `--send` on the same config exit 3 with the 4 expected blockers and no transport built |
| Sources untouched by the smoke run | Yes, by mtime: ledger.jsonl last written 21:41Z, events.jsonl 00:37:50Z (a concurrent session's `allowlist_confirmed`), both before the 00:45:15Z run |
| Repo hygiene (L10): real property IDs, Lockport numbers, worker or guest names, reservation codes in the new files | None found. The only personal name in `src/jev/comms` is the operator's own, in the "never signed as Gus" lint |
| Comms import graph | Reaches no Hospitable client and no network library. The one process-boundary call is `subprocess.run` in `QuoCliOperatorTransport.send` |

Probe scripts: /tmp/fable-audit/qc_probe_digest.py, qc_probe_crew.py,
qc_mutate.py. They are reproducible against the worktree as-is.

## Item 1. Digest correctness and privacy: **FAIL (two correctness bugs); privacy PASS**

**Mixing.** PASS. Own-listing sections are filtered by exact property
ID at every point: `new`, `skipped`, `waiting`, `waiting_old`, `sends`,
`trips` (derived from `new`). Client rows never appear in text, SMS,
payload, or log unless `include_client_properties` is true, and then
only in their own section, out of every count. Verified on the fixture,
on an impostor row with an own listing's name and a client ID, on a
client row in the actuator queue, and on the real ledger (1 client row
left out, 0 in the body). One gap, low: `events.jsonl` rows (auto-disarm
and lane events) carry no property filter, so an auto-disarm reason
lands in the digest and the SMS whatever property it mentions. Today
the actuator only runs on the allowlist, so this cannot carry a client
listing's text; noted, not a fix.

**Guest text.** PASS. Guest words appear only in the private digest
`.txt` ("Guest wrote:", clipped to 400 chars, mode 600, outside any git
tree). They are absent from the SMS, the payload JSON, `digest-log.jsonl`
(counts only), and stderr. The SMS carries property names, the engine's
draft (clipped to 70) or reason, and counts. Confirmed with marker
strings.

**"Waiting on you" exactly the held items with drafts.** FAIL on two
counts, both confirmed with probes:

- **F1 (required before merge). Shadow rows the shadow has already
  graded `no_reply_24h` vanish from the digest.** `digest.py:292`
  treats any truthy `host_outcome` as "answered". The live shadow
  writes exactly two values, `host_replied` and `no_reply_24h`
  (shadow_live.py:815-818, and the real ledger holds 23 and 5 of them).
  A held Act or Confirm the host never answered is therefore neither
  "waiting" nor counted in "older than 24 hours with no recorded
  answer": it is dropped silently, the one thing the docstring (line
  47-49) says never happens. Today's 5 `no_reply_24h` rows are all
  Ignore, so the real digest is not yet wrong, but the next unanswered
  Confirm will be.
  Fix: in `_shadow_waiting`, answered means
  `item.raw.get("answered_by_human") or item.raw.get("host_outcome") == "host_replied"`;
  a `no_reply_24h` row older than 24 h counts as "old". Add a fixture
  row with `host_outcome: "no_reply_24h"` and assert `waiting_old`
  counts it.

- **F2 (required before merge). An actuator queue copy wins over a
  shadow row that records a host reply.** `digest.py:363-370` lets the
  queue item overwrite unconditionally ("the actuator is the live lane"),
  but the shadow is the only log that tracks host replies. A message in
  both logs that the operator answered in Hospitable stays under
  "Waiting on you" for 24 hours. Probe P2 reproduced it.
  Fix: collect the keys of shadow rows that are answered
  (`answered_by_human` or `host_outcome == "host_replied"`) and skip
  queue items with those keys. Test: the fixture's res-s04 twin in the
  queue must not be listed.

Lesser observations, no fix required: shadow Act rows are included as
waiting (the report says so; at P1 both wait). Queue rows with
`hold_reason: pilot_ack_sent` show as waiting, which is right (the real
answer is still owed). Property-ID drift (whitespace or case) between
config and ledger reports an own row as a client row, a safe miscount.

## Item 2. Operator send gate: **PASS, with two hardening fixes**

Every attempt to send without all gates failed, and no path reaches any
number but the configured operator number:

- `--send` absent: builds only, `sent` state untouched, transport never
  built (test and probe).
- `a2p_confirmed`: only the JSON value `true` counts. `"true"`, `"yes"`,
  `1`, `1.0`, `[true]`, and the alternate keys `A2P_CONFIRMED`,
  `a2pConfirmed` all read as false.
- Operator number: `"+1...,+1..."`, leading space, trailing `;`,
  `tel:+1...`, spaced digits, equal to `from_number`, a crew directory
  number, unreadable directory: each produces a blocker. `to` and other
  alternate keys are ignored.
- The transport is bound at construction to `cfg.operator_number` and
  re-checked at `digest.py:741-742`; a transport bound elsewhere is
  refused before the intent record. The real CLI transport's argv
  matches the connector (`quo.py send --from --to --content`, verified
  against custom-connectors/custom.legacy-quo/bin/quo.py:154-157) and
  reads `data.id` from its JSON output.
- Intent-before-send, never-twice, unknown-never-retried, and
  watermark-only-on-confirmed all hold under mutation (each mutation
  caught).

Fixes:

- **F3 is in item 3.**
- **F4 (required before merge). A missing `crew_directory` silently
  skips the worker-number check.** `digest.py:636` runs the check only
  `if op and cfg.crew_directory`. The report lists the directory as a
  precondition of the one live send ("so the number is checked against
  worker numbers"); the code should make it one. Fix: in
  `send_blockers`, when `cfg.crew_directory` is unset, append
  "No crew directory configured: the operator number can't be checked
  against worker numbers." Test: `sendable_cfg` without a directory is
  refused on that reason.
- **F5 (required before merge). A non-string number in the config
  crashes every run, build-only included.** `digest.py:142-143` passes
  whatever JSON value is there; `send_blockers` is computed on every run
  for the payload, and `E164_RE.match(list)` raises `TypeError` before
  any file is written (probe P4: list, int, dict all crash). Fix: in
  `config_from_dict`, `operator_number`, `from_number`, `quo_cli`,
  `quo_pythonpath`, `crew_directory` must be a string or null, else
  `ConfigError`. Test: a list for `operator_number` exits 2 with a
  config error.
- **F6 (required before merge). `E164_RE` is loose.**
  `transport.py:33` uses `$` and `\d`: `"+15555550199\n"` and
  `"+1555555019９"` (fullwidth digit) both pass. Neither reaches a
  different person, but the regex is the recipient gate. Fix:
  `re.compile(r"\+[1-9][0-9]{7,14}\Z")` with `fullmatch`, or
  `re.ASCII`. Test both strings refused.

Note, no fix: a digest ID includes the build time, so "never sent
twice" is per run; two `--send` runs a minute apart send two texts.
Acceptable for an operator-run command; if a scheduler ever calls
`--send`, add a minimum interval.

## Item 3. Crew gate: **PASS on the stated claims, with one consent bypass to close**

Confirmed refused or held: free text in slots (any non-date, non-time
value, braces, extra keys), unapproved template, edited template (hash),
unknown template, wrong key or role, ambiguous recipient (two active
entries), zero entries, client property, property missing from the
directory, run record naming the wrong person, language mismatch (case
sensitive, safe), deny-listed keys in every spelling, non-crew keys,
opt-out, missing opt-out flag, no recorded YES, notice unapproved,
duplicate, unclear send, thread read failure, yield, loop guard, three
caps, cleaner hold, Class C hold, quiet hours across the DST change
(and in a non-Chicago property zone). An `OperatorTransport` passed as
the crew transport is a `TypeError`; a `CrewTransport` without
`OFFLINE = True` is refused. Every evaluate and submit is logged with
the body, hashes, class, right, last four digits only.

- **F3 (required before merge). Any template of kind `notice` skips
  the consent check, on any key.** `crew_gate.py:404` requires a
  recorded YES only when `t.kind == "job"`, and `templates.py:65-67`
  exempts every `notice`-kind template from the camera lint. Probe C17:
  an approved template with `kind="notice"` and `action_key="ask_status"`
  (Class B, earnable) went through to the mock transport with no YES on
  file and camera wording in the body. The registry is operator-edited
  outside the repo, so this is a one-field mistake away. Fix: (a) in
  `template_problems`, a `notice` kind must carry `action_key ==
  "crew_notice"` and a `job` kind must not; (b) in step 10, require the
  recorded YES for every template whose `template_id != self._notice_id`.
  Tests: the C17 template is rejected at load; a `job` template on
  `crew_notice` is rejected at load.

Required before any live crew transport (not blocking the merge):

- **F7. The offline check is a declared flag, not a type check.**
  `crew_gate.py:269` accepts any object with `OFFLINE is True`; a
  five-line subclass with a live `send_text` passes (probe C6), and the
  surface scanner does not see modules outside `jev.comms`. The report's
  wording ("refuses any transport that isn't offline") should read
  "that does not declare itself offline" until this is fixed. Fix:
  `if type(transport) is not MockCrewTransport: raise ValueError`, so
  wiring a live transport means editing the gate, which is what the
  report promises.
- **F8. A worker's last inbound text does not hold the send.** Spec 2.3
  ("the worker's last message bears on the trigger: running late,
  rescheduled, can't make it: skip, log human first") is implemented
  only as the loop guard (two inbound `other` in a row). With no
  classifier in this build, an inbound `kind=""` or `kind="cant_make_it"`
  as the newest message still sends (probe C9). Fix: add a `human_first`
  hold when the newest thread message is inbound and newer than the last
  automated outbound, whatever its kind.
- **F9. The directory's `short_name` is a free-text channel into the
  body.** It is operator-maintained, so this is the trust boundary, but
  `Directory.from_dict` (`crew_gate.py:160-162`) accepts an empty name
  (renders "visit to  on Sun Nov 8", probe C2) or a sentence (probe C1
  passed lint with "tell the cleaner to come at 6, guest said"). Fix:
  at load, require 1 to 40 characters of letters, digits, spaces,
  apostrophes, hyphens, periods, parentheses, and lint-clean.
- **F10. Lint vocabulary gaps.** "ninety dollars", "90 bucks",
  "compensation", "Guest Maria", "Mr. Smith's stay" pass (probe C4).
  Templates are hash-approved by the operator, so this is the second
  line, not the first. Add `dollars?|bucks|compensat\w*|wages?|salary|
  owed?|bill(?:ed|ing)?` to money and `\bguests?\s+[A-Z]\w+|\b(?:Mr|Mrs|
  Ms|Dr)\.?\s+[A-Z]` to guest detail.

Noted, no fix: ambiguous recipient refuses rather than holding as Class
C (spec 2.3); refusal is the safer reading. Varying `run_id` makes a new
attempt by design; the caps still bind (probe C11 held the fourth).
`approved_by` accepts any non-empty string; the registry file is the
operator's.

## Item 4. Deny-list: **PASS**

All 12 L7 operations raise `DeniedOperation` from `check_operation` in
every spelling tried (`Unlock_Smartlock`, `unlockSmartlock`, padded,
mixed-case connector). Deny-by-default holds: `quo:send-messages`,
`quo:list-contacts`, `quo:create-a2p-registration`, every Hospitable
read and write, and a `turno` connector are all refused. The only
allowed name is `quo:send-message`, every transport names it before
building a call, and every transport takes exactly one E.164 string (a
list, tuple, joined string, bytes, or zero-width-space variant is
refused). The comms package imports no Hospitable client and no network
library; its one process-boundary call is `subprocess.run` on the
configured CLI path, from the operator transport only, and that path is
built only after `send_blockers` is empty. The crew gate refuses
denied keys and every non-crew key. Mutations that dropped `unlock`,
widened the allowlist, or removed deny-by-default were all caught.

Limits, stated not failed: the surface scanner is keyword-based
(`text_everyone`, `send_to_many`, `open_door`, `refund_guest` would not
trip it; `cancel_booking` does), and `quo_cli` is a config-controlled
path, so the deny-list is enforced by naming inside this package, not
at the process boundary. Both are consistent with the report's wording.

## Item 5. Claim check (BUILD-REPORT.md vs code): **PASS with corrections**

Verified true: 144 new tests, 558 total, 414 before; no existing file
changed; builds only by default; `--send` on shipped gates exits 3; A2P
false refuses with the transport never built; sources are only read;
files mode 600 in a 700 directory, repo paths refused; Appendix B keys
are D, unknown keys are D, the map is read-only; B runs alone only on
its own key's right (borrowing the Act record is caught); the shipped
drafts load clean and unapproved; the CLI argv matches the connector.

- **F11 (report). The mutation-pass claim (41/41) has no artifact.**
  Nothing in the worktree, in /tmp, or anywhere under the hackathon
  directory holds the script or its output (the only mutation script on
  disk, /tmp/mutate.py, is the actuator's 12-mutation pass; the
  autopilot-live floor committed its own `mutation_check.py`). The claim
  is unverifiable as written. My independent pass of 51 mutations
  caught 51/51, so the substance holds. Fix: commit the script as
  `tests/mutation_check_comms.py` (or scripts/), so the number is
  reproducible, or change the sentence to cite this audit's pass.
- **F12 (report). "In the new tests, sockets and subprocesses raise if
  touched."** True for `test_comms_digest.py` and
  `test_comms_crew_gate.py`; `test_comms_deny_list.py` has no such
  fixture. Those tests touch neither, so nothing is at risk, but the
  sentence should say "in the digest and crew gate tests".
- The smoke-run sentence matches what I reproduced (2 own decisions, 0
  waiting, 1 client row left out, 4 blockers), except that the actuator
  directory now holds an `events.jsonl` (written 00:37:50Z by an
  `allowlist_confirmed` event from another session), so the digest
  today reads "actuator logs read" and lists that lane change. Not a
  discrepancy in the report; a change in the environment since it was
  written.
- Wording to tighten after F7: "refuses any transport that isn't
  offline" means "that does not declare `OFFLINE = True`".

- **F13 (out of scope, flag for main).** Package Appendix B says every
  Class D key is "in `NEVER_EARNED` (0b)". On both main 5f62011 and this
  branch, `autonomy.py:24` still reads `NEVER_EARNED = frozenset({"charge_guest"})`.
  The comms class map makes those keys never run alone inside comms, so
  this branch is safe, but the engine's `right_for` would still grade
  `share_access` or `refund` like any category if a ledger carried
  stats for them, and the test `NEVER_EARNED <= CLASS_D_KEYS` is
  vacuous with one element. Phase 0b is a main-branch item; the packet
  sentence "money and access actions are locked out of autonomy in
  code" depends on it landing. Suggested: add the Appendix B keys to
  `NEVER_EARNED` and flip the test to `CLASS_D_KEYS <= NEVER_EARNED`.

## Fix list, in order

Before the Mon Nov 2 merge (each with one test):

1. F1 `digest.py:292`: `no_reply_24h` is unanswered, counts as old.
2. F2 `digest.py:363-370`: a queue copy never overrides a shadow row that records a host reply.
3. F3 `crew_gate.py:404` and `templates.py:134-151`: consent required for every template but the registered notice; `notice` kind only on `crew_notice`.
4. F4 `digest.py:636`: missing `crew_directory` is a send blocker.
5. F5 `digest.py:142-148`: number and path fields must be string or null.
6. F6 `transport.py:33`: ASCII-only, `fullmatch`, no trailing newline.

Before any live crew transport: F7 (type check), F8 (human-first hold), F9 (short_name validation), F10 (lint words).

Report: F11 (commit the mutation script or recite this pass), F12 (fixture wording). Main: F13.

After F1 to F6 land with their tests and the suite is green, this
branch is clean to merge.
