from .analyst import RequirementAnalyst
from .architect import Architect
from .base import Agent
from .clarifier import Clarifier
from .codebase import CodebaseAnalyst
from .coder import Coder
from .docs import DocsWriter
from .gate import GateAgent
from .planner import Planner
from .repairer import Repairer
from .risk import RiskAnalyst
from .tester import Tester
from .validator import Validator


def registry() -> dict[str, Agent]:
    agents = [RequirementAnalyst(), CodebaseAnalyst(), Clarifier(), Planner(), Architect(), Coder(), Tester(),
              DocsWriter(), Validator(), Repairer(), RiskAnalyst(), GateAgent()]
    return {a.name: a for a in agents}


__all__ = ["Agent", "registry"]
