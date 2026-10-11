"""CLI for tool-throttle.

Subcommands:
  check   read {"tool": ...} JSON lines from stdin, print a throttling
          decision per line (dry-run: does NOT consume tokens).
  acquire read {"tool": ...} JSON lines from stdin, apply the throttle
          (consumes tokens; queues or rejects), print the decision.
  demo    run a built-in scenario showing queue vs reject behaviour.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import QUEUE, REJECT, Quota, ThrottlerConfig
from .throttler import ThrottledError, Throttler


def demo_config() -> ThrottlerConfig:
    return ThrottlerConfig(
        default=Quota(calls=60, per_seconds=60),
        per_tool={
            "web_search": Quota(calls=10, per_seconds=60),
            "expensive_*": Quota(calls=2, per_seconds=60),
        },
        on_exceed=REJECT,
        max_queue_wait=5.0,
    )


def _iter_stdin():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"bad JSON: {e}"}), flush=True)


def cmd_check(args) -> int:
    cfg = ThrottlerConfig.load(args.config) if args.config else demo_config()
    th = Throttler(cfg)
    for obj in _iter_stdin():
        tool = obj.get("tool", "")
        d = th.decide(tool)
        print(json.dumps({"tool": d.tool, "allowed": d.allowed,
                          "wait_seconds": round(d.wait_seconds, 3),
                          "reason": d.reason, "pattern": d.pattern}),
              flush=True)
    return 0


def cmd_acquire(args) -> int:
    cfg = ThrottlerConfig.load(args.config) if args.config else demo_config()
    th = Throttler(cfg)
    for obj in _iter_stdin():
        tool = obj.get("tool", "")
        try:
            d = th.acquire(tool)
            print(json.dumps({"tool": d.tool, "allowed": True,
                              "wait_seconds": round(d.wait_seconds, 3),
                              "reason": d.reason, "pattern": d.pattern}),
                  flush=True)
        except ThrottledError as e:
            print(json.dumps({"tool": tool, "allowed": False,
                              "wait_seconds": round(e.retry_after, 3),
                              "reason": e.reason, "pattern": None}),
                  flush=True)
    return 0


def cmd_demo(_args) -> int:
    print("== tool-throttle demo: an agent looping web_search ==")
    cfg = ThrottlerConfig(
        default=Quota(calls=60, per_seconds=60),
        per_tool={"web_search": Quota(calls=5, per_seconds=60)},
        on_exceed=REJECT,
    )
    th = Throttler(cfg)
    allowed, rejected = 0, 0
    for _ in range(12):
        try:
            th.acquire("web_search")
            allowed += 1
        except ThrottledError:
            rejected += 1
    print(f"web_search: {allowed} allowed, {rejected} rejected "
          f"(quota 5/min) -- the loop is fused, not looping forever")
    print(f"stats: {json.dumps(th.stats(), indent=2)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tool-throttle",
                                description="MCP tool-call throttler")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (
            ("check", cmd_check, "dry-run decisions for stdin JSON lines"),
            ("acquire", cmd_acquire, "consume tokens for stdin JSON lines"),
            ("demo", cmd_demo, "show throttling on a looping agent")):
        s = sub.add_parser(name, help=help_)
        if name != "demo":
            s.add_argument("--config", default=None,
                           help="path to a throttle config JSON file")
        s.set_defaults(func=fn)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
