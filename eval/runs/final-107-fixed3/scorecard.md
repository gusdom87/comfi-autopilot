# Scorecard — escalate, examples=6

- Records scored: 101
- Overall accuracy: 77.2% (n=101)
- Decisive-call accuracy: 82.5% (n=57)
- High-confidence (>=0.85) accuracy: 75.6% (n=86, coverage 85.1%)
- Total cost: $1.6734; mean $0.0166 per decision; $0.0356 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 83.8% |
| Act | 29 | 58.6% |
| Ignore | 35 | 85.7% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 31 | 0 | 6 |
| Act | 9 | 17 | 3 |
| Ignore | 4 | 1 | 30 |
