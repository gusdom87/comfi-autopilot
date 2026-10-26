"""Eval harness skeleton. Reads the anonymized eval dataset (JSONL), runs a
tiering policy over each record, and writes a results file plus a scorecard:
accuracy overall and by category, decisive-call accuracy, accuracy at high
confidence, mean cost per decision, and the full confusion breakdown.
Dataset format and scoring land with the eval build; see specs.
"""
