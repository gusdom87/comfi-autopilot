"""Quo comms floor (design package 2.6 floor; branch quo-comms).

Three pieces, none of them wired to a live loop:

  deny_list   the connector operations that are never callable from
              this package (Hospitable lock/unlock, cancel, quote,
              reservation and calendar writes, direct guest messages;
              Quo agent calls, knowledge agent, bulk and group texts,
              call transcripts). Deny by default: the only operation
              anything here may name is one Quo text to one number.
  digest      the operator digest (S1): composed from the run logs,
              written to a private file with a send-ready payload.
              The only live send it can make is one text to the
              operator's own phone, behind --send and a2p_confirmed.
  crew_gate   the crew send gate (S4-minimal): verified recipient,
              approved template, per-action autonomy check, full
              log. Code only: its transport is a mock, and it refuses
              any transport that is not offline.

Support modules: classes (the action-key class map, package 1b),
templates (the approved-template registry and content lint), transport
(the two transport interfaces), _files (private file handling).
"""
