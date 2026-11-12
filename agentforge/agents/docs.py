"""Technical Writer: README / architecture docs (greenfield) or README + changelog (brownfield)."""
from __future__ import annotations

from ..knowledge.brownfield import linkshort_recipe
from ..knowledge.url_shortener import blueprint
from ..models import AgentResult, RequirementType, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent
from .coder import repo_snapshot


class DocsWriter(Agent):
    name = "docs"
    role = "Technical Writer"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        if spec.req_type != RequirementType.GREENFIELD:
            if not linkshort_recipe.matches(repo_snapshot(ctx)):
                raise NonRetriableError("offline mode has no docs recipe for this repository")
            return AgentResult(edits=linkshort_recipe.DOC_EDITS, notes=["README API section + changelog updated"])
        if spec.domain != "url_shortener":
            raise NonRetriableError(f"no offline docs blueprint for domain '{spec.domain}'")
        files = blueprint.docs(spec.options, ctx.blackboard.require("api_contract"),
                               ctx.blackboard.require("architecture"))
        return AgentResult(files=files, notes=[", ".join(sorted(files))])

    def run_llm(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        data = self.ask(ctx, task, "Write concise, accurate developer documentation (README with run/use/test "
                        "sections; architecture doc with a mermaid diagram).",
                        '{"files": {"docs/README.md": "...", "docs/ARCHITECTURE.md": "..."}}',
                        Spec=spec.to_dict(), Architecture=ctx.blackboard.get("architecture"),
                        API_contract=ctx.blackboard.get("api_contract"), Change_design=ctx.blackboard.get("change_design"))
        return AgentResult(files=data["files"])
