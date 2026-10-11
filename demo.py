"""Quick demo: an agent stuck in a web_search loop gets fused."""

from tool_throttle import Quota, REJECT, Throttler, ThrottlerConfig, ThrottledError

th = Throttler(ThrottlerConfig(
    default=Quota(calls=60, per_seconds=60),
    per_tool={"web_search": Quota(calls=5, per_seconds=60)},
    on_exceed=REJECT,
))

allowed = rejected = 0
for i in range(12):
    try:
        th.acquire("web_search")
        allowed += 1
        print(f"call {i + 1}: allowed")
    except ThrottledError as e:
        rejected += 1
        print(f"call {i + 1}: REJECTED (retry in {e.retry_after:.0f}s)")

print(f"\n{allowed} allowed, {rejected} rejected -- quota 5/min fused the loop")
print("stats:", th.stats())
