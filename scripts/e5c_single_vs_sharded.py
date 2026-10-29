from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, MongoClient

from common import RESULTS, get_client, get_db, get_exec_stats, now_iso, percentile_stats, write_csv, write_json

FIXTURE_PATH = RESULTS / "e5c_query_keys.json"
SHARD_PORTS = {"sh1": 27101, "sh2": 27102, "sh3": 27103}


def _collection_stats(db, collection_name: str) -> dict[str, Any]:
    # PyMongo Collection has no .stats() method; use the collStats database command.
    return db.command({"collStats": collection_name, "scale": 1})


def _reservoir_sample_review_ids(coll, n_docs: int, sample_size: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    reservoir: list[str] = []
    seen = 0
    cursor = coll.find({}, {"_id": 0, "review_id": 1}).batch_size(5000)
    for row in cursor:
        seen += 1
        review_id = row.get("review_id")
        if not isinstance(review_id, str) or not review_id:
            raise SystemExit(f"Document number {seen} has a missing/invalid review_id; cannot build E5c fixture.")
        if seen <= sample_size:
            reservoir.append(review_id)
        else:
            j = rng.randrange(seen)
            if j < sample_size:
                reservoir[j] = review_id
        if seen >= n_docs:
            break
    if seen != n_docs:
        raise SystemExit(f"Expected to read {n_docs:,} documents while sampling keys; read {seen:,}. Check --n-docs and collection count.")
    if len(set(reservoir)) != len(reservoir):
        raise SystemExit("Sample contains duplicate review_id values. Verify source data uniqueness before E5c.")
    return reservoir


def capture_keys(args: argparse.Namespace) -> None:
    client = get_client(args.uri)
    try:
        hello = client.admin.command("hello")
        if hello.get("msg") == "isdbgrid":
            raise SystemExit("capture-keys must connect to the standalone mongod, not mongos. Stop sharded compose and start single compose first.")
        db = get_db(client, args.db)
        coll = db[args.collection]
        actual = coll.count_documents({})
        if actual != args.n_docs:
            raise SystemExit(f"Expected exactly {args.n_docs:,} documents in {coll.full_name}; found {actual:,}. Do not capture keys at a different checkpoint.")
        if args.key_count < 100 or args.key_count > args.n_docs:
            raise SystemExit("--key-count must be at least 100 and no greater than --n-docs.")
        if FIXTURE_PATH.exists() and not args.force:
            existing = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            if existing.get("n_docs") == args.n_docs and existing.get("source_collection") == coll.full_name and existing.get("seed") == args.seed and len(existing.get("keys", [])) == args.key_count:
                print(f"Reusing existing fixture: {FIXTURE_PATH} ({len(existing['keys']):,} keys). Do not regenerate between single and sharded runs.")
                return
            raise SystemExit(f"Fixture already exists but does not match this run: {FIXTURE_PATH}. Back it up, then rerun with --force only if intentionally starting a new comparison.")
        print(f"Sampling {args.key_count:,} review_id values from {coll.full_name} ({actual:,} documents). This scans the collection once; please wait for completion.")
        keys = _reservoir_sample_review_ids(coll, args.n_docs, args.key_count, args.seed)
        payload = {
            "created_at": now_iso(),
            "database": db.name,
            "source_collection": coll.full_name,
            "n_docs": args.n_docs,
            "key_count": len(keys),
            "seed": args.seed,
            "sampling_method": "reservoir sampling over projected review_id values from the standalone collection",
            "keys": keys,
            "warning": "Keep this file unchanged between standalone and sharded benchmark phases.",
        }
        write_json(FIXTURE_PATH, payload)
        print(f"Saved fixed query keys to {FIXTURE_PATH}.")
    finally:
        client.close()


def _index_info(coll) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for name, spec in coll.index_information().items():
        info[name] = {"key": list(spec.get("key", [])), "unique": spec.get("unique", False), "sparse": spec.get("sparse", False)}
    return info


def _ensure_single_index(coll) -> None:
    for spec in coll.index_information().values():
        if list(spec.get("key", [])) == [("review_id", ASCENDING)]:
            return
    print("Creating ascending index on review_id before E5c measurement (index build time is excluded from query latency).")
    coll.create_index([("review_id", ASCENDING)], name="e5c_review_id_asc")


def _validate_sharded_collection(client: MongoClient, db_name: str, collection_name: str) -> None:
    hello = client.admin.command("hello")
    if hello.get("msg") != "isdbgrid":
        raise SystemExit("--role sharded requires a connection through mongos. Stop standalone and start compose/docker-compose.shard.yml; connect to mongodb://localhost:27017.")
    namespace = f"{db_name}.{collection_name}"
    entry = client["config"]["collections"].find_one({"_id": namespace}, {"_id": 1, "key": 1})
    if not entry:
        raise SystemExit(f"{namespace} is not registered as a sharded collection. Run setup_e5c_collection.js through mongos first.")
    if (entry.get("key") or {}).get("review_id") != "hashed":
        raise SystemExit(f"{namespace} must use shard key {{review_id: 'hashed'}} for this E5c protocol; actual key is {entry.get('key')}.")


def _validate_sample_keys(coll, keys: list[str], batch_size: int = 500) -> None:
    checked = 0
    for start in range(0, len(keys), batch_size):
        batch = keys[start:start + batch_size]
        found = coll.count_documents({"review_id": {"$in": batch}})
        if found != len(batch):
            raise SystemExit(
                f"Fixture mismatch: batch starting at key {start} has {found}/{len(batch)} matching documents in {coll.full_name}. "
                "Make sure the sharded collection was loaded from the same source file and same first --n-docs records."
            )
        checked += len(batch)
    print(f"Validated all {checked:,} fixed query keys in {coll.full_name}.")


def _shard_distribution(stats: dict[str, Any], client: MongoClient) -> dict[str, Any]:
    try:
        listed = client.admin.command("listShards").get("shards", [])
        shard_ids = [str(s.get("_id")) for s in listed]
    except Exception:
        shard_ids = list(stats.get("shards", {}).keys())
    per_shard: dict[str, Any] = {}
    for shard_id in shard_ids:
        item = (stats.get("shards") or {}).get(shard_id, {})
        per_shard[shard_id] = {
            "count": int(item.get("count", 0) or 0),
            "data_size_bytes": int(item.get("size", 0) or 0),
            "index_size_bytes": int(item.get("totalIndexSize", item.get("indexSize", 0)) or 0),
        }
    total = sum(v["count"] for v in per_shard.values())
    for v in per_shard.values():
        v["share_pct"] = round(100 * v["count"] / total, 3) if total else 0.0
        v["data_plus_index_gib"] = round((v["data_size_bytes"] + v["index_size_bytes"]) / (1024**3), 4)
    active = sum(1 for v in per_shard.values() if v["count"] > 0)
    max_share = max((v["share_pct"] for v in per_shard.values()), default=0)
    return {"per_shard": per_shard, "total_count_from_collStats": total, "active_shards": active, "max_shard_share_pct": max_share}


def _cache_snapshot(role: str, client: MongoClient) -> dict[str, Any]:
    if role == "single":
        server_status = client.admin.command("serverStatus")
        cache = (server_status.get("wiredTiger") or {}).get("cache") or {}
        return {
            "per_node": {"standalone": {
                "maximum_bytes_configured": cache.get("maximum bytes configured"),
                "bytes_currently_in_cache": cache.get("bytes currently in the cache"),
                "pages_read_into_cache": cache.get("pages read into cache"),
                "pages_evicted_by_application_threads": cache.get("pages evicted by application threads"),
            }},
            "note": "WiredTiger cache only; operating-system filesystem cache is separate.",
        }
    per_node: dict[str, Any] = {}
    for shard_id, port in SHARD_PORTS.items():
        direct = MongoClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
        try:
            direct.admin.command("ping")
            cache = (direct.admin.command("serverStatus").get("wiredTiger") or {}).get("cache") or {}
            per_node[shard_id] = {
                "maximum_bytes_configured": cache.get("maximum bytes configured"),
                "bytes_currently_in_cache": cache.get("bytes currently in the cache"),
                "pages_read_into_cache": cache.get("pages read into cache"),
                "pages_evicted_by_application_threads": cache.get("pages evicted by application threads"),
            }
        except Exception as exc:
            per_node[shard_id] = {"error": str(exc)}
        finally:
            direct.close()
    configured = [v.get("maximum_bytes_configured") for v in per_node.values() if isinstance(v.get("maximum_bytes_configured"), (int, float))]
    return {
        "per_node": per_node,
        "maximum_cache_bytes_min": min(configured) if configured else None,
        "maximum_cache_bytes_max": max(configured) if configured else None,
        "note": "Collected directly from each shard. Filesystem cache is separate; all containers share host CPU and storage.",
    }


def _summarize_storage(role: str, stats: dict[str, Any], distribution: dict[str, Any] | None) -> dict[str, Any]:
    if role == "sharded" and distribution:
        data_bytes = sum(v["data_size_bytes"] for v in distribution["per_shard"].values())
        index_bytes = sum(v["index_size_bytes"] for v in distribution["per_shard"].values())
    else:
        data_bytes = int(stats.get("size", 0) or 0)
        index_bytes = int(stats.get("totalIndexSize", stats.get("indexSize", 0)) or 0)
    total_bytes = data_bytes + index_bytes
    return {
        "logical_data_bytes": data_bytes,
        "total_index_bytes": index_bytes,
        "logical_data_plus_indexes_bytes": total_bytes,
        "logical_data_plus_indexes_gib_rough_proxy": round(total_bytes / (1024**3), 4),
        "note": "This is a rough proxy (logical data size + index size), not a direct measurement of the in-memory working set.",
    }


def measure(args: argparse.Namespace) -> None:
    if not FIXTURE_PATH.exists():
        raise SystemExit(f"Fixed query-key file is missing: {FIXTURE_PATH}. Run --mode capture-keys while standalone is active, and keep the file for both phases.")
    fixture_bytes = FIXTURE_PATH.read_bytes()
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    fixture = json.loads(fixture_bytes.decode("utf-8"))
    if fixture.get("n_docs") != args.n_docs:
        raise SystemExit(f"Fixture n_docs={fixture.get('n_docs')} does not match --n-docs={args.n_docs}. Capture a new fixture for this dataset checkpoint before measuring.")
    keys = fixture.get("keys", [])
    if len(keys) < 100:
        raise SystemExit("The fixed query-key fixture has fewer than 100 keys; regenerate it with --mode capture-keys.")

    client = get_client(args.uri)
    try:
        hello = client.admin.command("hello")
        is_mongos = hello.get("msg") == "isdbgrid"
        if args.role == "single" and is_mongos:
            raise SystemExit("--role single requires standalone mongod, but the current connection is mongos. Stop shard compose and start single compose.")
        if args.role == "sharded" and not is_mongos:
            raise SystemExit("--role sharded requires mongos, but the current connection is standalone mongod. Switch Compose configuration first.")
        db = get_db(client, args.db)
        coll = db[args.collection]
        actual = coll.count_documents({})
        if actual != args.n_docs:
            raise SystemExit(f"Expected exactly {args.n_docs:,} documents in {coll.full_name}; found {actual:,}. Do not compare different data sizes.")

        if args.role == "single":
            _ensure_single_index(coll)
        else:
            _validate_sharded_collection(client, db.name, coll.name)
        _validate_sample_keys(coll, keys)

        stats = _collection_stats(db, coll.name)
        distribution = _shard_distribution(stats, client) if args.role == "sharded" else None
        explain_command = {
            "find": coll.name,
            "filter": {"review_id": keys[0]},
            "projection": {"_id": 1},
        }
        explain_sample = db.command("explain", explain_command, verbosity="executionStats")
        explain_summary = get_exec_stats(explain_sample)
        if explain_summary.get("keys_examined") == 0 and (explain_summary.get("docs_examined") or 0) > 1:
            print("WARNING: sample explain suggests the point query may not be using an index efficiently; inspect explain_sample in metadata.")
        if distribution:
            if distribution["active_shards"] < 2:
                raise SystemExit(
                    f"E5c invalid: only {distribution['active_shards']} shard(s) have documents according to collStats. "
                    "Keep the balancer enabled, inspect sh.status() and wait for distribution before measuring."
                )
            if distribution["max_shard_share_pct"] > 80:
                raise SystemExit(
                    f"E5c invalid: the largest shard holds {distribution['max_shard_share_pct']:.1f}% of documents. "
                    "Wait for balancing and verify chunk placement before measuring."
                )

        cache_before = _cache_snapshot(args.role, client)
        storage = _summarize_storage(args.role, stats, distribution)
        cache_values = []
        if args.role == "single":
            v = cache_before.get("per_node", {}).get("standalone", {}).get("maximum_bytes_configured")
            if isinstance(v, (int, float)):
                cache_values = [int(v)]
        else:
            cache_values = [
                int(v["maximum_bytes_configured"])
                for v in cache_before.get("per_node", {}).values()
                if isinstance(v.get("maximum_bytes_configured"), (int, float))
            ]
        cache_gib = min(cache_values) / (1024**3) if cache_values else None
        proxy_gib = storage["logical_data_plus_indexes_gib_rough_proxy"]
        largest_shard_gib = None
        if distribution:
            largest_shard_gib = max((v["data_plus_index_gib"] for v in distribution["per_shard"].values()), default=0.0)
        ratios = {
            "cache_gib_used_for_ratio": round(cache_gib, 4) if cache_gib is not None else None,
            "single_or_cluster_total_proxy_over_cache": round(proxy_gib / cache_gib, 3) if cache_gib else None,
            "largest_shard_proxy_gib": largest_shard_gib,
            "largest_shard_proxy_over_smallest_shard_cache": round(largest_shard_gib / cache_gib, 3) if cache_gib and largest_shard_gib is not None else None,
            "target_guide": "For the cache-wall comparison, the rough proxy D may target about 2.5-2.8 x single-node cache while largest-shard D should be at or below about 0.9 x shard cache. This proxy is approximate; report actual values and do not claim a cache-wall result if the target is not met.",
        }
        if args.role == "single" and cache_gib and proxy_gib / cache_gib < 2.5:
            print("WARNING: logical data + index proxy is under 2.5x the WiredTiger cache. E5c may be exploratory rather than a strong cache-wall test.")
        if args.role == "sharded" and cache_gib and largest_shard_gib is not None and largest_shard_gib / cache_gib > 1.0:
            print("WARNING: largest shard's logical data + index proxy exceeds its WiredTiger cache. The per-shard working set may not fit; interpret the comparison conditionally.")

        # Same seeded query order in both configurations and all rounds.
        ordered_keys = list(keys)
        random.Random(int(fixture.get("seed", 42))).shuffle(ordered_keys)
        raw_rows: list[dict[str, Any]] = []
        round_rows: list[dict[str, Any]] = []
        print(f"E5c role={args.role} collection={coll.full_name} n_docs={actual:,}; warmup={args.warmup:,}; ops/round={args.ops:,}; rounds={args.rounds}")
        for round_no in range(1, args.rounds + 1):
            round_timestamp = now_iso()
            for i in range(args.warmup):
                coll.find_one({"review_id": ordered_keys[i % len(ordered_keys)]}, {"_id": 1})
            latencies: list[float] = []
            found_count = 0
            started = time.perf_counter()
            for i in range(args.ops):
                key = ordered_keys[i % len(ordered_keys)]
                t0 = time.perf_counter()
                result = coll.find_one({"review_id": key}, {"_id": 1})
                latency_ms = (time.perf_counter() - t0) * 1000
                found = result is not None
                found_count += int(found)
                latencies.append(latency_ms)
                raw_rows.append({
                    "timestamp": round_timestamp, "experiment": "E5c", "role": args.role,
                    "collection": coll.name, "n_docs": actual, "round": round_no,
                    "op": i + 1, "review_id": key, "elapsed_ms": round(latency_ms, 5), "found": found,
                })
            elapsed_s = time.perf_counter() - started
            perf = percentile_stats(latencies)
            round_row = {
                "timestamp": round_timestamp, "experiment": "E5c", "role": args.role,
                "collection": coll.name, "n_docs": actual, "round": round_no,
                "warmup_ops": args.warmup, "measured_ops": args.ops, "key_count": len(keys),
                **{k: round(v, 5) for k, v in perf.items()},
                "ops_s_elapsed_wall": round(args.ops / elapsed_s, 3) if elapsed_s else 0,
                "found_count": found_count, "missing_count": args.ops - found_count,
                "logical_data_plus_indexes_gib_rough_proxy": proxy_gib,
                "cache_gib": round(cache_gib, 4) if cache_gib is not None else None,
                "largest_shard_proxy_gib": largest_shard_gib,
                "shard_distribution_json": json.dumps(distribution or {}, ensure_ascii=False),
            }
            round_rows.append(round_row)
            print(f"  round {round_no}/{args.rounds}: p50={perf['p50_ms']:.3f} ms, p95={perf['p95_ms']:.3f} ms, ops/s={perf['ops_s']:.2f}, missing={args.ops-found_count}")

        role_file = RESULTS / f"e5c_{args.role}_rounds.csv"
        raw_file = RESULTS / f"e5c_{args.role}_raw.csv"
        metadata_file = RESULTS / f"e5c_{args.role}_metadata.json"
        write_csv(role_file, round_rows)
        write_csv(raw_file, raw_rows)
        cache_after = _cache_snapshot(args.role, client)
        metadata = {
            "timestamp": now_iso(), "experiment": "E5c", "role": args.role,
            "uri": args.uri or "from .env/default localhost:27017", "database": db.name,
            "collection": coll.name, "n_docs": actual, "fixture_file": str(FIXTURE_PATH),
            "fixture_sha256": fixture_sha256,
            "query_key_count": len(keys), "index_information": _index_info(coll),
            "storage_estimate": storage, "cache_snapshot_before": cache_before,
            "cache_snapshot_after": cache_after,
            "rough_cache_ratios": ratios, "shard_distribution": distribution,
            "explain_summary": explain_summary, "explain_sample": explain_sample,
            "rounds_file": str(role_file), "raw_file": str(raw_file),
            "interpretation_note": "This is a single laptop. All Docker containers share physical CPU/storage; measured latency includes Python/PyMongo round-trip and mongos routing for the sharded configuration. This does not establish production-scale throughput.",
        }
        write_json(metadata_file, metadata)
        print(f"Saved E5c results: {role_file}, {raw_file}, {metadata_file}")
    finally:
        client.close()


def summarize() -> None:
    single_path = RESULTS / "e5c_single_rounds.csv"
    shard_path = RESULTS / "e5c_sharded_rounds.csv"
    if not single_path.is_file() or not shard_path.is_file():
        raise SystemExit("Need both results/e5c_single_rounds.csv and results/e5c_sharded_rounds.csv before --mode summarize.")
    import csv

    def read_rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    single_rows = read_rows(single_path)
    shard_rows = read_rows(shard_path)
    if not single_rows or not shard_rows:
        raise SystemExit("One E5c rounds file is empty. Run both measurements first.")
    single_meta_path = RESULTS / "e5c_single_metadata.json"
    shard_meta_path = RESULTS / "e5c_sharded_metadata.json"
    if not single_meta_path.is_file() or not shard_meta_path.is_file():
        raise SystemExit("E5c metadata files are missing. Do not summarize until both role measurements are complete.")
    single_meta = json.loads(single_meta_path.read_text(encoding="utf-8"))
    shard_meta = json.loads(shard_meta_path.read_text(encoding="utf-8"))
    if single_meta.get("fixture_sha256") != shard_meta.get("fixture_sha256"):
        raise SystemExit("The fixed query-key fixture differs between standalone and sharded measurements. Re-run both roles using one unchanged results/e5c_query_keys.json.")
    single_n = {int(r["n_docs"]) for r in single_rows}
    shard_n = {int(r["n_docs"]) for r in shard_rows}
    if single_n != shard_n:
        raise SystemExit(f"Data-size mismatch: single={single_n}, sharded={shard_n}. Do not compare different n_docs.")
    if len(single_n) != 1:
        raise SystemExit(f"Single-node file has mixed data sizes: {single_n}.")
    n_docs = next(iter(single_n))
    match_fields = ["warmup_ops", "measured_ops", "key_count"]
    for field in match_fields:
        single_values = {int(r[field]) for r in single_rows}
        shard_values = {int(r[field]) for r in shard_rows}
        if single_values != shard_values:
            raise SystemExit(f"Workload mismatch for {field}: single={single_values}, sharded={shard_values}. Re-run both roles with identical settings.")
    if len(single_rows) != len(shard_rows):
        raise SystemExit(f"Round mismatch: single has {len(single_rows)} rounds, sharded has {len(shard_rows)}. Re-run both roles with identical --rounds.")
    fields = ["p50_ms", "p95_ms", "p99_ms", "mean_ms", "ops_s"]
    row: dict[str, Any] = {
        "timestamp": now_iso(), "experiment": "E5c", "n_docs": n_docs,
        "single_rounds": len(single_rows), "sharded_rounds": len(shard_rows),
        "fixture_sha256": single_meta.get("fixture_sha256"),
    }
    for field in fields:
        single_values = [float(r[field]) for r in single_rows]
        shard_values = [float(r[field]) for r in shard_rows]
        single_median = statistics.median(single_values)
        shard_median = statistics.median(shard_values)
        row[f"single_{field}_median"] = round(single_median, 5)
        row[f"sharded_{field}_median"] = round(shard_median, 5)
        if field != "ops_s":
            row[f"sharded_vs_single_{field}_change_pct"] = round((shard_median / single_median - 1) * 100, 3) if single_median else None
        else:
            row["sharded_vs_single_ops_s_change_pct"] = round((shard_median / single_median - 1) * 100, 3) if single_median else None
    row["interpretation_note"] = "Positive latency change means sharded latency was higher; negative means it was lower. Evaluate data distribution and cache ratios before attributing a result to sharding."
    output = RESULTS / "e5c_comparison.csv"
    write_csv(output, [row])
    write_json(RESULTS / "e5c_comparison.json", row)
    print(json.dumps(row, indent=2, ensure_ascii=False))
    print(f"Saved comparison: {output} and results/e5c_comparison.json")


def main() -> None:
    p = argparse.ArgumentParser(description="E5c: compare indexed random review_id lookup latency on standalone MongoDB and a 3-shard cluster.")
    p.add_argument("--mode", required=True, choices=["capture-keys", "measure", "summarize"])
    p.add_argument("--role", choices=["single", "sharded"], help="required for --mode measure")
    p.add_argument("--collection", default=None, help="single defaults to reviews; sharded defaults to reviews_e5c")
    p.add_argument("--n-docs", type=int, help="exact expected document count; required for capture-keys and measure")
    p.add_argument("--key-count", type=int, default=50000, help="number of fixed IDs sampled once from standalone")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--force", action="store_true", help="overwrite existing fixed query key fixture; do not use between the two roles")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--ops", type=int, default=50000, help="measured point lookups per round; 5000 is a pilot")
    p.add_argument("--warmup", type=int, default=20000, help="discarded warm-up lookups per round")
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    args = p.parse_args()

    if args.mode == "summarize":
        summarize()
        return
    if args.n_docs is None or args.n_docs < 1:
        p.error("--n-docs is required and must be positive for capture-keys/measure")
    if args.mode == "capture-keys":
        args.collection = args.collection or "reviews"
        capture_keys(args)
        return
    if args.role is None:
        p.error("--role single or --role sharded is required for --mode measure")
    args.collection = args.collection or ("reviews" if args.role == "single" else "reviews_e5c")
    if args.rounds < 1 or args.ops < 2 or args.warmup < 0:
        p.error("--rounds must be >= 1, --ops >= 2 and --warmup >= 0")
    measure(args)


if __name__ == "__main__":
    main()
