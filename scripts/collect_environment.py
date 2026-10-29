from __future__ import annotations

import argparse
import platform
import subprocess
from pathlib import Path

import psutil
import pymongo

from common import RESULTS, get_client, get_db, now_iso, write_csv, write_json


def command_output(args: list[str]) -> str | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except Exception:
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Collect environment/version details required by the report.")
    p.add_argument("--uri", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--disk-type", default="FILL-IN: SSD or HDD")
    args = p.parse_args()
    client = get_client(args.uri)
    build = client.admin.command("buildInfo")
    host_info = client.admin.command("hostInfo")
    info = {
        "captured_at": now_iso(), "os": platform.platform(), "python_version": platform.python_version(),
        "cpu": platform.processor() or platform.machine(), "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False), "host_ram_gib": round(psutil.virtual_memory().total / (1024**3), 2),
        "disk_type": args.disk_type, "docker_version": command_output(["docker", "--version"]),
        "docker_compose_version": command_output(["docker", "compose", "version"]),
        "mongodb_version": build.get("version"), "mongodb_git_version": build.get("gitVersion"),
        "pymongo_version": pymongo.version,
        "container_memory_visible_mb": host_info.get("system", {}).get("memSizeMB"),
        "container_memory_limit_mb": host_info.get("system", {}).get("memLimitMB"),
        "mongo_host_info": host_info.get("system", {}),
        "note": "Fill in disk type manually and capture docker stats --no-stream during E4/E5. container_memory_visible_mb and container_memory_limit_mb are different; host RAM is separate.",
    }
    write_json(RESULTS / "environment.json", info)
    write_csv(RESULTS / "environment.csv", [{k: str(v) if isinstance(v, (dict, list)) else v for k, v in info.items()}])
    print(info)
    client.close()


if __name__ == "__main__":
    main()
