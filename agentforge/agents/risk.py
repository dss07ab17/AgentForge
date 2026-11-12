"""Risk Analyst: risks, trade-offs and failure scenarios, tied to the actual design decisions."""
from __future__ import annotations

from ..models import AgentResult, RequirementType, Risk, Task
from .base import Agent


def url_shortener_risks(opts: dict) -> tuple[list[Risk], list[dict], list[dict]]:
    risks = [
        Risk("Abuse for phishing/malware redirects", "security", "high", "high",
             "Rate limiting on creation is in place; add URL reputation checks (e.g. Safe Browsing) and an abuse report/takedown flow before public launch" if opts.get("rate_limit") else
             "NOT mitigated: rate limiting was descoped - add before public launch"),
        Risk("Open, unauthenticated write/delete API", "security", "high", "high",
             "Prototype assumption. Add API keys/OAuth and per-owner authorization before exposure; DELETE must be "
             "owner-only."),
        Risk("SQLite write contention at the stated scale target", "scalability", "high", "medium",
             "Repository protocol isolates storage; migrate to PostgreSQL (+ read replicas, sharding by code hash)."),
        Risk("Analytics loss on crash or queue overflow", "data", "medium", "low",
             "Batched flush every 0.5s bounds loss; dropped events are counted and exposed on /healthz; move to "
             "Kafka for durable ingestion."),
        Risk("Per-instance cache serves stale data after delete", "reliability", "medium", "medium",
             "TTL bounds staleness; deletes invalidate locally; use shared Redis with pub/sub invalidation when "
             "running multiple instances."),
        Risk("Hot links overload a single DB row (click_count)", "scalability", "medium", "medium",
             "Counter updates are batched per flush; at higher scale use sharded counters or stream aggregation."),
        Risk("In-process rate limits are per instance", "security", "medium", "low",
             "Acceptable for prototype; use a shared token bucket (Redis) behind a load balancer.")
        if opts.get("rate_limit") else
        Risk("No abuse protection on link creation", "security", "high", "high",
             "Add rate limiting and URL reputation checks before launch."),
    ]
    if opts.get("expiry"):
        risks.append(Risk("Expired rows accumulate", "data", "medium", "low",
                          "Indexed expires_at enables a periodic purge job (not yet implemented)."))
    if opts.get("user_accounts") == "in scope":
        risks.append(Risk("User accounts requested but not implemented in this iteration", "delivery", "high",
                          "high", "Track as follow-up epic: identity provider, ownership column, authz on every route."))
    tradeoffs = [
        {"choice": "302 instead of 301", "gain": "accurate analytics, links can expire/be deleted",
         "cost": "every click hits the service (no browser caching)"},
        {"choice": "Async analytics", "gain": "redirect latency independent of analytics writes",
         "cost": "stats are eventually consistent (pending_events exposed); small loss window on crash"},
        {"choice": "Random codes", "gain": "non-enumerable, coordination-free",
         "cost": "requires uniqueness check + retry; slightly longer codes than a counter"},
        {"choice": "Stdlib WSGI + SQLite", "gain": "zero dependencies, trivially runnable",
         "cost": "not the production stack; swap required for the scale target"},
    ]
    failures = [
        {"scenario": "Database unavailable", "behaviour": "API returns 500 with generic error; /healthz reports "
         "degraded; analytics flush failures are logged and retried next interval"},
        {"scenario": "Code collision", "behaviour": "unique constraint -> retry up to 5 times -> 503 (never overwrites)"},
        {"scenario": "Traffic spike on one link", "behaviour": "served from cache; click queue absorbs bursts, "
         "sheds load (counted) when full"},
        {"scenario": "Malformed / oversized input", "behaviour": "400/413 with stable error shape; no stack traces"},
    ]
    return risks, tradeoffs, failures


def brownfield_risks(impact: dict, design: dict) -> tuple[list[Risk], list[dict], list[dict]]:
    risks = [Risk(f"Pre-existing issue in {h['file']}:{h['line']}: {h['issue']}", "reliability", "medium",
                  "medium" if "REPLACE" not in h["code"] else "high",
                  "Fixed by this change" if "REPLACE" in h["code"] else "Out of scope; flagged for follow-up")
             for h in impact.get("risk_hotspots", [])]
    risks += [
        Risk("Schema migration on live databases", "data", "low", "high",
             "Additive nullable column, idempotent, tested against a v1 database; take a backup before deploy."),
        Risk("Callers relying on old store.get_url signature", "delivery", "low", "medium",
             "Kept get_url as a backward-compatible wrapper; new code uses get_link."),
        Risk("Clock skew across instances affects expiry", "reliability", "low", "low",
             "Expiry uses server time at resolve; acceptable at second-level TTL granularity."),
    ]
    tradeoffs = [{"choice": "Retry on collision vs longer codes", "gain": "keeps existing 5-char code format",
                  "cost": "bounded retries; code space (26^5 ~ 11.9M) will need longer codes as volume grows"},
                 {"choice": "410 Gone for expired links", "gain": "clients can distinguish expired from unknown",
                  "cost": "reveals that a code once existed"}]
    failures = [{"scenario": "Code space nearly exhausted", "behaviour": "503 after MAX_CODE_ATTEMPTS; monitor and "
                 "increase CODE_LENGTH"},
                {"scenario": "Old database without expires_at", "behaviour": "migrated in place on first connect"}]
    return risks, tradeoffs, failures


class RiskAnalyst(Agent):
    name = "risk"
    role = "Risk Analyst"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        if spec.req_type != RequirementType.GREENFIELD:
            risks, tradeoffs, failures = brownfield_risks(ctx.blackboard.require("impact"),
                                                          ctx.blackboard.get("change_design") or {})
        elif spec.domain == "url_shortener":
            risks, tradeoffs, failures = url_shortener_risks(spec.options)
        else:
            risks, tradeoffs, failures = [Risk("Generic domain without blueprint", "delivery", "high", "high",
                                               "Use LLM mode and review outputs carefully")], [], []
        return AgentResult(outputs={"risks": {"risks": [r.to_dict() for r in risks], "tradeoffs": tradeoffs,
                                              "failure_scenarios": failures}},
                           notes=[f"{len(risks)} risks ({sum(r.impact == 'high' for r in risks)} high impact)"])

    def run_llm(self, task: Task, ctx) -> AgentResult:
        base = self.run_offline(task, ctx)
        data = self.ask(ctx, task, "Review and extend this risk register for the design. Be specific.",
                        '{"risks": [{"title": "...", "category": "...", "likelihood": "low|medium|high", '
                        '"impact": "low|medium|high", "mitigation": "..."}], "tradeoffs": [{"choice": "...", '
                        '"gain": "...", "cost": "..."}], "failure_scenarios": [{"scenario": "...", "behaviour": "..."}]}',
                        Spec=self.spec(ctx).to_dict(), Architecture=ctx.blackboard.get("architecture"),
                        Change_design=ctx.blackboard.get("change_design"), Draft=base.outputs["risks"])
        return AgentResult(outputs={"risks": data})
