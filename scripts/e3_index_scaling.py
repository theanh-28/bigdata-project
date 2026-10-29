from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from common import RESULTS, get_client, get_db, get_exec_stats, percentile_stats, write_csv, write_json


def get_fixtures(coll, n_docs: int, path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    docs = list(coll.find({}, {"_id": 0, "business_id": 1, "user_id": 1, "date": 1}).limit(2000))
    if not docs:
        raise SystemExit("No documents found to build deterministic query fixtures.")
    rng = random.Random(42)
    picked = rng.choice(docs)
    month_value = str(picked.get("date", "2019-01-15"))[:7]
    try:
        year, month = int(month_value[:4]), int(month_value[5:7])
        if month == 12:
            next_month = f"{year + 1:04d}-01-01"
        else:
            next_month = f"{year:04d}-{month + 1:02d}-01"
        first_day = f"{year:04d}-{month:02d}-01"
    except Exception:
        first_day, next_month = "2019-01-01", "2019-02-01"
    fixtures = {
        "business_id": picked.get("business_id"),
        "user_id": picked.get("user_id"),
        "date_gte": first_day,
        "date_lt": next_month,
        "seed": 42,
        "fixture_source": "deterministically selected from the first 2,000 natural-order documents in the initial checkpoint",
        "first_run_n_docs": n_docs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixtures, indent=2, ensure_ascii=False), encoding="utf-8")
    return fixtures


def run_one(coll, qname: str, fixtures: dict[str, Any]) -> int:
    if qname == "Q1_business_id":
        flt = {"business_id": fixtures["business_id"]}
        return sum(1 for _ in coll.find(flt, {"_id": 1}).batch_size(1000))
    if qname == "Q2_user_id":
        flt = {"user_id": fixtures["user_id"]}
        return sum(1 for _ in coll.find(flt, {"_id": 1}).batch_size(1000))
    if qname == "Q3_date_month":
        flt = {"date": {"$gte": fixtures["date_gte"], "$lt": fixtures["date_lt"]}}
        return sum(1 for _ in coll.find(flt, {"_id": 1}).batch_size(1000))
    if qname == "Q4_group_top10":
        pipe = [
            {"$group": {"_id": "$business_id", "review_count": {"$sum": 1}, "avg_stars": {"$avg": "$stars"}}},
            {"$sort": {"review_count": -1}},
            {"$limit": 10},
        ]
        return len(list(coll.aggregate(pipe, allowDiskUse=True)))
    raise ValueError(qname)


def explain_one(coll, qname: str, fixtures: dict[str, Any]) -> dict[str, Any]:
    if qname == "Q1_business_id":
        cmd = {"find": coll.name, "filter": {"business_id": fixtures["business_id"]}, "projection": {"_id": 1}}
    elif qname == "Q2_user_id":
        cmd = {"find": coll.name, "filter": {"user_id": fixtures["user_id"]}, "projection": {"_id": 1}}
    elif qname == "Q3_date_month":
        cmd = {"find": coll.name, "filter": {"date": {"$gte": fixtures["date_gte"], "$lt": fixtures["date_lt"]}}, "projection": {"_id": 1}}
    else:
        cmd = {"aggregate": coll.name, "pipeline": [
            {"$group": {"_id": "$business_id", "review_count": {"$sum": 1}, "avg_stars": {"$avg": "$stars"}}},
            {"$sort": {"review_count": -1}}, {"$limit": 10},
        ], "cursor": {}, "allowDiskUse": True}
    return coll.database.command("explain", cmd, verbosity="executionStats")


