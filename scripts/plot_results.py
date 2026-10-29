from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt

from common import FIGURES, RESULTS


def read_csv(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    # E1
    e1 = read_csv(RESULTS / "e1_attribute_frequency.csv")
    if e1:
        top = e1[:15]
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.barh([r["attribute"] for r in top[::-1]], [float(r["pct_all_businesses"]) for r in top[::-1]])
        ax.set_xlabel("Businesses with non-null value (%)")
        ax.set_title("E1 - Top 15 attribute keys")
        ax.grid(axis="x", alpha=0.25)
        fig.tight_layout(); fig.savefig(FIGURES / "e1_attribute_prevalence.png", dpi=160); plt.close(fig)

    # E3 - one figure per query, grouped by scale and index state.
    summaries = []
    for path in sorted(RESULTS.glob("e3_summary_*.csv")):
        summaries.extend(read_csv(path))
    if summaries:
        queries = sorted({r["query"] for r in summaries})
        for q in queries:
            subset = [r for r in summaries if r["query"] == q]
            fig, ax = plt.subplots(figsize=(8, 5))
            for config in ["no_index", "with_index"]:
                part = sorted([r for r in subset if r["config"] == config], key=lambda r: int(r["n_docs"]))
                if part:
                    ax.plot([int(r["n_docs"]) for r in part], [float(r["p50_ms"]) for r in part], marker="o", label=config)
            ax.set_xlabel("Number of review documents")
            ax.set_ylabel("Median latency (ms)")
            ax.set_yscale("log")
            ax.set_title(f"E3 - {q}: with and without indexes")
            ax.legend(); ax.grid(True, which="both", alpha=0.25)
            fig.tight_layout(); fig.savefig(FIGURES / f"e3_{q}.png", dpi=160); plt.close(fig)

    # E4 - median across repeat rows for each label/cache setting.
    e4 = read_csv(RESULTS / "e4_summary.csv")
    if e4:
        by_label = {}
        for r in e4:
            by_label.setdefault(r["label"], []).append(r)
        labels = list(by_label)
        p50 = [sorted(float(x["p50_ms"]) for x in by_label[k])[len(by_label[k]) // 2] for k in labels]
        p95 = [sorted(float(x["p95_ms"]) for x in by_label[k])[len(by_label[k]) // 2] for k in labels]
        fig, ax = plt.subplots(figsize=(9, 5))
        x = list(range(len(labels)))
        ax.plot(x, p50, marker="o", label="p50")
        ax.plot(x, p95, marker="o", label="p95")
        ax.set_xticks(x, labels, rotation=25, ha="right")
        ax.set_ylabel("Latency (ms)"); ax.set_yscale("log")
        ax.set_title("E4 - Indexed lookup latency by cache configuration")
        ax.legend(); ax.grid(True, which="both", alpha=0.25)
        fig.tight_layout(); fig.savefig(FIGURES / "e4_cache_latency.png", dpi=160); plt.close(fig)

    # E5a - per-shard insert share.
    e5 = read_csv(RESULTS / "e5a_shard_distribution.csv")
    if e5:
        cols = sorted({r["collection"] for r in e5})
        shards = ["shard1", "shard2", "shard3"]
        fig, ax = plt.subplots(figsize=(9, 5))
        width = 0.8 / max(1, len(cols))
        for i, col in enumerate(cols):
            vals = []
            for shard in shards:
                match = next((r for r in e5 if r["collection"] == col and r["shard"] == shard), None)
                vals.append(float(match["docs_share_pct"]) if match and match.get("docs_share_pct") else 0.0)
            positions = [j - 0.4 + width / 2 + i * width for j in range(len(shards))]
            ax.bar(positions, vals, width=width, label=col)
        ax.set_xticks(range(len(shards)), shards)
        ax.set_ylabel("Share of documents in shard (%)")
        ax.set_title("E5a - Document distribution observed on each shard")
        ax.legend(); ax.grid(axis="y", alpha=0.25)
        fig.tight_layout(); fig.savefig(FIGURES / "e5a_shard_insert_share.png", dpi=160); plt.close(fig)

    # E5c - same-workload latency comparison: standalone vs 3-shard cluster.
    e5c = read_csv(RESULTS / "e5c_comparison.csv")
    if e5c:
        row = e5c[-1]
        labels = ["Standalone", "3-shard cluster"]
        p50 = [float(row.get("single_p50_ms_median", 0) or 0), float(row.get("sharded_p50_ms_median", 0) or 0)]
        p95 = [float(row.get("single_p95_ms_median", 0) or 0), float(row.get("sharded_p95_ms_median", 0) or 0)]
        fig, ax = plt.subplots(figsize=(8, 5))
        x = list(range(len(labels)))
        width = 0.34
        ax.bar([i - width/2 for i in x], p50, width=width, label="p50")
        ax.bar([i + width/2 for i in x], p95, width=width, label="p95")
        ax.set_xticks(x, labels)
        ax.set_ylabel("Latency (ms)")
        if all(v > 0 for v in p50 + p95):
            ax.set_yscale("log")
        ax.set_title(f"E5c - point lookup latency comparison (N={row.get('n_docs', 'unknown')})")
        ax.legend(); ax.grid(axis="y", which="both", alpha=0.25)
        fig.tight_layout(); fig.savefig(FIGURES / "e5c_single_vs_sharded.png", dpi=160); plt.close(fig)

    print("Finished. Existing result CSVs were used; no numbers were fabricated.")
    print(f"Figures directory: {FIGURES}")


if __name__ == "__main__":
    main()
