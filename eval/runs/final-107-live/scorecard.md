# Scorecard — escalate, examples=6

- Records scored: 101
- Overall accuracy: 80.2% (n=101)
- Decisive-call accuracy: 82.3% (n=62)
- High-confidence (>=0.85) accuracy: 79.5% (n=78, coverage 77.2%)
- Total cost: $1.6673; mean $0.0165 per decision; $0.0327 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 81.1% |
| Act | 29 | 75.9% |
| Ignore | 35 | 82.9% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 30 | 2 | 5 |
| Act | 3 | 22 | 4 |
| Ignore | 6 | 0 | 29 |
