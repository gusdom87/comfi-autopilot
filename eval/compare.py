"""Side-by-side ablation table from several eval out dirs (stats.json each).
Usage: python compare.py OUT_DIR [OUT_DIR ...]"""
import json
import sys


def main() -> int:
    print("| Run | n | Overall | Decisive | High-conf | Coverage | Mean cost |")
    print("|---|---|---|---|---|---|---|")
    for d in sys.argv[1:]:
        with open(f"{d}/stats.json") as fh:
            s = json.load(fh)
        print(f"| {d} | {s['n']} | {s['accuracy']:.1%} | {s['decisive_accuracy']:.1%} (n={s['decisive_n']}) "
              f"| {s['high_conf_accuracy']:.1%} | {s['high_conf_coverage']:.1%} | ${s['mean_cost_usd']:.4f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
