"""Build the operator digest from the run logs (jev.comms.digest).

    python3 scripts/operator_digest.py --config <private config> [--print] [--no-advance]

Builds only. --send texts the digest to the operator's own phone through
Quo, and is refused (exit 3, reasons logged) unless every gate in
jev.comms.digest.send_blockers holds; a2p_confirmed ships false.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from jev.comms.digest import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
