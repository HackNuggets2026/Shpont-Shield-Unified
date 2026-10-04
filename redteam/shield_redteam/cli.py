"""shield-redteam: serve the sidecar, run the corpus once (CI), or self-test the signature feed."""

from __future__ import annotations

import argparse
import json
import sys

from .config import load
from .core.target import GatewayTarget
from .poligon import corpus
from .poligon.feedcheck import check_feed
from .poligon.mutate import mutants
from .poligon.runner import run
from .poligon.score import report


def _pct(x: float | None) -> str:
    return " n/a" if x is None else f"{x * 100:5.1f}%"


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server.app import create_app

    cfg = load(args.config)
    uvicorn.run(create_app(cfg), host=cfg.server["host"], port=int(cfg.server["port"]), log_level="warning")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    cases = corpus.load_dir(cfg.resolve(cfg.poligon["corpus"]))
    if cfg.poligon["mutate"] and not args.no_mutants:
        cases += mutants(cases)
    target = GatewayTarget(cfg.target["url"], cfg.target["admin_token"])
    rep = report(run(target, cases, workers=int(cfg.poligon["workers"])))
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        a, b, m, lat = rep["attacks"], rep["benign"], rep["mutants"], rep["latency_ms"]
        print(f"target        {target.name}   config {rep['fingerprint'].split('|')[0]}")
        print(f"posture       {rep['posture']} / 100")
        print(f"protection   {_pct(rep['protection'])}  {a['stopped']}/{a['total']} attacks stopped")
        print(f"friction     {_pct(rep['friction'])}  {b['stopped']}/{b['total']} benign stopped")
        print(f"evasion      {_pct(rep['evasion'])}  {m['stopped']}/{m['total']} mutants stopped")
        print(f"latency       p50 {lat['p50']} ms   p95 {lat['p95']} ms   ({rep['cases']} cases, {rep['seconds']} s)")
        print()
        print(f"{'category':28} {'attacks':>8} {'stopped':>8} {'benign':>7} {'false+':>7}")
        for g in rep["by_category"]:
            print(f"{g['name']:28} {g['attacks']:>8} {g['stopped']:>8} {g['benign']:>7} {g['false_positives']:>7}")
        for title, key in (("OPEN ATTACKS", "open_attacks"), ("FALSE POSITIVES", "false_positives")):
            if rep[key]:
                print(f"\n{title}")
                for o in rep[key]:
                    print(f"  {o['id']:32} {o['action']:7} {o['text'][:70]}")
    if rep["errors"]:
        print(f"\n{rep['errors']} cases errored (is the gateway reachable?)", file=sys.stderr)
        return 2
    if args.min_posture is not None and (rep["posture"] or 0) < args.min_posture:
        print(f"\nposture {rep['posture']} is below the required {args.min_posture}", file=sys.stderr)
        return 1
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    from .server.app import load_feed

    cfg = load(args.config)
    feed, err = load_feed(cfg)
    if err:
        print(err, file=sys.stderr)
    if args.check:
        res = check_feed(feed)
        for row in res["rows"]:
            print(
                f"{'ok ' if row['ok'] else 'BAD'} {row['id']:28} vectors={row['vectors']} {'; '.join(row['problems'])}"
            )
        print(f"{res['signatures']} signatures, {res['broken']} broken, {res['self_tested']} carry their own tests")
        return 1 if res["broken"] or err else 0
    print(json.dumps(feed, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shield-redteam")
    ap.add_argument("-c", "--config", default="redteam.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="the sidecar: continuous self-attack and its API").set_defaults(fn=cmd_serve)
    r = sub.add_parser("run", help="fire the corpus once and print the posture")
    r.add_argument("--json", action="store_true")
    r.add_argument("--no-mutants", action="store_true")
    r.add_argument("--min-posture", type=float, help="exit 1 if the posture score is below this (for CI)")
    r.set_defaults(fn=cmd_run)
    f = sub.add_parser("feed", help="print the gateway's signature feed, or self-test it with --check")
    f.add_argument("--check", action="store_true")
    f.set_defaults(fn=cmd_feed)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
