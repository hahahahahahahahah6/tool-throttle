"""Configuration for tool-throttle: per-tool quotas, JSON round-trip."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field


@dataclass
class Quota:
    """Rate quota: ``calls`` tokens per ``per_seconds`` seconds.

    ``burst`` caps the burst size (defaults to ``calls``).
    """
    calls: int
    per_seconds: float = 60.0
    burst: int | None = None

    def __post_init__(self) -> None:
        if self.calls <= 0:
            raise ValueError("calls must be > 0")
        if self.per_seconds <= 0:
            raise ValueError("per_seconds must be > 0")
        if self.burst is not None and self.burst <= 0:
            raise ValueError("burst must be > 0")

    @property
    def capacity(self) -> float:
        return float(self.burst if self.burst is not None else self.calls)

    @property
    def refill_per_second(self) -> float:
        return float(self.calls) / float(self.per_seconds)

    def to_dict(self) -> dict:
        return {"calls": self.calls, "per_seconds": self.per_seconds,
                "burst": self.burst}

    @classmethod
    def from_dict(cls, d: dict) -> "Quota":
        return cls(calls=d["calls"], per_seconds=d.get("per_seconds", 60.0),
                   burst=d.get("burst"))


#: What to do when a call exceeds its quota.
QUEUE = "queue"    #: block until a token is available (bounded by max_queue_wait)
REJECT = "reject"  #: fail fast with ThrottledError


@dataclass
class ThrottlerConfig:
    """Full throttle configuration.

    ``per_tool`` maps glob patterns (e.g. ``"web_*"``) to quotas.
    Patterns are matched with :func:`fnmatch.fnmatchcase` in insertion
    order; the first match wins. Tools matching nothing use ``default``.
    """
    default: Quota = field(default_factory=lambda: Quota(calls=60, per_seconds=60))
    per_tool: dict[str, Quota] = field(default_factory=dict)
    on_exceed: str = QUEUE
    max_queue_wait: float = 30.0

    def __post_init__(self) -> None:
        if self.on_exceed not in (QUEUE, REJECT):
            raise ValueError("on_exceed must be 'queue' or 'reject'")
        if self.max_queue_wait < 0:
            raise ValueError("max_queue_wait must be >= 0")

    def quota_for(self, tool: str) -> tuple[Quota, str | None]:
        """Return (quota, matched_pattern) for a tool name."""
        for pattern, quota in self.per_tool.items():
            if fnmatch.fnmatchcase(tool, pattern):
                return quota, pattern
        return self.default, None

    def to_dict(self) -> dict:
        return {
            "default": self.default.to_dict(),
            "per_tool": {p: q.to_dict() for p, q in self.per_tool.items()},
            "on_exceed": self.on_exceed,
            "max_queue_wait": self.max_queue_wait,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ThrottlerConfig":
        return cls(
            default=Quota.from_dict(d.get("default", {"calls": 60})),
            per_tool={p: Quota.from_dict(q)
                      for p, q in d.get("per_tool", {}).items()},
            on_exceed=d.get("on_exceed", QUEUE),
            max_queue_wait=d.get("max_queue_wait", 30.0),
        )

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "ThrottlerConfig":
        with open(path) as f:
            return cls.from_dict(json.load(f))
