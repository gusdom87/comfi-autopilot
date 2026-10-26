"""Append-only JSONL decision log: the raw material of the confidence ledger."""
import json
import os


def append_decision(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
