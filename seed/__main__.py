"""python -m seed --data-dir data/demo --days 30 --seed 42 --people 5000"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from .world import seed


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m seed", description="Write demo history into a data directory.")
    ap.add_argument("--data-dir", default="data/demo")
    ap.add_argument("--policy", default="policy.yaml")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--people", type=int, default=5000, help="people besides the 12 named demo people")
    ap.add_argument("--now", type=float, default=None, help="end of the history (unix time); default: now")
    ap.add_argument("--keep", action="store_true", help="add to an existing data directory instead of replacing it")
    args = ap.parse_args()
    data = Path(args.data_dir)
    if data.exists() and not args.keep:
        shutil.rmtree(data)
    t0 = time.time()
    stats = seed(Path(args.policy), data, days=args.days, rng_seed=args.seed, now=args.now, people=args.people)
    print(
        f"seeded {data}: {stats['people']} people, {stats['events']} events, {stats['usage_rows']} usage rows, "
        f"${stats['usd']:.2f}, {stats['incidents']} incidents, {stats['leases']} leases in {time.time() - t0:.1f}s"
    )
    print(f"run it:  python -m controllayer --data-dir {data}")


if __name__ == "__main__":
    main()
