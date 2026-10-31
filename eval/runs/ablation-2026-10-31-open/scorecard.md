# Scorecard — open_escalate, examples=6

- Records scored: 101
- Overall accuracy: 75.2% (n=101)
- Decisive-call accuracy: 82.1% (n=56)
- High-confidence (>=0.85) accuracy: 80.3% (n=76, coverage 75.2%)
- Total cost: $1.5810; mean $0.0157 per decision; $0.0344 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 81.1% |
| Act | 29 | 62.1% |
| Ignore | 35 | 80.0% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 30 | 1 | 6 |
| Act | 9 | 18 | 2 |
| Ignore | 6 | 1 | 28 |
