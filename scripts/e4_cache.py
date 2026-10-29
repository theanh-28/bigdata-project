from __future__ import annotations

import argparse
import itertools
import json
import random
import time
from pathlib import Path
from typing import Any

from pymongo import ASCENDING

from common import RESULTS, get_client, get_db, now_iso, percentile_stats, write_csv, write_json

CACHE_KEYS = [
    "bytes currently in the cache",
    "maximum bytes configured",
    "pages read into cache",
    "pages written from cache",
    "pages evicted by application threads",
    "pages requested from the cache",
]


def cache_snapshot(client) -> dict[str, Any]:
    try:
        cache = client.admin.command("serverStatus").get("wiredTiger", {}).get("cache", {})
        return {key: cache.get(key) for key in CACHE_KEYS if key in cache}
    except Exception as e:
        return {"error": str(e)}


def ensure_keys(coll, path: Path, n_keys: int) -> list[str]:
    if path.exists():
        keys = json.loads(path.read_text(encoding="utf-8"))
        if keys:
            probe = keys[:min(100, len(keys))]
            found = coll.count_documents({"review_id": {"$in": probe}})
            if found == len(probe):
                return keys
            print("Existing E4 key list does not match the current collection; regenerating.")
    if coll.count_documents({}) < n_keys:
        raise SystemExit(f"Need at least {n_keys:,} documents for key pool; found {coll.count_documents({}):,}.")
    rows = list(coll.aggregate([{"$sample": {"size": n_keys}}, {"$project": {"_id": 0, "review_id": 1}}]))
    keys = [r["review_id"] for r in rows if r.get("review_id") is not None]
    path.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(keys):,} stable review_id keys to {path}")
    return keys


def main() -> None:
    p = argparse.ArgumentParser(description="E4: measure indexed point lookups under different WiredTiger cache limits.")
    p.add_argument("--mode", choices=["stats", "make-keys", "run"], required=True)
    p.add_argument("--collection", default="reviews")
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--key-count", type=int, default=60000)
    p.add_argument("--ops", type=int, default=50000, help="measured lookups (use 5000 for a pilot)")
    p.add_argument("--warmup", type=int, default=20000)
    p.add_argument("--skip-warmup", action="store_true", help="measure immediately after container recreation (WiredTiger cache is fresh)")
    p.add_argument("--label", default="cache_unknown", help="e.g. C0_5_cold or C0_5_hot")
    p.add_argument("--round", type=int, default=1)
    args = p.parse_args()
    client = get_client(args.uri)
    db = get_db(client, args.db)
    coll = db[args.collection]
    stats = db.command({"collStats": coll.name, "scale": 1})
    if args.mode == "stats":
        status = {
            "timestamp": now_iso(), "collection": coll.full_name, "count": coll.count_documents({}),
            "size_bytes_logical": stats.get("size"), "storage_size_bytes": stats.get("storageSize"),
            "total_index_size_bytes": stats.get("totalIndexSize"),
            "D_logical_plus_indexes_gib_rough_estimate": round((stats.get("size", 0) + stats.get("totalIndexSize", 0)) / (1024**3), 4),
            "wiredTiger_cache": cache_snapshot(client),
            "note": "Rough estimate only: WiredTiger in-memory representation differs from on-disk BSON/storage size.",
        }
        write_json(RESULTS / "e4_working_set_estimate.json", status)
        print(json.dumps(status, indent=2, ensure_ascii=False))
        client.close()
        return
    key_path = RESULTS / "e4_query_keys.json"
    if args.mode == "make-keys":
        keys = ensure_keys(coll, key_path, args.key_count)
        print(f"Generated/reused {len(keys):,} stable lookup keys.")
        client.close()
        return
    if not key_path.exists():
        raise SystemExit("Key file not found. Run --mode make-keys after E3 indexes are created.")
    keys = json.loads(key_path.read_text(encoding="utf-8"))
    if not keys:
        raise SystemExit("E4 key file is empty.")
    if coll.index_information().get("review_id_1") is None:
        print("Creating review_id index before measurement; index build is not included in query timings.")
        coll.create_index([("review_id", ASCENDING)])
    rng = random.Random(42)
    order = list(range(len(keys)))
    rng.shuffle(order)
    ordered_keys = [keys[i] for i in order]
    if not args.skip_warmup:
        warm_iterator = itertools.cycle(ordered_keys)
        print(f"Warm-up: {args.warmup:,} point lookups (excluded from measured latency)")
        for _ in range(args.warmup):
            coll.find_one({"review_id": next(warm_iterator)}, {"_id": 1})
    # Reset the measured key sequence after warm-up so all cache configurations use the same order.
    iterator = itertools.cycle(ordered_keys)
    before = cache_snapshot(client)
    latencies = []
    raw_rows = []
    found_count = 0
    started = time.perf_counter()
    print(f"Measuring {args.ops:,} indexed point lookups; label={args.label}; skip_warmup={args.skip_warmup}")
    for i in range(args.ops):
        key = next(iterator)
        t0 = time.perf_counter()
        result = coll.find_one({"review_id": key}, {"_id": 1})
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        found = result is not None
        found_count += int(found)
        raw_rows.append({"trial_op": i + 1, "review_id": key, "elapsed_ms": round(ms, 5), "found": found})
    elapsed_s = time.perf_counter() - started
    after = cache_snapshot(client)
    perf = percentile_stats(latencies)
    row = {
        "timestamp": now_iso(), "experiment": "E4", "label": args.label, "round": args.round,
        "collection": coll.full_name, "n_docs": coll.count_documents({}), "ops": args.ops,
        "warmup_ops": 0 if args.skip_warmup else args.warmup, "configured_cache_bytes": after.get("maximum bytes configured"),
        "p50_ms": round(perf["p50_ms"], 5), "p95_ms": round(perf["p95_ms"], 5),
        "p99_ms": round(perf["p99_ms"], 5), "mean_ms": round(perf["mean_ms"], 5),
        "ops_s_client_round_trip": round(args.ops / elapsed_s, 2) if elapsed_s else 0,
        "found_count": found_count,
        "missing_count": args.ops - found_count,
        "wiredTiger_before": json.dumps(before, ensure_ascii=False),
        "wiredTiger_after": json.dumps(after, ensure_ascii=False),
        "interpretation_note": "Latency includes Python/PyMongo client round-trip; Docker containers share the laptop's physical storage and CPU.",
    }
    write_csv(RESULTS / "e4_summary.csv", [row], append=True)
    write_csv(RESULTS / f"e4_raw_{args.label}_r{args.round}.csv", raw_rows)
    write_json(RESULTS / f"e4_cache_stats_{args.label}_r{args.round}.json", {"before": before, "after": after})
    print(json.dumps(row, ensure_ascii=False, indent=2))
    client.close()


if __name__ == "__main__":
    main()
