"""Requirement analyzer: rule-based checks (offline), AI path (local API stub), Markdown, API and CLI."""
import pytest

import cli
from app import create_app
from testgen import analyzer
from testgen.analyzer import analyze, rule_based_analysis, split_requirements, to_markdown
from testgen.llm import AnthropicProvider, LLMError, OfflineDemoProvider
from testgen.samples import SAMPLES
from tests.test_llm import Stub, tool_reply

SAMPLE = SAMPLES["password_validator"]["requirements"]
# the sample has a deliberately vague R7 for the demo; without it every requirement is clear
SAMPLE_CLEAN = "\n".join(l for l in SAMPLE.splitlines() if not l.startswith("R7"))

VAGUE = """Profile headline
R1. The profile page should load fast.
R2. The headline must be user-friendly and support emojis, etc.
R3. Headline must be between 10 and 120 characters.
R4. Headline must be at most 100 characters.
R5. Users can upload a profile photo.
R6. Headline field is optional.
R7. Headline field is required for recruiters.
R8. The headline must contain emojis.
R9. The headline must not contain emojis. TBD
"""


def by_id(a, rid):
    return next(r for r in a.requirements if r.id == rid)


def types(report):
    return {i.type for i in report.issues}


# ---------------------------------------------------------------- parsing
def test_split_numbered_bullets_continuations_and_plain_text():
    pre, reqs = split_requirements("Intro line\nR1. First\n   continues here\nREQ-2: Second\n3) Third")
    assert pre == "Intro line"
    assert reqs == [("R1", "First continues here"), ("REQ-2", "Second"), ("R3", "Third")]
    _, bullets = split_requirements("- one thing\n* another thing")
    assert [r for r, _ in bullets] == ["R1", "R2"]
    _, sentences = split_requirements("The app must log in users. Passwords must be hashed.")
    assert [t for _, t in sentences] == ["The app must log in users.", "Passwords must be hashed."]


# ---------------------------------------------------------------- rules
def test_bundled_sample_flags_only_the_planted_vague_requirement():
    a = rule_based_analysis(SAMPLE)
    sm = a.summary()
    assert sm["requirements"] == 7 and sm["clear"] == 6 and sm["contradictions"] == 0
    r7 = by_id(a, "R7")
    assert r7.status == "needs_clarification" and {"vague", "ambiguous"} <= types(r7)
    assert any(i.severity == "high" and "user-friendly" in i.problem for i in r7.issues)
    assert a.engine == "rules"
    clean = rule_based_analysis(SAMPLE_CLEAN).summary()
    assert clean["requirements"] == 6 and clean["issues"] == 0 and clean["clear"] == 6


def test_vague_words_weak_modals_and_open_lists():
    a = rule_based_analysis(VAGUE)
    r1 = by_id(a, "R1")
    assert {"vague", "ambiguous"} <= types(r1)
    assert any(i.severity == "high" and "fast" in i.problem for i in r1.issues)
    assert r1.status == "needs_clarification"
    assert r1.rewrite.startswith("The profile page must load fast (define:")
    r2 = by_id(a, "R2")
    assert any("user-friendly" in i.problem for i in r2.issues)
    assert any("etc" in i.problem and "list open" in i.problem for i in r2.issues)


def test_numbers_make_quantity_words_measurable():
    a = rule_based_analysis("R1. Search must return results fast, within 200 ms at p95.")
    assert "vague" not in types(by_id(a, "R1"))


def test_missing_limit_open_range_tbd_and_absolute():
    a = rule_based_analysis(VAGUE + "R10. The system must never lose data.\n")
    assert "missing_limit" in types(by_id(a, "R5"))                       # upload with no size
    assert "edge_case" in types(by_id(a, "R3"))                           # inclusive?
    assert any(i.type == "incomplete" and i.severity == "high" for i in by_id(a, "R9").issues)
    assert "untestable" in types(by_id(a, "R10"))
    # headline has limits in R3/R4, so R8 is not asked about headline length again
    assert "missing_limit" not in types(by_id(a, "R8"))


def test_contradictions_between_requirements():
    a = rule_based_analysis(VAGUE)
    problems = [(tuple(c.requirements), c.severity, c.problem) for c in a.cross_issues]
    assert any(ids == ("R3", "R4") and "120 vs 100" in p for ids, _, p in problems)
    assert any(ids == ("R6", "R7") and sev == "high" for ids, sev, _ in problems)
    assert any(ids == ("R8", "R9") and "must not contain emojis" in p for ids, _, p in problems)
    assert a.summary()["contradictions"] == 3
    assert all(c.suggestion.endswith("?") for c in a.cross_issues)


def test_impossible_range_inside_one_requirement_and_unitless_limit():
    a = rule_based_analysis("R1. Users must enter at least 12 characters and at most 8 characters.")
    c = [i for i in by_id(a, "R1").issues if i.type == "contradiction"]
    assert len(c) == 1 and c[0].severity == "high"
    b = rule_based_analysis("- A member may send at most 100 connection requests per week.\n"
                            "- Connection requests are limited to 200 per week.")
    assert any("100 vs 200" in i.problem for i in b.cross_issues)


