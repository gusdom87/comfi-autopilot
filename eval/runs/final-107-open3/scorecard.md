# Scorecard — open_escalate, examples=6

- Records scored: 101
- Overall accuracy: 74.3% (n=101)
- Decisive-call accuracy: 83.0% (n=53)
- High-confidence (>=0.85) accuracy: 76.2% (n=84, coverage 83.2%)
- Total cost: $1.1644; mean $0.0115 per decision; $0.0265 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 83.8% |
| Act | 29 | 55.2% |
| Ignore | 35 | 80.0% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 31 | 0 | 6 |
| Act | 10 | 16 | 3 |
| Ignore | 7 | 0 | 28 |
