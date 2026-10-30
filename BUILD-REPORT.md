# Build report: Quo comms floor (branch `quo-comms`)

Built Thu Oct 29, 2026, in the worktree at
`~/workspace/hackathon/quo-comms-worktree/`, cut from main at `5f62011`
(Phase 0 guard included). Not merged. Nothing was sent anywhere. Specs:
design-package-final-2026-10-29.md (S1, 1b, 1c, 1d, 2.6 floor, L7, L10)
and design-additions-audited-2026-10-29.md (2.1, 2.3, 2.6, 2.7).

## What was built

All new code is in `src/jev/comms/`. No existing file was changed.

| Piece | File | What it does |
|---|---|---|
| S1 operator digest | `digest.py`, `scripts/operator_digest.py` | Reads the shadow ledger and the actuator's logs (read-only; the actuator logs are optional, and the digest says when there are none). Writes a plain-language digest per run with these sections: waiting on you (held Act and Confirm items with their drafts), new decisions since the last digest, sends, auto-disarms and lane changes, guard trips (Phase 0 guard, never-graduate topics, actuator deny list, topic, lint, and redaction holds, fail-safes), and what was left out. Also writes a send-ready payload: an ASCII SMS of 700 characters or less, its hash, a segment estimate, and every gate that blocks sending it. It covers own listings only, matched by property ID; client rows are counted in one line and never mixed in. With `include_client_properties` they get a separate section that stays out of every count. Builds only by default. |
| S2-minimal crew send gate | `crew_gate.py`, `templates.py` | `CrewSendGate.evaluate/submit` is the only public surface. A request has no body field: the text is an approved template (hash-pinned), rendered by the gate. Slots can only be dates and times; the property name comes from the directory. Hard checks: deny list, own-listing allowlist, per-property role directory (cleaner, handyman, plumber, electrician; exactly one active entry, or it is refused), template approval, language, typed slots, content lint (money, access, digits, cameras, pronouns, guest details, discipline, signature, dashes, worker names), recorded YES to the approved notice, opt-out, one attempt per run, recipient, and template, and a thread re-read. Holds: cleaner recipient, yield to a human (2 h), loop guard, caps (3 per worker per day, 3 per job, 10 per day overall), and per-action autonomy. Worker quiet hours (9 PM to 7 AM, property-local, DST-aware) defer a send. Every call is logged in full, mode 600, outside the repo. |
| Connector deny-list | `deny_list.py` | The 12 operations package L7 names (Hospitable lock/unlock, cancel-reservation, create-quote, update-reservation, update-property-calendar, send-reservation-message; Quo agent call and conversation, knowledge agent, bulk, group, call transcripts) raise `DeniedOperation`. Everything else is denied by default. The only allowed operation is `quo:send-message` to one number. A surface scanner fails the tests if any comms module or class grows a denied name. |
| Class map (1b) | `classes.py` | Fixed A/B/C/D class per action key. Appendix B keys are D, an unknown key is D, and the map is read-only. B runs alone only on its own key's earned right; C and D never do. |
| Transports | `transport.py` | The crew transport has a mock only, and the gate refuses any transport that isn't offline. The operator transport has a mock plus `QuoCliOperatorTransport` (`quo.py send`, one recipient bound at construction). The digest builds it only after every gate holds, and no test runs it. |
| Private files | `_files.py` | Mode 600 files in mode 700 directories. Any path inside a git work tree is refused. |

## Tests

- **144 new tests, all passing.** Full suite: **558 passed** (414 before
  this branch plus 144 new), offline. In the new tests, sockets and
  subprocesses raise if touched. The fixtures in `tests/fixtures/comms/`
  are all invented.
- Digest, on the fixture ledger: counts (9 new, 5 waiting, 2 older than 24 h,
  2 sends, 1 auto-disarm, 4 guard trips, 2 client rows and 1 no-ID row left
  out, 1 unreadable line), drafts shown, Act sorted first, a message in both
  logs shown once, client rows absent by default and only in their own
  section when configured, ID match never by name, the window advances and
  held items persist, sources unchanged (hash and mtime), DST labels, and
  file modes.
- Operator send gates: A2P false refuses with a logged reason and the
  transport never built. Also refused: no or invalid operator number, the
  operator number equal to the sending line or on the crew directory, a
  missing CLI, and a digest sent twice. An unclear send is never retried; a
  transport bound to another number is refused. The CLI `--send` on shipped
  gates exits 3.
