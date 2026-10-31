# Scorecard — spark_only, examples=6

- Records scored: 101
- Overall accuracy: 76.2% (n=101)
- Decisive-call accuracy: 80.7% (n=57)
- High-confidence (>=0.85) accuracy: 77.2% (n=79, coverage 78.2%)
- Total cost: $1.1074; mean $0.0110 per decision; $0.0241 per correct decisive call

## By ground-truth category

| Category | n | Accuracy |
|---|---|---|
| Confirm | 37 | 83.8% |
| Act | 29 | 62.1% |
| Ignore | 35 | 80.0% |

## Confusion (rows = truth, columns = prediction)

| Truth \ Pred | Confirm | Act | Ignore |
|---|---|---|---|
| Confirm | 31 | 0 | 6 |
| Act | 7 | 18 | 4 |
| Ignore | 6 | 1 | 28 |
