from pathlib import Path

from agentforge.agents.codebase import analyze_repository, impact_analysis
from agentforge.agents.coder import contract_operations, routes_in
from agentforge.agents.tester import contract_coverage
from agentforge.knowledge import domains
from agentforge.knowledge.url_shortener import blueprint
from agentforge.models import RequirementType, Severity

SAMPLE = Path(__file__).parents[1] / "examples/brownfield/linkshort_v1"
BROWNFIELD_REQ = ("Add optional link expiration: clients can pass ttl_seconds when shortening and expired links must "
                  "return HTTP 410. Also fix the bug where a newly generated code can silently overwrite an existing link.")


def test_type_and_domain_detection():
    assert domains.detect_type("Build a URL shortener", has_repo=False) == RequirementType.GREENFIELD
    assert domains.detect_type("Add expiry", has_repo=True) == RequirementType.BROWNFIELD
    assert domains.detect_type("Improve test coverage and docs", has_repo=True) == RequirementType.TESTS_DOCS
    assert domains.detect_domain("Build a scalable URL shortener service") == "url_shortener"
    assert domains.detect_domain("We need a links thing that's fast") == "url_shortener"
    assert domains.detect_domain("Build a payroll system") == "generic"


def test_clear_requirement_has_no_blocking_ambiguities():
    ambs = domains.url_shortener_ambiguities("Build a scalable URL shortener service with APIs, persistence, and analytics.")
    assert not [a for a in ambs if a.severity == Severity.HIGH]
    assert {"scale", "analytics", "rate_limit"} <= {a.id for a in ambs}


def test_vague_requirement_has_blocking_ambiguities():
    ambs = domains.url_shortener_ambiguities("We need a links thing that's fast and has some stats. Maybe users too?")
    assert {a.id for a in ambs if a.severity == Severity.HIGH} == {"system", "analytics", "users"}


def test_impact_analysis_finds_layers_flows_and_hotspots():
    analysis = analyze_repository(SAMPLE)
    impact = impact_analysis(BROWNFIELD_REQ, analysis)
    paths = [m["path"] for m in impact["impacted_modules"]]
    assert {"app/store.py", "app/service.py", "app/web.py", "app/db.py"} <= set(paths)
    assert paths.index("app/store.py") < paths.index("app/service.py") < paths.index("app/web.py")
    assert impact["entry_points"] == ["app/web.py"]
    assert "app/web.py -> app/service.py -> app/store.py" in impact["data_flows"]
    assert any("INSERT OR REPLACE" in h["issue"] for h in impact["risk_hotspots"])
    assert impact["tests_affected"] == ["tests/test_service.py"]


def test_contract_extraction_and_coverage():
    opts = blueprint.normalize_options({})
    api = blueprint.render("api", opts)["shortener/api.py"]
    contract = blueprint.openapi(opts)
    assert routes_in(api) == contract_operations(contract)
    cov = contract_coverage(blueprint.render("integration_tests", opts), contract)
    assert cov["ratio"] == 1.0


def test_options_change_the_contract():
    lean = blueprint.openapi({"custom_alias": False, "expiry": False, "rate_limit": False, "analytics": "basic"})
    props = lean["components"]["schemas"]["CreateLinkRequest"]["properties"]
    assert set(props) == {"url"}
    assert "410" not in lean["paths"]["/{code}"]["get"]["responses"]
    assert "clicks_by_day" not in lean["components"]["schemas"]["LinkStats"]["properties"]
