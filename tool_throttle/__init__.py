"""tool-throttle: MCP tool-call throttling (token bucket per tool name)."""

from .bucket import TokenBucket
from .config import QUEUE, REJECT, Quota, ThrottlerConfig
from .throttler import Decision, ThrottledError, Throttler

__version__ = "0.1.0"
__all__ = [
    "TokenBucket",
    "Quota",
    "ThrottlerConfig",
    "QUEUE",
    "REJECT",
    "Decision",
    "ThrottledError",
    "Throttler",
]
