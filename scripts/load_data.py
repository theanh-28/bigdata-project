from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from pymongo.errors import BulkWriteError

from common import RESULTS, get_client, get_db, now_iso, write_csv


def main() -> None:
    p = argparse.ArgumentParser(description="Stream JSONL/JSON-lines source data into MongoDB in batches.")
    p.add_argument("--file", required=True, help="JSON Lines input file (one JSON document per line)")
    p.add_argument("--collection", required=True, help="Target collection, e.g. business or reviews")
    p.add_argument("--entity", choices=["business", "reviews", "other"], default="other", help="Only reviews receive the stable seq field")
    p.add_argument("--start", type=int, default=0, help="Zero-based source line offset; use for incremental loads")
    p.add_argument("--limit", type=int, required=True, help="Maximum number of source records; never means unlimited")
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--reset", action="store_true", help="Drop the target collection before loading; destructive")
    args = p.parse_args()
    if args.start < 0 or args.limit < 1 or args.batch_size < 1:
        p.error("--start must be >= 0; --limit and --batch-size must be positive")
    source = Path(args.file)
    if not source.is_file():
        p.error(f"Input file not found: {source}")

    client = get_client(args.uri)
    db = get_db(client, args.db)
    coll = db[args.collection]
    if args.reset:
        print(f"WARNING: dropping {db.name}.{args.collection} as requested by --reset")
        coll.drop()
        coll = db[args.collection]
    before = coll.count_documents({})
    started = time.perf_counter()
    buffer: list[dict[str, Any]] = []
    read_count = 0
    inserted_count = 0
    failed_count = 0
    last_line = args.start - 1

    def flush() -> None:
        nonlocal inserted_count, failed_count, buffer
        if not buffer:
            return
        try:
            result = coll.insert_many(buffer, ordered=False)
            inserted_count += len(result.inserted_ids)
        except BulkWriteError as e:
            details = e.details or {}
            inserted_count += int(details.get("nInserted", 0))
            errs = details.get("writeErrors", [])
            failed_count += len(errs)
            print(f"BulkWriteError: inserted={details.get('nInserted', 0)}, writeErrors={len(errs)}")
            if len(errs) and len(errs) <= 5:
                for err in errs:
                    print(f"  index={err.get('index')} code={err.get('code')} msg={err.get('errmsg')}")
        buffer = []

    with source.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f):
            if line_no < args.start:
                continue
            if read_count >= args.limit:
                break
            if not line.strip():
                continue
            doc = json.loads(line)
            if not isinstance(doc, dict):
                raise ValueError(f"Line {line_no + 1} is not a JSON object")
            if args.entity == "reviews":
                doc["seq"] = line_no
            else:
                doc.pop("seq", None)
            buffer.append(doc)
            read_count += 1
            last_line = line_no
            if len(buffer) >= args.batch_size:
                flush()
                if inserted_count and inserted_count % 50000 < args.batch_size:
                    elapsed = time.perf_counter() - started
                    print(f"progress read={read_count:,}/{args.limit:,}, inserted={inserted_count:,}, elapsed={elapsed:.1f}s")
    flush()
    elapsed = time.perf_counter() - started
    after = coll.count_documents({})
    row = {
        "timestamp": now_iso(), "db": db.name, "collection": args.collection, "source_file": str(source),
        "start_line": args.start, "limit": args.limit, "read": read_count, "inserted": inserted_count,
        "write_errors": failed_count, "count_before": before, "count_after": after,
        "elapsed_s": round(elapsed, 3), "docs_per_s": round(inserted_count / elapsed, 2) if elapsed else 0,
    }
    out = RESULTS / "load_log.csv"
    write_csv(out, [row], append=True)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    if failed_count:
        raise SystemExit("Load finished with write errors. Investigate before continuing.")
    client.close()


if __name__ == "__main__":
    main()
