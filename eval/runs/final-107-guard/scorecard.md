# Scorecard — escalate, examples=6

- Records scored: 101
- Overall accuracy: 77.2% (n=101)
- Decisive-call accuracy: 82.5% (n=57)
- High-confidence (>=0.85) accuracy: 78.8% (n=80, coverage 79.2%)
- Total cost: $1.8543; mean $0.0184 per decision; $0.0395 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 83.8% |
| Act | 29 | 55.2% |
| Ignore | 35 | 88.6% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 31 | 0 | 6 |
| Act | 10 | 16 | 3 |
| Ignore | 3 | 1 | 31 |
