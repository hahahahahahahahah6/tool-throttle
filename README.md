# tool-throttle

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![No dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/pypi/v/tool-throttle.svg)](https://pypi.org/project/tool-throttle/)

A token-bucket rate limiter for **MCP tool calls** — a fuse against runaway
agents. When an agent gets stuck in a loop hammering `web_search` 400 times a
minute, tool-throttle slows it down or stops it, per tool name, with per-tool
quotas you configure in JSON.

Zero dependencies. Standard library only. Thread-safe.

```bash
pip install tool-throttle
```

```python
from tool_throttle import Throttler, ThrottlerConfig, Quota, REJECT

th = Throttler(ThrottlerConfig(
    default=Quota(calls=60, per_seconds=60),      # 60 calls/min for everything
    per_tool={
        "web_search":  Quota(calls=10, per_seconds=60),  # tight leash
        "expensive_*": Quota(calls=2, per_seconds=60),   # glob patterns work
    },
    on_exceed=REJECT,   # or "queue": wait for a token, up to max_queue_wait
))

# wrap any callable -- your MCP tool dispatcher, a client method, anything:
safe_search = th.wrap("web_search", mcp_client.call_tool)
safe_search("web_search", {"query": "..."})   # -> ThrottledError past quota

# or decide explicitly without consuming a token:
d = th.decide("web_search")
# Decision(tool='web_search', allowed=False, wait_seconds=42.1, ...)

th.stats()  # per-tool allowed / rejected / queued / waited_seconds
```

Config round-trips through JSON, so quotas can live in a file:

```python
cfg = ThrottlerConfig(
    default=Quota(calls=60, per_seconds=60),
    per_tool={"web_*": Quota(calls=10, per_seconds=60, burst=3)},
    on_exceed="queue",
    max_queue_wait=30.0,
)
cfg.save("throttle.json")
```

## CLI

```bash
# dry-run: print a decision per {"tool": ...} JSON line on stdin
echo '{"tool": "web_search"}' | tool-throttle check --config throttle.json

# enforce: consume tokens (queues or rejects over-quota calls)
echo '{"tool": "web_search"}' | tool-throttle acquire --config throttle.json

# built-in demo of a looping agent being fused
tool-throttle demo
```

## How it works

Each distinct tool name gets its own `TokenBucket`, sized by the first
matching quota in `per_tool` (glob patterns, insertion order) or `default`.
Buckets refill steadily over time, so a `Quota(calls=10, per_seconds=60)`
allows 10 calls per minute sustained, with a burst of up to `burst`
(default: `calls`).

On quota exhaustion:
- `on_exceed="reject"` (default in the demo): raise `ThrottledError` with a
  `retry_after` hint — fail fast.
- `on_exceed="queue"`: block until a token frees up, bounded by
  `max_queue_wait` seconds; if the wait would exceed the bound, raise
  `ThrottledError(reason="queue_wait_exceeded")`.

## Differentiation: why not just rate-limit at the API gateway?

API gateways throttle **HTTP requests**. That is the wrong layer for agent
safety:

- One HTTP request can fan out into many tool calls; one tool call pattern
  can span many HTTP endpoints. Gateway counters never see *tool-call
  semantics* — they see bytes and paths.
- Gateway limits are per API key / per IP. An agent looping on a *single
  expensive tool* (say, a search tool that costs $0.02 per call) looks
  perfectly normal at the HTTP layer while burning money at the tool layer.
- Quotas here are expressed in the language of the agent: *tool names and
  glob patterns* (`expensive_*`), with per-tool bursts and queue-or-reject
  policies — the thing an agent developer actually wants to tune.

tool-throttle is the **fuse at the MCP tool-call semantic layer**: it sits
between the agent's tool-call intent and execution, trips before the loop
does real damage, and reports per-tool stats so you can see which tool the
agent was abusing. It complements gateway rate limiting; it does not
replace it.

## API reference

| Symbol | Description |
|---|---|
| `Throttler(config)` | the interceptor; one per agent/process |
| `ThrottlerConfig(default, per_tool, on_exceed, max_queue_wait)` | quotas + policy |
| `Quota(calls, per_seconds=60, burst=None)` | rate quota for one pattern |
| `th.acquire(tool)` | consume a token; queues or raises `ThrottledError` |
| `th.decide(tool)` | dry-run check, does not consume |
| `th.wrap(tool, fn)` / `th.call(tool, fn, *a, **k)` | throttle a callable |
| `th.stats()` / `th.reset(tool?)` / `th.clear()` | observability and reset |
| `ThrottledError` | `.tool`, `.retry_after`, `.reason` |
| `TokenBucket(capacity, refill_per_second, clock=...)` | the primitive underneath |

## License

MIT — see [LICENSE](LICENSE).
