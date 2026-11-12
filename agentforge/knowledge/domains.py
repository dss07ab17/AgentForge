"""Rule-based requirement analysis used in offline mode.

Detects requirement type and domain, finds vague language and missing
decisions, and turns each into an ``Ambiguity`` with a question, options, a
safe default and a severity. Severity HIGH means "a wrong guess would change
what we build", so the run stops at the clarification gate instead of guessing.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..models import Ambiguity, RequirementType, Severity

VAGUE_TERMS = {
    "fast": "performance target is unspecified",
    "quick": "performance target is unspecified",
    "scalable": "scale target is unspecified",
    "some": "scope is unspecified",
    "stuff": "scope is unspecified",
    "thing": "the system itself is not clearly named",
    "better": "success criteria are unspecified",
    "maybe": "scope is undecided",
    "etc": "list is open-ended",
    "nice": "success criteria are unspecified",
    "users": "user/account model is unspecified",
}

_DOMAIN_PATTERNS = {
    "url_shortener": re.compile(r"url[ -]?shorten|shorten(er|ing)? (url|link)|short(ened)? links?|link[s]? (thing|service|shortener)|\blinks?\b.*\b(redirect|short)|linkshort|\blinks? thing", re.I),
}


def detect_type(text: str, has_repo: bool) -> RequirementType:
    lowered = text.lower()
    if has_repo:
        if re.search(r"\b(add|improve)\b.*\b(tests?|test coverage|docs?|documentation)\b", lowered) and \
                not re.search(r"\b(feature|support|endpoint|fix|bug)\b", lowered):
            return RequirementType.TESTS_DOCS
        return RequirementType.BROWNFIELD
    return RequirementType.GREENFIELD


def detect_domain(text: str) -> str:
    for domain, pattern in _DOMAIN_PATTERNS.items():
        if pattern.search(text):
            return domain
    return "generic"


def vague_terms(text: str) -> list[tuple[str, str]]:
    words = set(re.findall(r"[a-z]+", text.lower()))
    return [(w, why) for w, why in VAGUE_TERMS.items() if w in words]


# ------------------------------------------------------------------ url shortener
def _yes(answer: str) -> bool:
    return answer.strip().lower().startswith(("yes", "y", "true", "include", "on"))


# ambiguity id -> function(answer) -> option updates
OPTION_APPLIERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "scale": lambda a: {"scale_target": a},
    "analytics": lambda a: {"analytics": "basic" if a.lower().startswith("basic") else "detailed"},
    "custom_alias": lambda a: {"custom_alias": _yes(a)},
    "expiry": lambda a: {"expiry": _yes(a)},
    "rate_limit": lambda a: {"rate_limit": _yes(a)},
    "performance": lambda a: {"latency_target": a},
    "users": lambda a: {"user_accounts": "in scope" if a.lower().startswith("in scope") else "deferred"},
    "system": lambda a: {"confirmed_domain": a},
}


def url_shortener_ambiguities(text: str) -> list[Ambiguity]:
    lowered = text.lower()
    vague = {w for w, _ in vague_terms(text)}
    mentions_analytics = bool(re.search(r"analytic|stats|statistic|metric|track|click", lowered))
    out: list[Ambiguity] = []
    if "thing" in vague or not re.search(r"shorten", lowered):
        out.append(Ambiguity(
            id="system", severity=Severity.HIGH,
            question="Is the system a URL shortener (create short codes that redirect to long URLs)?",
            why_it_matters="Determines the whole architecture; 'links thing' could also mean a bookmark manager.",
            options=["yes, a URL shortener with redirects", "no, something else (describe)"],
            default="yes, a URL shortener with redirects"))
    out.append(Ambiguity(
        id="scale", severity=Severity.MEDIUM if "scalable" in vague or "fast" in vague else Severity.LOW,
        question="What load should the design target?",
        why_it_matters="Drives caching, storage choice, code-space size and the analytics pipeline.",
        options=["1k creates/s, 50k redirects/s at peak (design target); 100M links/year",
                 "internal tool: <100 requests/s",
                 "internet scale: 1M+ redirects/s, multi-region"],
        default="1k creates/s, 50k redirects/s at peak (design target); 100M links/year"))
    if "fast" in vague or "quick" in vague:
        out.append(Ambiguity(
            id="performance", severity=Severity.MEDIUM,
            question="What does 'fast' mean for redirects?",
            why_it_matters="Sets the latency budget for the hot path and whether a cache is mandatory.",
            options=["p99 redirect latency < 50 ms (server side) with cache", "p99 < 200 ms, no cache needed"],
            default="p99 redirect latency < 50 ms (server side) with cache"))
    analytics_vague = mentions_analytics and ("some" in vague or not re.search(r"referr|per[- ]day|daily|geo", lowered))
    out.append(Ambiguity(
        id="analytics",
        severity=Severity.HIGH if ("some" in vague and mentions_analytics) else Severity.MEDIUM,
        question="Which analytics are required?" if mentions_analytics else "Are click analytics required?",
        why_it_matters="Determines the event model, storage growth and whether raw click events are kept.",
        options=["detailed: total clicks, clicks per day, top referrers", "basic: total clicks and last click time"],
        default="detailed: total clicks, clicks per day, top referrers") if analytics_vague or mentions_analytics
        else Ambiguity(id="analytics", severity=Severity.LOW, question="Are click analytics required?",
                       why_it_matters="Adds an event pipeline.", options=["detailed: total clicks, clicks per day, top referrers",
                                                                         "basic: total clicks and last click time"],
                       default="basic: total clicks and last click time"))
    if "users" in vague:
        out.append(Ambiguity(
            id="users", severity=Severity.HIGH,
            question="Are user accounts (sign-up, login, per-user link ownership) in scope for this iteration?",
            why_it_matters="Adds identity, authorization on every endpoint and a users table; roughly doubles scope.",
            options=["out of scope for v1 (anonymous API); design leaves room for ownership",
                     "in scope: accounts and per-user links"],
            default="out of scope for v1 (anonymous API); design leaves room for ownership"))
    out.append(Ambiguity(
        id="custom_alias", severity=Severity.LOW, question="Should clients be able to choose a custom alias?",
        why_it_matters="Adds alias validation, reserved words and a conflict (409) path.",
        options=["yes", "no"], default="yes"))
    out.append(Ambiguity(
        id="expiry", severity=Severity.LOW, question="Should links support an optional expiry?",
        why_it_matters="Adds a nullable expires_at column and a 410 Gone response.",
        options=["yes", "no"], default="yes"))
    out.append(Ambiguity(
        id="rate_limit", severity=Severity.MEDIUM if "scalable" in vague else Severity.LOW,
        question="Should link creation be rate limited to prevent abuse?",
        why_it_matters="Public shorteners are abused for spam/phishing; protects storage and reputation.",
        options=["yes", "no"], default="yes"))
    return out


def generic_ambiguities(text: str, req_type: RequirementType) -> list[Ambiguity]:
    out = []
    for word, why in vague_terms(text):
        out.append(Ambiguity(
            id=f"vague_{word}", severity=Severity.HIGH if word in {"thing", "stuff", "some", "maybe"} else Severity.MEDIUM,
            question=f"The requirement says '{word}': {why}. What exactly is expected?",
            why_it_matters="Unclear scope leads to building the wrong thing.",
            options=["(describe)"], default="(no default: needs a human answer)"))
    if req_type == RequirementType.BROWNFIELD and not re.search(r"\btests?\b", text.lower()):
        out.append(Ambiguity(
            id="compat", severity=Severity.LOW,
            question="Must existing API behaviour and stored data stay backward compatible?",
            why_it_matters="Decides between additive changes + migration and a breaking change.",
            options=["yes: additive, backward-compatible change with migration", "no: breaking change acceptable"],
            default="yes: additive, backward-compatible change with migration"))
    return out


def brownfield_expiry_ambiguities(text: str) -> list[Ambiguity]:
    lowered = text.lower()
    out = []
    if re.search(r"expir|ttl", lowered) and not re.search(r"\b4\d\d\b|gone|not found", lowered):
        out.append(Ambiguity(
            id="expired_status", severity=Severity.MEDIUM,
            question="What should clients get for an expired link?",
            why_it_matters="404 hides that the link existed; 410 Gone is more precise.",
            options=["410 Gone", "404 Not Found"], default="410 Gone"))
    if re.search(r"expir|ttl", lowered):
        out.append(Ambiguity(
            id="ttl_bounds", severity=Severity.LOW,
            question="What TTL range is valid?",
            why_it_matters="Prevents nonsense values (0, negative, centuries).",
            options=["1 second to 10 years, integer seconds", "1 minute to 1 year"],
            default="1 second to 10 years, integer seconds"))
    return out
