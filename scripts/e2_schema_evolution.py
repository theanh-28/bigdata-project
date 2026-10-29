from __future__ import annotations

import argparse
import statistics
import time
from datetime import datetime, timezone

from common import RESULTS, get_client, get_db, now_iso, write_csv, write_json


def timed_query(coll, business_id: str, rounds: int) -> list[float]:
    latencies = []
    for _ in range(rounds):
        started = time.perf_counter()
        # Consume every matching result so the timer is not just measuring cursor creation.
        n = sum(1 for _ in coll.find({"business_id": business_id}, {"_id": 1, "review_id": 1, "schema_version": 1}).batch_size(500))
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def main() -> None:
    p = argparse.ArgumentParser(description="E2: demonstrate coexisting document versions without polluting the E3 baseline collection.")
    p.add_argument("--source-collection", default="reviews")
    p.add_argument("--target-collection", default="reviews_e2")
    p.add_argument("--n", type=int, default=100000, help="number of v1 documents and number of v2 documents")
    p.add_argument("--batch-size", type=int, default=2000)
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    args = p.parse_args()
    client = get_client(args.uri)
    db = get_db(client, args.db)
    source, target = db[args.source_collection], db[args.target_collection]
    if source.count_documents({}) < args.n:
        raise SystemExit(f"Source needs at least {args.n:,} docs; found {source.count_documents({}):,}.")
    target.drop()
    started = time.perf_counter()
    copied = 0
    cursor = source.find({}, {"_id": 0}).limit(args.n).batch_size(args.batch_size)
    buf = []
    sample_business = None
    for doc in cursor:
        if sample_business is None:
            sample_business = doc.get("business_id")
        buf.append(doc)
        if len(buf) >= args.batch_size:
            target.insert_many(buf, ordered=False)
            copied += len(buf)
            buf = []
    if buf:
        target.insert_many(buf, ordered=False)
        copied += len(buf)
    copy_seconds = time.perf_counter() - started

    # Second-version documents go into the isolated E2 collection, never into `reviews`.
    started = time.perf_counter()
    inserted_v2 = 0
    cursor = source.find({}, {"_id": 0}).limit(args.n).batch_size(args.batch_size)
    buf = []
    for doc in cursor:
        d = dict(doc)
        old_id = str(d.get("review_id", f"row-{inserted_v2}"))
        d["review_id"] = "v2-" + old_id
        d["schema_version"] = 2
        d["owner_reply"] = {"text": "Cam on ban", "at": "2026-10-01"}
        buf.append(d)
        if len(buf) >= args.batch_size:
            target.insert_many(buf, ordered=False)
            inserted_v2 += len(buf)
            buf = []
    if buf:
        target.insert_many(buf, ordered=False)
        inserted_v2 += len(buf)
    insert_seconds = time.perf_counter() - started

    versions = {str(row["_id"]): int(row["n"]) for row in target.aggregate([
        {"$group": {"_id": {"$ifNull": ["$schema_version", 1]}, "n": {"$sum": 1}}}
    ])}
    query_latencies = timed_query(target, sample_business, args.rounds) if sample_business is not None else []
    summary = {
        "timestamp": now_iso(), "experiment": "E2", "source_collection": args.source_collection,
        "isolated_target_collection": args.target_collection, "v1_documents_copied": copied,
        "v2_documents_inserted": inserted_v2, "count_total": target.count_documents({}),
        "version_counts": versions, "copy_v1_seconds": round(copy_seconds, 3),
        "insert_v2_seconds": round(insert_seconds, 3),
        "insert_v2_docs_per_s": round(inserted_v2 / insert_seconds, 2) if insert_seconds else 0,
        "mixed_query_business_id": sample_business,
        "mixed_query_p50_ms": round(statistics.median(query_latencies), 4) if query_latencies else None,
        "mixed_query_runs_ms": [round(v, 4) for v in query_latencies],
        "schema_migrations_performed": 0,
        "note": "E2 uses a separate collection so that E2's extra 2*n documents do not change E3/E4's dataset sizes.",
    }
    write_json(RESULTS / "e2_summary.json", summary)
    write_csv(RESULTS / "e2_summary.csv", [{k: (str(v) if isinstance(v, (dict, list)) else v) for k, v in summary.items()}])
    print(summary)
    client.close()


if __name__ == "__main__":
    main()
