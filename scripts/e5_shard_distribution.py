from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from common import RESULTS, get_client, get_db, now_iso, write_csv, write_json

SHARD_PORTS = {"shard1": 27101, "shard2": 27102, "shard3": 27103}
SHARD_ID_TO_SERVICE = {"sh1": "shard1", "sh2": "shard2", "sh3": "shard3", "shard1": "shard1", "shard2": "shard2", "shard3": "shard3"}


def counter(shard: str) -> int:
    client = MongoClient(f"mongodb://localhost:{SHARD_PORTS[shard]}/?directConnection=true", serverSelectionTimeoutMS=5000)
    try:
        return int(client.admin.command("serverStatus").get("opcounters", {}).get("insert", 0))
    finally:
        client.close()


def iter_docs(path: Path, start: int, limit: int):
    with path.open("r", encoding="utf-8") as f:
        read = 0
        for lineno, line in enumerate(f):
            if lineno < start:
                continue
            if read >= limit:
                break
            if not line.strip():
                continue
            doc = json.loads(line)
            doc.pop("_id", None)
            doc["seq"] = lineno
            yield doc
            read += 1


def load_target(db, name: str, path: Path, start: int, limit: int, batch_size: int) -> dict[str, Any]:
    coll = db[name]
    existing = coll.count_documents({})
    if existing:
        raise SystemExit(f"{name} already has {existing:,} documents. Use a fresh cluster/collection or intentionally clear it before this run.")
    before = {s: counter(s) for s in SHARD_PORTS}
    started = time.perf_counter()
    buf = []
    inserted = 0
    for doc in iter_docs(path, start, limit):
        buf.append(doc)
        if len(buf) >= batch_size:
            coll.insert_many(buf, ordered=False)
            inserted += len(buf)
            buf = []
            if inserted and inserted % 50000 == 0:
                print(f"{name}: inserted {inserted:,}...")
    if buf:
        coll.insert_many(buf, ordered=False)
        inserted += len(buf)
    elapsed = time.perf_counter() - started
    after = {s: counter(s) for s in SHARD_PORTS}
    deltas = {s: after[s] - before[s] for s in SHARD_PORTS}
    total = sum(deltas.values())
    collstats = db.command({"collStats": name, "scale": 1})
    raw_shard_counts = {s: int(v.get("count", 0) or 0) for s, v in collstats.get("shards", {}).items()}
    # MongoDB shard IDs come from addShard() (e.g. sh1), while direct counter ports use service names (shard1).
    shard_doc_counts = {SHARD_ID_TO_SERVICE.get(s, s): n for s, n in raw_shard_counts.items()}
    shard_doc_total = sum(shard_doc_counts.values())
    return {
        "timestamp": now_iso(), "collection": name, "source_file": str(path), "start": start,
        "requested_limit": limit, "inserted_by_client": inserted, "elapsed_s": round(elapsed, 3),
        "client_docs_per_s": round(inserted / elapsed, 2) if elapsed else 0,
        "counter_before": before, "counter_after": after, "insert_op_delta": deltas,
        "insert_share_pct": {s: round(100 * deltas[s] / total, 2) if total else 0 for s in SHARD_PORTS},
        "shard_doc_counts_from_collStats": shard_doc_counts,
        "shard_doc_share_pct": {s: round(100 * count / shard_doc_total, 2) if shard_doc_total else 0 for s, count in shard_doc_counts.items()},
        "collStats_shard_names": list(collstats.get("shards", {}).keys()),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="E5a: insert monotonically increasing seq into range and hashed-shard-key collections; capture per-shard counters.")
    p.add_argument("--file", required=True, help="Yelp review JSONL or generated reviews.jsonl")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int, default=100000, help="use 100k for the first controlled pass; larger batches may trigger chunk changes")
    p.add_argument("--batch-size", type=int, default=2000)
    p.add_argument("--uri", default=None, help="mongos endpoint; normally mongodb://localhost:27017")
    p.add_argument("--db", default=None)
    args = p.parse_args()
    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Source file does not exist: {path}")
    client = get_client(args.uri)
    db = get_db(client, args.db)
    rows = []
    for name in ["reviews_range", "reviews_hashed_seq"]:
        print(f"\nLoading {args.limit:,} records into {name}...")
        rows.append(load_target(db, name, path, args.start, args.limit, args.batch_size))
    write_json(RESULTS / "e5a_distribution_details.json", rows)
    flat = []
    for row in rows:
        for shard in SHARD_PORTS:
            flat.append({
                "timestamp": row["timestamp"], "collection": row["collection"], "shard": shard,
                "insert_op_delta": row["insert_op_delta"].get(shard, 0),
                "insert_share_pct": row["insert_share_pct"].get(shard, 0),
                "client_inserted": row["inserted_by_client"], "elapsed_s": row["elapsed_s"],
                "docs_in_shard_collStats": row["shard_doc_counts_from_collStats"].get(shard),
                "docs_share_pct": row["shard_doc_share_pct"].get(shard, 0),
            })
    write_csv(RESULTS / "e5a_shard_distribution.csv", flat)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    client.close()


if __name__ == "__main__":
    main()