- Gate: unknown, ambiguous, or mismatched recipient refused; unapproved,
  edited, or unknown template refused; free-text slot rejected at load;
  deny-listed operations refused and absent from the surface; non-offline
  transport refused; consent, opt-out, quiet hours across Nov 1, caps,
  yield, loop guard, duplicates, and logging all covered.
- **Mutation pass:** 41 mutations, each breaking one gate in a scratch copy;
  all 41 turned the suite red. The first pass found one gap (a client row in
  the actuator queue); a test now covers it.
- **Smoke run on the real shadow ledger:** build-only, `--no-advance`, output
  in `/tmp` and then deleted. Own IDs came from the actuator's resolved
  allowlist. Exit 0; the ledger was byte-identical afterward; 2 own decisions
  in the 24 h window, 0 waiting, 1 client row left out, and 4 send blockers
  listed in the payload.

## Decisions to check

- Branch cut from main `5f62011` as instructed, not from a frozen tag (the
  package's 2.2 assumed a tag).
- Added beyond the brief, from audited spec 2.3: a cleaner text always
  holds for the operator to confirm the recipient, because there is no live
  Turno assignee read (`CLEANER_ASSIGNEE_READ = False`). Vendors are
  verified by the directory alone, so the mock send-path tests use a
  handyman.
- "Held Confirms" includes held Act calls: at P1 both wait for approval.
  Actuator queue items stay "waiting" for 24 h, because the actuator
  doesn't track replies.
- `message_guest` and `message_cleaner` are class C (the ceiling of their
  uses). `notify_operator` is A in the map, but the engine still gates it
  (item 2b isn't built).
- Missing `opted_out` reads as opted out (fail-safe).
- No private config or directory was created. Example config:

```json
{"schema": "operator-digest/config/v1",
 "own_property_ids": ["<confirmed own property IDs>"],
 "shadow_ledger": "~/workspace/hackathon/shadow-live/ledger.jsonl",
 "actuator_dir": "~/workspace/hackathon/autopilot-live",
 "out_dir": "~/workspace/hackathon/operator-digest",
 "timezone": "America/Chicago", "include_client_properties": false,
 "operator_number": null, "from_number": null, "a2p_confirmed": false,
 "quo_cli": null, "quo_pythonpath": null, "crew_directory": null}
```

## Stubbed or not built

- No live crew transport and no live `ThreadReader` (fakes only); no
  webhook; no S2 crew-text reader or classifier.
- Not built from item 1d: reply-to-approve codes (R9), the instant alert
  class and burst batching (R8), and the safety "call now" format. 2b
  (ungating `notify_operator` in the engine) and 2c (fixing the built
  drafts) are not done.
- `voice.py` is not wired in (it is private tooling outside the repo); the
  gate takes a `voice_lint` hook.
- The operator-authorized template arming path (R4) is not built; class B
  runs alone only on an earned right, and no worker key has one.
- The shipped templates are drafts awaiting approval: the worker notice and
  the S3 cleaner ask. None is approved. The brand string is open (Q7).

## Remaining gates before any live Quo send

**Operator digest text** (the only live send this code can make):
1. **A2P confirmation.** The read-only Quo A2P check (Q4a) has to show the
   line registered for operational notifications. Then set
   `a2p_confirmed: true`.
2. **Operator number config.** Set `operator_number` (Gus's own cell),
   `from_number`, `quo_cli`, `quo_pythonpath`, and `crew_directory`, so the
   number is checked against worker numbers.
3. **Gus's yes** to one real text (Q4b) and to the digest format (Q7),
   after the Mon Nov 2 merge and Fable audit; Milo watches it live, and the
   message ID is logged.

**Crew texts** (no live path exists in code):
1. **Worker-notice approval.** Gus approves the exact notice (R12), its
   hash goes in the registry, and each worker's recorded YES carries that
   hash.
2. **A2P coverage** for contractor notifications, plus a check of STOP
   behavior on the shared line.
3. **Code changes:** a live `CrewTransport` and `ThreadReader`, audited
   (the gate refuses non-offline transports today), and a live Turno
   assignee read for cleaners.
4. **Approvals:** templates by hash, directory entries, own-listing IDs,
   and an earned right or Gus's R4 decision for any class B automation.
