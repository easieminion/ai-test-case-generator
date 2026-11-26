import pytest

from app import create_app
from testgen.samples import SAMPLES

REQ = SAMPLES["password_validator"]["requirements"]
H = {"Host": "localhost"}


@pytest.fixture
def c(tmp_path):
    return create_app(tmp_path).test_client()


def create(c, language="python", **extra):
    r = c.post("/api/sessions", json={"requirements": REQ, "language": language, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["session"]


def test_full_workflow_matches_the_brief(c):
    s = create(c)
    sid = s["id"]
    # 4. sequence of tests: positive, negative, edge
    cats = [t["category"] for t in s["tests"]]
    assert cats == sorted(cats, key=["positive", "negative", "edge"].index)
    # 2. user adds a missing test
    r = c.post(f"/api/sessions/{sid}/tests", json={"category": "negative", "title": "Tab",
               "explanation": "Tab is whitespace", "code": "assert solution.validate_password('Abcd\\t1234!')['valid'] is False"})
    assert r.status_code == 201
    # run
    run = c.post(f"/api/sessions/{sid}/run").get_json()["session"]
    assert len(run["runs"][-1]["tests"]) == 16 and run["tests_changed_since_run"] is False
    # 1. requirements updated -> regenerated, manual test kept, run is now stale, and code still exists
    reduced = "\n".join(l for l in REQ.splitlines() if not l.startswith("R6"))
    r = c.put(f"/api/sessions/{sid}/requirements", json={"requirements": reduced})
    after = r.get_json()["session"]
    assert any(t["origin"] == "user" for t in after["tests"]) and after["tests_changed_since_run"] is True
    assert after["source_code"].strip(), "AI-written code must be regenerated, not wiped"
    r2 = c.post(f"/api/sessions/{sid}/run")        # regression: used to fail with "no code under test"
    assert r2.status_code == 200
    assert c.get(f"/api/sessions/{sid}").get_json()["session"]["tests_changed_since_run"] is False
    # 5. report: default PDF
    rep = c.get(f"/api/sessions/{sid}/report")
    assert rep.mimetype == "application/pdf" and rep.data.startswith(b"%PDF")
    assert c.post(f"/api/sessions/{sid}/report", json={"format": "docx"}).status_code == 200


def test_user_code_is_kept_across_regeneration(c):
    code = "def validate_password(p):\n    return {'valid': True, 'errors': []}\n"
    s = create(c, source_code=code)
    assert s["source_origin"] == "user"
    after = c.put(f"/api/sessions/{s['id']}/requirements", json={"requirements": REQ}).get_json()["session"]
    assert after["source_code"] == code.strip()


def test_language_switch_regenerates_in_new_language(c):
    s = create(c, "python")
    r = c.put(f"/api/sessions/{s['id']}/requirements", json={"requirements": REQ, "language": "javascript"})
    after = r.get_json()["session"]
    assert after["language"] == "javascript" and "module.exports" in after["source_code"]
    assert c.post(f"/api/sessions/{s['id']}/run").status_code == 200


def test_edit_and_delete(c):
    s = create(c)
    uid = s["tests"][0]["uid"]
    e = c.put(f"/api/sessions/{s['id']}/tests/{uid}", json={"title": "Renamed", "code": "assert True"}).get_json()["session"]
    t = next(t for t in e["tests"] if t["uid"] == uid)
    assert t["title"] == "Renamed" and t["origin"] == "user"          # edited AI tests become manual
    d = c.delete(f"/api/sessions/{s['id']}/tests/{uid}").get_json()["session"]
    assert uid not in [t["uid"] for t in d["tests"]]


def test_security_guards(c):
    assert c.get("/api/config", headers={"Host": "evil.example"}).status_code == 400
    assert c.post("/api/sessions", data="x=1", content_type="text/plain").status_code == 415
    assert c.post("/api/sessions", data="x=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert c.get("/api/sessions/s_zzzzzzzz").status_code == 404
    assert c.get("/api/sessions/..%2f..%2fetc%2fpasswd").status_code == 404


def test_friendly_errors(c):
    assert c.post("/api/sessions", json={"requirements": "x", "language": "python"}).status_code == 400
    r = c.post("/api/sessions", json={"requirements": "A function that adds numbers.", "language": "python"})
    assert r.status_code == 502 and "demo" in r.get_json()["error"].lower()
    s = create(c)
    assert c.get(f"/api/sessions/{s['id']}/report").status_code == 400        # nothing run yet
    assert c.post(f"/api/sessions/{s['id']}/tests", json={"category": "edge", "title": "x", "code": ""}).status_code == 400


def test_index_page_served(c):
    r = c.get("/", headers=H)
    assert r.status_code == 200 and b"AI Test Case Generator" in r.data
