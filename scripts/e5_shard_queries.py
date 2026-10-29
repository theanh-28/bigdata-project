from __future__ import annotations

import argparse
import random
import time
from pathlib import Path
from typing import Any

from common import RESULTS, get_client, get_db, get_exec_stats, percentile_stats, write_csv, write_json


def main() -> None:
    p = argparse.ArgumentParser(description="E5b: compare targeted vs scatter-gather queries through mongos.")
    p.add_argument("--collection", default="reviews_targeted")
    p.add_argument("--ops", type=int, default=1000, help="repetitions per query; use 100 for a quick pilot")
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    args = p.parse_args()
    client = get_client(args.uri)
    db = get_db(client, args.db)
    coll = db[args.collection]
    docs = list(coll.find({}, {"_id": 0, "review_id": 1, "business_id": 1}).limit(5000))
    if len(docs) < 10:
        raise SystemExit(f"{coll.full_name} needs at least 10 documents. Load it through mongos after sharding it on review_id hashed.")
    rng = random.Random(42)
    exact = {"review_id": rng.choice(docs)["review_id"]}
    business = {"business_id": rng.choice(docs)["business_id"]}
    ids = sorted(str(d["review_id"]) for d in docs if d.get("review_id") is not None)
    lo, hi = ids[len(ids) // 4], ids[(3 * len(ids)) // 4]
    by_name = {
        "targeted_exact_shard_key": (exact, None),
        "scatter_gather_business_id": (business, None),
        "scatter_gather_range_on_hashed_key": ({"review_id": {"$gte": lo, "$lt": hi}}, 100),
    }
    explain_out = {}
    rows = []
    for name, (flt, limit) in by_name.items():
        find_cmd = {"find": coll.name, "filter": flt, "projection": {"_id": 1}}
        if limit:
            find_cmd["limit"] = limit
        explain = db.command("explain", find_cmd, verbosity="executionStats")
        explain_out[name] = {"filter": flt, "explain": explain, "summary": get_exec_stats(explain)}
        latencies = []
        def operation():
            cur = coll.find(flt, {"_id": 1})
            if limit:
                cur = cur.limit(limit)
            return sum(1 for _ in cur.batch_size(1000))
        operation()  # one warm-up
        for _ in range(args.ops):
            t0 = time.perf_counter()
            result_count = operation()
            latencies.append((time.perf_counter() - t0) * 1000)
        perf = percentile_stats(latencies)
        rows.append({
            "experiment": "E5b", "query": name, "filter": str(flt), "repeats": args.ops,
            **{k: round(v, 4) for k, v in perf.items()}, "result_count_last_run": result_count,
            **get_exec_stats(explain),
        })
        print(f"{name}: p50={perf['p50_ms']:.3f}ms p95={perf['p95_ms']:.3f}ms docsExamined={get_exec_stats(explain).get('docs_examined')}")
    write_csv(RESULTS / "e5b_query_summary.csv", rows)
    write_json(RESULTS / "e5b_query_explain.json", explain_out)
    client.close()


if __name__ == "__main__":
    main()
