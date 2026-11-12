"""Requirement Analyst: intent, type, domain, ambiguities -> normalized Spec."""
from __future__ import annotations

from ..knowledge import domains
from ..models import AgentResult, Ambiguity, RequirementType, Severity, Spec, Task
from .base import Agent

_SHAPE = """{
  "req_type": "greenfield|brownfield|tests_docs",
  "domain": "short snake_case domain name, e.g. url_shortener",
  "title": "short title", "intent": "one sentence: what problem is being solved and for whom",
  "functional": ["..."], "non_functional": ["..."], "out_of_scope": ["..."],
  "acceptance_criteria": ["testable statements"],
  "ambiguities": [{"id": "snake_case", "question": "...", "why_it_matters": "...",
                   "options": ["..."], "default": "one of options", "severity": "low|medium|high"}]
}"""


class RequirementAnalyst(Agent):
    name = "analyst"
    role = "Requirement Analyst"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        raw: str = task.params["requirement"]
        has_repo = bool(task.params.get("has_repo"))
        req_type = domains.detect_type(raw, has_repo)
        domain = domains.detect_domain(raw) if req_type == RequirementType.GREENFIELD else "existing_codebase"
        vague = domains.vague_terms(raw)

        if domain == "url_shortener":
            spec = self._url_shortener_spec(raw, req_type)
        else:
            ambiguities = domains.generic_ambiguities(raw, req_type)
            if req_type == RequirementType.BROWNFIELD:
                ambiguities += domains.brownfield_expiry_ambiguities(raw)
            spec = Spec(
                raw=raw, req_type=req_type, domain=domain, title=_title(raw), intent=raw.strip(),
                functional=_split_clauses(raw), non_functional=[
                    "No regression in existing behaviour (existing tests must keep passing)",
                    "Changes are minimal, localized and follow existing code conventions",
                ] if req_type != RequirementType.GREENFIELD else [],
                out_of_scope=["Unrelated refactoring"], ambiguities=ambiguities,
                acceptance_criteria=[f"{c} is implemented and covered by an automated test" for c in _split_clauses(raw)],
            )
        notes = [f"type={req_type.value}", f"domain={spec.domain}", f"vague terms={[w for w, _ in vague] or 'none'}",
                 f"ambiguities={len(spec.ambiguities)} ({sum(a.severity == Severity.HIGH for a in spec.ambiguities)} blocking)"]
        return AgentResult(outputs={"spec": spec.to_dict()}, notes=notes)

    def _url_shortener_spec(self, raw: str, req_type: RequirementType) -> Spec:
        return Spec(
            raw=raw, req_type=req_type, domain="url_shortener", title="URL shortener service",
            intent="Let clients turn long URLs into short codes that redirect, persist them durably, "
                   "and report how links are used.",
            functional=[
                "Create a short link for a valid http(s) URL via REST API",
                "Redirect GET /{code} to the target URL",
                "Fetch and delete link metadata via REST API",
                "Record clicks and expose per-link analytics",
                "Persist links and analytics durably across restarts",
            ],
            non_functional=[
                "Redirect hot path does no synchronous writes (analytics are asynchronous)",
                "Short codes are non-sequential and not enumerable",
                "Horizontally scalable stateless API tier; storage behind a repository interface",
                "Consistent JSON error model; no internal details leaked on errors",
                "Input validation on every external field (URL scheme, length, body size)",
            ],
            out_of_scope=["User accounts / authentication (documented as a follow-up)", "Web UI",
                          "Link previews / malware scanning (documented as a risk)"],
            ambiguities=domains.url_shortener_ambiguities(raw),
            acceptance_criteria=[
                "POST /api/v1/links returns 201 with code and short_url for a valid URL, 400 for an invalid one",
                "GET /{code} returns 302 with Location for a known code and 404 for an unknown one",
                "Creating many links concurrently yields unique codes and never overwrites an existing link",
                "Click counts reported by the stats endpoint equal the number of redirects served",
                "Every route in the implementation is documented in the OpenAPI contract and vice versa",
            ],
        )

    def run_llm(self, task: Task, ctx) -> AgentResult:
        raw = task.params["requirement"]
        data = self.ask(ctx, task, "Analyse this software requirement. Identify every ambiguity a senior engineer "
                        "would raise; mark as 'high' only those where a wrong guess changes what gets built.",
                        _SHAPE, Requirement=raw, Has_existing_repository=bool(task.params.get("has_repo")))
        spec = Spec(
            raw=raw, req_type=RequirementType(data["req_type"]), domain=data["domain"], title=data["title"],
            intent=data["intent"], functional=data["functional"], non_functional=data["non_functional"],
            out_of_scope=data.get("out_of_scope", []), acceptance_criteria=data.get("acceptance_criteria", []),
            ambiguities=[Ambiguity.from_dict(a) for a in data.get("ambiguities", [])],
        )
        if not spec.functional or not spec.acceptance_criteria:
            raise ValueError("spec must contain functional requirements and acceptance criteria")
        return AgentResult(outputs={"spec": spec.to_dict()}, notes=[f"type={spec.req_type.value}", f"domain={spec.domain}"])


def _split_clauses(raw: str) -> list[str]:
    import re
    parts = re.split(r"(?<=[.;])\s+|\balso\b|\band then\b", raw, flags=re.I)
    return [p.strip(" .;,").capitalize() for p in parts if len(p.strip()) > 8]


def _title(raw: str) -> str:
    words = raw.strip().split()
    return " ".join(words[:8]) + ("..." if len(words) > 8 else "")
