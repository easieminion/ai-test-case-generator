"""The Anthropic client is tested against a local stub that speaks the Messages API."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from testgen import llm
from testgen.llm import AnthropicProvider, LLMError, OfflineDemoProvider, get_provider
from testgen.samples import SAMPLES


class Stub:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload = stub.responses.pop(0) if len(stub.responses) > 1 else stub.responses[0]
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def tool_reply(name, payload):
    return 200, {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": name, "input": payload}]}


SUITE = {
    "implementation": "def add(a, b):\n    return a + b\n",
    "tests": [
        {"category": "positive", "title": "adds", "explanation": "sum", "requirement": "R1",
         "code": "assert solution.add(1, 2) == 3"},
        {"category": "Edge Case", "title": "zero", "explanation": "zero", "requirement": "R1",
         "code": "assert solution.add(0, 0) == 0"},
        {"category": "nonsense", "title": "dropped", "explanation": "", "requirement": "", "code": "pass"},
        {"category": "negative", "title": "empty body", "explanation": "", "requirement": "", "code": "  "},
    ],
}


@pytest.fixture
def stub():
    made = []

    def make(responses):
        s = Stub(responses)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def test_generate_suite_sends_forced_tool_call_and_parses(stub):
    s = stub([tool_reply("submit_test_suite", SUITE)])
    p = AnthropicProvider("sk-test", model="m-1", base_url=s.url)
    draft = p.generate_suite("R1. add two numbers", "python", "", 3)
    req = s.requests[0]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == "sk-test"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert req["body"]["model"] == "m-1"
    assert req["body"]["tool_choice"] == {"type": "tool", "name": "submit_test_suite"}
    assert "R1. add two numbers" in req["body"]["messages"][0]["content"]
    assert "write a correct, clean reference" in req["body"]["messages"][0]["content"]
    # 2 valid tests kept, the invalid category and empty body were skipped with warnings
    assert [t.category for t in draft.tests] == ["positive", "edge"]
    assert len(draft.warnings) == 2
    assert draft.implementation.startswith("def add")


def test_user_supplied_code_is_sent_and_implementation_ignored(stub):
    s = stub([tool_reply("submit_test_suite", SUITE)])
    p = AnthropicProvider("k", base_url=s.url)
    draft = p.generate_suite("R1", "python", "def add(a,b): return a+b", 3)
    assert "def add(a,b): return a+b" in s.requests[0]["body"]["messages"][0]["content"]
    assert draft.implementation == ""


def test_no_implementation_when_needed_is_an_error(stub):
    s = stub([tool_reply("submit_test_suite", {"tests": SUITE["tests"][:1]})])
    with pytest.raises(LLMError, match="did not write one"):
        AnthropicProvider("k", base_url=s.url).generate_suite("R1", "python", "", 3)


def test_api_error_message_is_surfaced(stub):
    s = stub([(400, {"error": {"type": "invalid_request_error", "message": "model: not found"}})])
    with pytest.raises(LLMError, match="400.*model: not found"):
        AnthropicProvider("k", base_url=s.url).generate_suite("R1", "python", "x", 3)


def test_retries_on_overload_then_succeeds(stub, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    s = stub([(529, {"error": {"message": "overloaded"}}), tool_reply("submit_test_suite", SUITE)])
    draft = AnthropicProvider("k", base_url=s.url).generate_suite("R1", "python", "", 3)
    assert len(s.requests) == 2 and draft.tests


def test_truncated_answer_gives_helpful_error(stub):
    s = stub([(200, {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "..."}]})])
    with pytest.raises(LLMError, match="cut off"):
        AnthropicProvider("k", base_url=s.url).generate_suite("R1", "python", "x", 3)


def test_draft_test_forces_category_and_marks_manual(stub):
    s = stub([tool_reply("submit_test", {"tests": [dict(SUITE["tests"][0], category="positive")]})])
    t = AnthropicProvider("k", base_url=s.url).draft_test("R1", "python", "code", "negative", "zero input")
    assert t.category == "negative" and t.origin == "user"
    assert "zero input" in s.requests[0]["body"]["messages"][0]["content"]


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("QA_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_provider(), OfflineDemoProvider)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert isinstance(get_provider(), AnthropicProvider)
    assert isinstance(get_provider("demo"), OfflineDemoProvider)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(LLMError):
        get_provider("anthropic")


def test_demo_provider_refuses_unknown_requirements_and_filters_removed_ones():
    p = OfflineDemoProvider()
    with pytest.raises(LLMError, match="bundled sample"):
        p.generate_suite("Some other feature that adds numbers", "python", "", 4)
    full = SAMPLES["password_validator"]["requirements"]
    reduced = "\n".join(l for l in full.splitlines() if not l.startswith("R6"))
    assert len(p.generate_suite(reduced, "python", "", 4).tests) < len(p.generate_suite(full, "python", "", 4).tests)


def test_bug_report_wording_uses_forced_tool_and_sends_the_failure(stub):
    reply = {"title": "64-char password rejected", "summary": "s", "steps": ["a"], "expected": "e",
             "actual": "a", "severity": "High", "severity_reason": "r"}
    s = stub([tool_reply("submit_bug_report", reply)])
    out = AnthropicProvider("k", base_url=s.url).write_bug_report(
        {"language": "python", "requirements": "R1. len 8-64", "code_under_test": "def f(): pass",
         "test": "def test_e3(): ...", "error_output": "AssertionError: ['too_long']",
         "category": "Edge case", "requirement": "R1"})
    body = s.requests[0]["body"]
    assert body["tool_choice"] == {"type": "tool", "name": "submit_bug_report"}
    assert "['too_long']" in body["messages"][0]["content"] and out["title"].startswith("64-char")
    with pytest.raises(LLMError):
        OfflineDemoProvider().write_bug_report({})
