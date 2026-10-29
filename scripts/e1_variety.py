from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from common import FIGURES, RESULTS, get_client, get_db, write_csv, write_json


def main() -> None:
    p = argparse.ArgumentParser(description="E1: measure attribute variety and sparsity in business documents.")
    p.add_argument("--collection", default="business")
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    args = p.parse_args()
    client = get_client(args.uri)
    db = get_db(client, args.db)
    coll = db[args.collection]
    n_business = coll.count_documents({})
    if n_business == 0:
        raise SystemExit(f"{db.name}.{args.collection} is empty. Load business JSONL first.")
    n_object_attrs = coll.count_documents({"attributes": {"$type": "object"}})
    pipeline = [
        {"$match": {"attributes": {"$type": "object"}}},
        {"$project": {"pairs": {"$objectToArray": "$attributes"}}},
        {"$unwind": "$pairs"},
        {"$match": {"pairs.v": {"$ne": None}}},
        {"$group": {"_id": "$pairs.k", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    frequencies = list(coll.aggregate(pipeline, allowDiskUse=True))
    k = len(frequencies)
    rows = []
    for row in frequencies:
        count = int(row["n"])
        rows.append({
            "attribute": row["_id"],
            "non_null_count": count,
            "pct_all_businesses": round(100 * count / n_business, 4),
            "pct_businesses_with_object_attributes": round(100 * count / n_object_attrs, 4) if n_object_attrs else 0.0,
        })
    filled_cells = sum(int(r["n"]) for r in frequencies)
    null_pct = 100 * (1 - filled_cells / (n_business * k)) if n_business and k else 0.0
    keys_present_in_under_half = sum(1 for r in rows if r["pct_all_businesses"] < 50.0)
    summary = {
        "experiment": "E1",
        "database": db.name,
        "collection": args.collection,
        "business_documents": n_business,
        "documents_with_object_attributes": n_object_attrs,
        "documents_without_object_attributes_or_null": n_business - n_object_attrs,
        "distinct_non_null_attribute_keys_K": k,
        "attribute_value_presence_cells": filled_cells,
        "estimated_flat_table_null_cells_pct": round(null_pct, 4),
        "keys_present_in_less_than_50pct_businesses": keys_present_in_under_half,
        "fraction_of_keys_present_in_less_than_50pct_businesses": round(keys_present_in_under_half / k, 4) if k else 0.0,
        "interpretation_note": "Sparsity is an estimate from observed non-null attribute values. Attribute keys with explicit null values are treated as missing cells.",
    }
    write_csv(RESULTS / "e1_attribute_frequency.csv", rows, append=False)
    write_json(RESULTS / "e1_summary.json", summary)
    print(summary)
    if rows:
        top = rows[:15]
        fig, ax = plt.subplots(figsize=(10, 6))
        labels = [r["attribute"] for r in reversed(top)]
        vals = [r["pct_all_businesses"] for r in reversed(top)]
        ax.barh(labels, vals)
        ax.set_xlabel("Businesses with a non-null value (%)")
        ax.set_title("E1 - Top 15 attribute keys by prevalence")
        ax.grid(axis="x", alpha=0.25)
        fig.tight_layout()
        fig.savefig(FIGURES / "e1_attribute_prevalence.png", dpi=160)
        plt.close(fig)
    client.close()


if __name__ == "__main__":
    main()