def test_unrelated_limits_are_not_contradictions():
    a = rule_based_analysis("R1. Username must be at most 20 characters.\nR2. Bio must be at most 300 characters.")
    assert a.cross_issues == []


def test_general_questions_and_input_validation():
    a = rule_based_analysis("Users can reset their password by email. The app should be intuitive.")
    assert any("invalid input" in q for q in a.general_questions)
    assert any("automatically" in w for w in a.warnings)
    with pytest.raises(ValueError):
        rule_based_analysis("   ")
    with pytest.raises(ValueError):
        rule_based_analysis("x" * (analyzer.MAX_CHARS + 1))


# ---------------------------------------------------------------- markdown
def test_markdown_puts_questions_first_and_numbers_them():
    md = to_markdown(rule_based_analysis(VAGUE))
    assert md.startswith("## Requirement clarification questions")
    assert md.index("### Conflicts between requirements") < md.index("### Questions by requirement") < md.index("### General")
    assert "1. **R3, R4**" in md and "<details>" in md
    clean = to_markdown(rule_based_analysis(SAMPLE_CLEAN))
    assert "look specific and testable" in clean


# ---------------------------------------------------------------- AI path
AI_PAYLOAD = {
    "requirements": [
        {"id": "R1", "text": "The page should load fast.",
         "issues": [{"type": "vague", "severity": "HIGH", "excerpt": "fast", "problem": "No target.",
                     "suggestion": "Give a number."},
                    {"type": "weird", "severity": "??", "problem": "Normalised."},
                    {"type": "vague", "severity": "low", "problem": ""}],
         "questions": ["What load time, in ms?", " "], "rewrite": "The page must load within [N] ms."},
        {"id": "R2", "text": "Clear one.", "issues": [], "questions": []},
    ],
    "contradictions": [{"requirements": ["R1", "R2"], "problem": "Clash.", "question": "Which wins?"}],
    "general_questions": ["Which browsers?"],
}


@pytest.fixture
def stub():
    made = []
    yield lambda responses: made.append(Stub(responses)) or made[-1]
    for s in made:
        s.close()


def test_ai_analysis_uses_forced_tool_and_parses(stub):
    s = stub([tool_reply(analyzer.ANALYZE_TOOL, AI_PAYLOAD)])
    a = analyze("R1. The page should load fast.\nR2. Clear one.", AnthropicProvider("k", model="m-2", base_url=s.url))
    body = s.requests[0]["body"]
    assert body["tool_choice"] == {"type": "tool", "name": analyzer.ANALYZE_TOOL}
    assert "keyword scanner flagged" in body["messages"][0]["content"]         # rule hints are passed along
    assert a.engine == "ai" and a.model == "m-2"
    r1 = a.requirements[0]
    assert [(i.type, i.severity) for i in r1.issues] == [("vague", "high"), ("ambiguous", "medium")]
    assert r1.questions == ["What load time, in ms?"] and r1.rewrite
    assert a.requirements[1].status == "clear"
    assert a.cross_issues[0].requirements == ["R1", "R2"] and a.cross_issues[0].suggestion == "Which wins?"
    assert "Which browsers?" in to_markdown(a)


def test_ai_analysis_empty_payload_is_an_error(stub):
    s = stub([tool_reply(analyzer.ANALYZE_TOOL, {"requirements": []})])
    with pytest.raises(LLMError):
        analyze("R1. The page should load fast.", AnthropicProvider("k", base_url=s.url))


def test_demo_provider_uses_rules():
    assert analyze(VAGUE, OfflineDemoProvider()).engine == "rules"


# ---------------------------------------------------------------- API + CLI
def test_api_analyze(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = create_app(tmp_path).test_client()
    r = c.post("/api/analyze", json={"requirements": VAGUE})
    assert r.status_code == 200
    d = r.get_json()
    assert d["analysis"]["engine"] == "rules" and d["analysis"]["summary"]["contradictions"] == 3
    assert d["analysis"]["requirements"][0]["status"] == "needs_clarification"
    assert d["markdown"].startswith("## Requirement clarification questions")
    assert c.post("/api/analyze", json={"requirements": ""}).status_code == 400
    assert c.post("/api/analyze", data="x", content_type="text/plain").status_code == 415


def test_cli_analyze(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = tmp_path / "reqs.md"
    f.write_text(VAGUE, encoding="utf-8")
    assert cli.main(["-r", str(f), "--analyze"]) == 1                     # high-severity issues found
    out = capsys.readouterr().out
    assert out.startswith("## Requirement clarification questions")    # stdout is pure Markdown
    assert cli.main(["--sample", "--analyze"]) == 1                     # the planted vague R7
    assert "**R7**" in capsys.readouterr().out
