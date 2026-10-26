"""Pure scoring for eval results. A results row is a dict with:
ground_truth, predicted, confidence, cost_usd.
Definitions (kept identical to the pilot's report card):
- decisive: the model committed to Act or Ignore instead of deferring (Confirm).
- high-confidence band: confidence >= 0.85.
"""
CATEGORIES = ["Confirm", "Act", "Ignore"]
HIGH_CONF = 0.85


def _acc(rows):
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["predicted"] == r["ground_truth"]) / len(rows)


def score_rows(rows: list[dict]) -> dict:
    stats = {"n": len(rows), "accuracy": _acc(rows), "total_cost_usd": sum(r.get("cost_usd", 0.0) for r in rows)}
    stats["mean_cost_usd"] = stats["total_cost_usd"] / len(rows) if rows else 0.0
    stats["by_category"] = {}
    for cat in CATEGORIES:
        subset = [r for r in rows if r["ground_truth"] == cat]
        stats["by_category"][cat] = {"n": len(subset), "accuracy": _acc(subset)}
    decisive = [r for r in rows if r["predicted"] in ("Act", "Ignore")]
    stats["decisive_n"] = len(decisive)
    stats["decisive_accuracy"] = _acc(decisive)
    correct_decisive = sum(1 for r in decisive if r["predicted"] == r["ground_truth"])
    stats["cost_per_correct_decisive"] = (
        stats["total_cost_usd"] / correct_decisive if correct_decisive else 0.0
    )
    band = [r for r in rows if r.get("confidence", 0.0) >= HIGH_CONF]
    stats["high_conf_n"] = len(band)
    stats["high_conf_coverage"] = len(band) / len(rows) if rows else 0.0
    stats["high_conf_accuracy"] = _acc(band)
    matrix = {g: {p: 0 for p in CATEGORIES} for g in CATEGORIES}
    for r in rows:
        if r["ground_truth"] in matrix and r["predicted"] in matrix:
            matrix[r["ground_truth"]][r["predicted"]] += 1
    stats["confusion"] = matrix
    return stats


def render_scorecard(stats: dict, title: str, note: str = "") -> str:
    lines = [f"# Scorecard — {title}", ""]
    if note:
        lines += [f"> {note}", ""]
    lines += [
        f"- Records scored: {stats['n']}",
        f"- Overall accuracy: {stats['accuracy']:.1%} (n={stats['n']})",
        f"- Decisive-call accuracy: {stats['decisive_accuracy']:.1%} (n={stats['decisive_n']})",
        f"- High-confidence (>={HIGH_CONF}) accuracy: {stats['high_conf_accuracy']:.1%} "
        f"(n={stats['high_conf_n']}, coverage {stats['high_conf_coverage']:.1%})",
        f"- Total cost: ${stats['total_cost_usd']:.4f}; mean ${stats['mean_cost_usd']:.4f} per decision; "
        f"${stats['cost_per_correct_decisive']:.4f} per correct decisive call",
        "",
        "## By ground-truth category",
        "",
        "| Category | n | Accuracy |",
        "|---|---|---|",
    ]
    for cat in CATEGORIES:
        c = stats["by_category"][cat]
        lines.append(f"| {cat} | {c['n']} | {c['accuracy']:.1%} |")
    lines += ["", "## Confusion (rows = truth, columns = prediction)", "",
              "| Truth \\ Pred | " + " | ".join(CATEGORIES) + " |",
              "|" + "---|" * (len(CATEGORIES) + 1)]
    for g in CATEGORIES:
        row = stats["confusion"][g]
        lines.append(f"| {g} | " + " | ".join(str(row[p]) for p in CATEGORIES) + " |")
    lines.append("")
    return "\n".join(lines)
