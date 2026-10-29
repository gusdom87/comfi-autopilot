# Scorecard — open_escalate, examples=6

- Records scored: 101
- Overall accuracy: 81.2% (n=101)
- Decisive-call accuracy: 79.4% (n=68)
- High-confidence (>=0.85) accuracy: 81.7% (n=82, coverage 81.2%)
- Total cost: $1.4202; mean $0.0141 per decision; $0.0263 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 75.7% |
| Act | 29 | 86.2% |
| Ignore | 35 | 82.9% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 28 | 3 | 6 |
| Act | 1 | 25 | 3 |
| Ignore | 4 | 2 | 29 |