def main() -> None:
    p = argparse.ArgumentParser(description="E3: benchmark queries with and without secondary indexes at each incremental data size.")
    p.add_argument("--collection", default="reviews")
    p.add_argument("--n-docs", type=int, required=True, help="expected count of reviews at this staged checkpoint")
    p.add_argument("--repeats", type=int, default=10, help="full query executions per round; p95 from only 10 values is descriptive, not a high-confidence tail estimate")
    p.add_argument("--rounds", type=int, default=3, help="independent measurement rounds; report median across rounds")
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    args = p.parse_args()
    if args.rounds < 1 or args.repeats < 2:
        p.error("--rounds must be >= 1 and --repeats >= 2")
    client = get_client(args.uri)
    db = get_db(client, args.db)
    coll = db[args.collection]
    actual = coll.count_documents({})
    if actual != args.n_docs:
        raise SystemExit(f"Checkpoint mismatch: --n-docs={args.n_docs:,}, actual collection count={actual:,}. Do not run E3 on an E2-contaminated collection.")
    fixture_path = RESULTS / "e3_query_fixtures.json"
    fixtures = get_fixtures(coll, args.n_docs, fixture_path)
    queries = ["Q1_business_id", "Q2_user_id", "Q3_date_month", "Q4_group_top10"]
    raw_rows: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []
    explain_payload: dict[str, Any] = {"n_docs": actual, "fixtures": fixtures, "rounds": args.rounds, "repeats_per_query": args.repeats, "queries": {}}

    for config in ["no_index", "with_index"]:
        for round_no in range(1, args.rounds + 1):
            coll.drop_indexes()
            index_build_ms = 0.0
            if config == "with_index":
                for keys in [[("business_id", 1)], [("user_id", 1)], [("date", 1)], [("review_id", 1)]]:
                    t0 = time.perf_counter()
                    coll.create_index(keys)
                    index_build_ms += (time.perf_counter() - t0) * 1000
            index_stats = db.command({"collStats": coll.name, "scale": 1})
            index_size_bytes = int(index_stats.get("totalIndexSize", index_stats.get("indexSize", 0)) or 0)
            print(f"\nE3 n={actual:,} config={config} round={round_no}/{args.rounds}; running {len(queries)} queries x {args.repeats} trials...")
            for qname in queries:
                explain = explain_one(coll, qname, fixtures)
                stats = get_exec_stats(explain)
                explain_payload["queries"][f"{config}:round{round_no}:{qname}"] = {"stats": stats, "explain": explain}
                latencies = []
                run_one(coll, qname, fixtures)  # discarded warm-up for this query/config/round
                for trial in range(1, args.repeats + 1):
                    t0 = time.perf_counter()
                    returned = run_one(coll, qname, fixtures)
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    latencies.append(elapsed_ms)
                    raw_rows.append({
                        "experiment": "E3", "n_docs": actual, "config": config, "round": round_no,
                        "query": qname, "trial": trial, "elapsed_ms": round(elapsed_ms, 4),
                        "result_count": returned, "docs_examined_explain": stats.get("docs_examined"),
                        "keys_examined_explain": stats.get("keys_examined"), "explain_ms": stats.get("explain_ms"),
                    })
                perf = percentile_stats(latencies)
                round_rows.append({
                    "experiment": "E3", "n_docs": actual, "config": config, "round": round_no, "query": qname,
                    **{k: round(v, 4) for k, v in perf.items()},
                    "docs_examined": stats.get("docs_examined"), "keys_examined": stats.get("keys_examined"),
                    "explain_execution_ms": stats.get("explain_ms"), "index_size_mb": round(index_size_bytes / (1024**2), 3),
                    "index_build_ms_total": round(index_build_ms, 3),
                })
                print(f"  {qname}: p50={perf['p50_ms']:.2f} ms, p95={perf['p95_ms']:.2f} ms, docsExamined={stats.get('docs_examined')}")

    # One summary row per query/config/size, using the median of per-round statistics.
    summary_rows: list[dict[str, Any]] = []
    for config in ["no_index", "with_index"]:
        for qname in queries:
            group = [r for r in round_rows if r["config"] == config and r["query"] == qname]
            if not group:
                continue
            summary_rows.append({
                "experiment": "E3", "n_docs": actual, "config": config, "query": qname,
                "rounds": len(group), "repeats_per_round": args.repeats,
                "p50_ms": round(sorted(float(r["p50_ms"]) for r in group)[len(group)//2], 4),
                "p95_ms": round(sorted(float(r["p95_ms"]) for r in group)[len(group)//2], 4),
                "p99_ms": round(sorted(float(r["p99_ms"]) for r in group)[len(group)//2], 4),
                "mean_ms": round(sorted(float(r["mean_ms"]) for r in group)[len(group)//2], 4),
                "ops_s": round(sorted(float(r["ops_s"]) for r in group)[len(group)//2], 4),
                "docs_examined": group[-1].get("docs_examined"), "keys_examined": group[-1].get("keys_examined"),
                "explain_execution_ms": group[-1].get("explain_execution_ms"),
                "index_size_mb": round(sorted(float(r["index_size_mb"]) for r in group)[len(group)//2], 3),
                "index_build_ms_total": round(sorted(float(r["index_build_ms_total"]) for r in group)[len(group)//2], 3),
                "p95_caveat": "Median of per-round p95; each round has a small number of full-query executions, so interpret as descriptive.",
            })
    write_csv(RESULTS / f"e3_raw_{actual}.csv", raw_rows)
    write_csv(RESULTS / f"e3_rounds_{actual}.csv", round_rows)
    write_csv(RESULTS / f"e3_summary_{actual}.csv", summary_rows)
    write_json(RESULTS / f"e3_explain_{actual}.json", explain_payload)
    print(f"Saved E3 raw, per-round, median-summary and explain outputs for n={actual:,} under results/.")
    client.close()


if __name__ == "__main__":
    main()
