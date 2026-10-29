from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
RESULTS.mkdir(parents=True, exist_ok=True)
FIGURES.mkdir(parents=True, exist_ok=True)


def get_client(uri: str | None = None) -> MongoClient:
    resolved = uri or os.getenv("MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(resolved, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)
    try:
        client.admin.command("ping")
    except Exception:
        client.close()
        raise
    return client


def get_db(client: MongoClient, db_name: str | None = None):
    return client[db_name or os.getenv("DB_NAME", "yelp")]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str] | None = None, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if not rows:
        return
    names = fieldnames or list(dict.fromkeys(k for row in rows for k in row.keys()))
    mode = "a" if append else "w"
    need_header = (not append) or (not path.exists()) or path.stat().st_size == 0
    with path.open(mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=names, extrasaction="ignore")
        if need_header:
            writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def percentile_stats(values: list[float]) -> dict[str, float]:
    import numpy as np
    a = np.asarray(values, dtype=float)
    if len(a) == 0:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "mean_ms": 0.0, "ops_s": 0.0}
    total_seconds = float(a.sum()) / 1000.0
    return {
        "p50_ms": float(np.percentile(a, 50)),
        "p95_ms": float(np.percentile(a, 95)),
        "p99_ms": float(np.percentile(a, 99)),
        "mean_ms": float(a.mean()),
        "ops_s": float(len(a) / total_seconds) if total_seconds > 0 else 0.0,
    }


def get_exec_stats(explain: dict[str, Any]) -> dict[str, Any]:
    stats = explain.get("executionStats", {})
    # On a sharded query, top-level stats may be absent/aggregated differently; retain shard-level totals too.
    docs = stats.get("totalDocsExamined")
    keys = stats.get("totalKeysExamined")
    time_ms = stats.get("executionTimeMillis")
    if docs is None or keys is None:
        docs_sum = 0
        keys_sum = 0
        times_sum = 0
        found = False
        def walk(x: Any) -> None:
            nonlocal docs_sum, keys_sum, times_sum, found
            if isinstance(x, dict):
                if "totalDocsExamined" in x or "totalKeysExamined" in x:
                    found = True
                    docs_sum += int(x.get("totalDocsExamined", 0) or 0)
                    keys_sum += int(x.get("totalKeysExamined", 0) or 0)
                    times_sum += int(x.get("executionTimeMillis", 0) or 0)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(explain)
        if found:
            docs = docs if docs is not None else docs_sum
            keys = keys if keys is not None else keys_sum
            time_ms = time_ms if time_ms is not None else times_sum
    return {"docs_examined": docs, "keys_examined": keys, "explain_ms": time_ms}


def stop_with_error(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)
