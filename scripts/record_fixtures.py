"""Record real HAFAS departures and stop-search fixtures (charter G1).

Offline by default: `--help`, importing this module and `--trim` never touch
the network. `--record` makes exactly one GET per stop given, at least 1.5 s
apart, and never loops or retries -- the public instance is rate-limited.
`--locations` does the same, one GET /locations per query.

    uv run python scripts/record_fixtures.py --record 900100001 900024102 -o raw/
    uv run python scripts/record_fixtures.py --trim raw/900100001.json \\
        -o tests/fixtures/hafas/recorded/departures_replacement_service.json
    uv run python scripts/record_fixtures.py --locations Alexanderplatz Ostkreuz \\
        -o tests/fixtures/hafas/recorded/

Trimming keeps every departure in response order and only drops fields the
engine does not read; it never rewrites a value or drops a departure. The
same holds for --locations: each entry keeps only type, id and name (plus
station id/name when present), in response order, values unedited.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

BASE_URL = "https://v6.bvg.transport.rest"
_DEPARTURE_KEYS = ("tripId", "when", "plannedWhen", "delay", "cancelled")
_LINE_KEYS = ("id", "name", "product")
_STOP_KEYS = ("id", "name")
_REMARK_KEYS = ("id", "type", "code", "summary", "text")
_LOCATION_KEYS = ("type", "id", "name")
_LOCATION_STATION_KEYS = ("id", "name")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _pick(source: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    source = source or {}
    return {key: source.get(key) for key in keys}


def trim(response: dict[str, Any]) -> dict[str, Any]:
    departures = []
    for departure in response.get("departures") or []:
        trimmed = _pick(departure, _DEPARTURE_KEYS)
        trimmed["line"] = _pick(departure.get("line"), _LINE_KEYS)
        trimmed["stop"] = _pick(departure.get("stop"), _STOP_KEYS)
        trimmed["remarks"] = [_pick(r, _REMARK_KEYS) for r in departure.get("remarks") or []]
        departures.append(trimmed)
    return {"departures": departures}


def trim_locations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed = []
    for entry in entries:
        item = _pick(entry, _LOCATION_KEYS)
        station = entry.get("station")
        if isinstance(station, dict):
            item["station"] = _pick(station, _LOCATION_STATION_KEYS)
        trimmed.append(item)
    return trimmed


def _slugify(query: str) -> str:
    return _SLUG_RE.sub("_", query.strip().lower()).strip("_")


def record_locations(queries: list[str], out_dir: Path, results: int) -> None:
    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        for index, query in enumerate(queries):
            if index:
                time.sleep(1.5)
            recorded_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            response = client.get("/locations", params={"query": query, "results": results})
            response.raise_for_status()
            entries = trim_locations(response.json())
            out_path = out_dir / f"locations_{_slugify(query)}.json"
            out_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n")
            print(f"{query}: {len(entries)} entries recorded at {recorded_at} -> {out_path}")


def record(stop_ids: list[str], out_dir: Path, duration: int) -> None:
    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        for index, stop_id in enumerate(stop_ids):
            if index:
                time.sleep(1.5)
            recorded_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            response = client.get(
                f"/stops/{stop_id}/departures",
                params={"duration": duration, "results": 200, "remarks": "true"},
            )
            response.raise_for_status()
            payload = {"_recorded_at": recorded_at, **response.json()}
            (out_dir / f"{stop_id}.json").write_text(json.dumps(payload, ensure_ascii=False))
            print(f"{stop_id}: {len(payload.get('departures') or [])} departures")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", nargs="+", metavar="STOP_ID", help="live GET, one per stop")
    mode.add_argument("--trim", type=Path, metavar="RAW_JSON", help="trim one recorded file")
    mode.add_argument(
        "--locations", nargs="+", metavar="QUERY", help="live GET /locations, one per query"
    )
    parser.add_argument("-o", "--out", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--results", type=int, default=5)
    args = parser.parse_args(argv)

    if args.record:
        record(args.record, args.out, args.duration)
    elif args.locations:
        record_locations(args.locations, args.out, args.results)
    else:
        raw = json.loads(args.trim.read_text())
        args.out.write_text(json.dumps(trim(raw), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
