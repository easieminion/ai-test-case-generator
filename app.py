"""Web UI + JSON API.   Run:  python app.py   then open http://127.0.0.1:5000

Because this tool EXECUTES generated code, the server:
  * binds to 127.0.0.1 by default,
  * only accepts JSON bodies (blocks cross-site form posts),
  * rejects unexpected Host headers (blocks DNS-rebinding).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from testgen import bugreport, generator, renderer, runner
from testgen.llm import LLMError, get_provider
from testgen.models import CATEGORY_LABELS, LANGUAGES, Session
from testgen.reporting import DEFAULT_FORMAT, FORMATS, build_report, charts, normalise_format
from testgen.samples import SAMPLES
from testgen.store import SessionStore

WEB_DIR = Path(__file__).parent / "web"
MAX_RUN_HISTORY = 20


def create_app(data_dir: Path | None = None, allowed_hosts=("localhost", "127.0.0.1", "[::1]")) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    store = SessionStore(data_dir) if data_dir else SessionStore()

    # ------------------------------------------------------------ guards
    @app.before_request
    def _guard():
        raw = request.host or ""
        host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.split(":")[0]
        if allowed_hosts and host not in allowed_hosts:
            abort(400, description="Unexpected Host header")

    @app.errorhandler(ValueError)
    def _bad(e):
        return jsonify(error=str(e)), 400

    @app.errorhandler(KeyError)
    def _missing(e):
        return jsonify(error=str(e.args[0]) if e.args else "Not found"), 404

    @app.errorhandler(LLMError)
    def _llm(e):
        return jsonify(error=str(e)), 502

    @app.errorhandler(runner.RunnerError)
    def _runner(e):
        return jsonify(error=str(e)), 422

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(415)
    def _http(e):
        return jsonify(error=getattr(e, "description", str(e))), e.code

    # ------------------------------------------------------------ helpers
    def provider():
        return get_provider()

    def body() -> dict:
        data = request.get_json()          # 415 unless Content-Type: application/json
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def present(s: Session) -> dict:
        d = s.to_dict()
        d["labels"] = s.labels()
        d["category_labels"] = CATEGORY_LABELS
        d["latest_run_summary"] = _summary(s.runs[-1]) if s.runs else None
        d["tests_changed_since_run"] = bool(s.runs) and s.runs[-1].signature != s.signature()
        d["runs"] = [_slim(r) for r in s.runs]
        return d

    def _slim(run) -> dict:
        from dataclasses import asdict
        r = asdict(run)
        for big in ("code_source", "test_source", "requirements"):
            r.pop(big, None)
        for slim, full in zip(r["tests"], run.tests):
            slim["bug_reportable"] = bugreport.is_reportable(full)
        r["summary"] = _summary(run)
        return r

    def _summary(run) -> dict:
        sm = runner.summarise(run)
        sm["slowest"] = sm["slowest"].label if sm["slowest"] else None
        return sm

    def load(sid: str) -> Session:
        return store.load(sid)

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/api/config")
    def config():
        return jsonify(provider=provider().describe(), languages=list(LANGUAGES),
                       samples=list(SAMPLES.values()), formats=list(FORMATS),
                       default_format=DEFAULT_FORMAT)

    # ------------------------------------------------------------ sessions
    @app.get("/api/sessions")
    def list_sessions():
        return jsonify(store.list())

    @app.post("/api/sessions")
    def create_session():
        b = body()
        s = generator.create_session(b.get("requirements", ""), b.get("language", "python"),
                                     b.get("source_code", ""), b.get("tests_per_category", 4))
        warnings = generator.generate(s, provider(), keep_user_tests=False)
        store.save(s)
        return jsonify(session=present(s), warnings=warnings), 201

    @app.get("/api/sessions/<sid>")
    def get_session(sid):
        return jsonify(session=present(load(sid)))

    @app.put("/api/sessions/<sid>/requirements")
    def put_requirements(sid):
        b = body()
        s = load(sid)
        regenerate = bool(b.get("regenerate", True))
        if "language" in b:
            if b["language"] not in LANGUAGES:
                raise ValueError("Unknown language")
            if b["language"] != s.language:
                if s.source_origin == "user":
                    raise ValueError("You supplied your own code under test, so the language cannot "
                                     "change. Start a new session or clear the code first.")
                s.language = b["language"]
                s.tests = [t for t in s.tests if t.origin != "user"]  # user tests are language specific
                regenerate = True
        if "tests_per_category" in b:
            s.tests_per_category = max(1, min(int(b["tests_per_category"]), 10))
        warnings = generator.update_requirements(s, b.get("requirements", ""), provider(),
                                                 regenerate=regenerate,
                                                 keep_user_tests=bool(b.get("keep_user_tests", True)))
        store.save(s)
        return jsonify(session=present(s), warnings=warnings)

    @app.put("/api/sessions/<sid>/source")
    def put_source(sid):
        s = load(sid)
        generator.set_source_code(s, body().get("source_code", ""))
        store.save(s)
        return jsonify(session=present(s))

    # ------------------------------------------------------------ tests
    @app.post("/api/sessions/<sid>/tests")
    def add_test(sid):
        b, s = body(), load(sid)
        t = generator.add_test(s, b.get("category", ""), b.get("title", ""), b.get("code", ""),
                               b.get("explanation", ""), b.get("requirement", ""))
        store.save(s)
        return jsonify(test=t.__dict__, session=present(s)), 201

    @app.put("/api/sessions/<sid>/tests/<uid>")
    def edit_test(sid, uid):
        b, s = body(), load(sid)
        generator.update_test(s, uid, **b)
        store.save(s)
        return jsonify(session=present(s))

    @app.delete("/api/sessions/<sid>/tests/<uid>")
    def delete_test(sid, uid):
        s = load(sid)
        generator.delete_test(s, uid)
        store.save(s)
        return jsonify(session=present(s))

    @app.post("/api/sessions/<sid>/tests/draft")
    def draft(sid):
        b, s = body(), load(sid)
        t = generator.draft_test(s, provider(), b.get("category", "positive"), b.get("description", ""))
        return jsonify(test=t.__dict__)

    @app.get("/api/sessions/<sid>/testfile")
    def testfile(sid):
        s = load(sid)
        src, _ = renderer.render(s)
        return Response(src, mimetype="text/plain; charset=utf-8")

    # ------------------------------------------------------------ run + report
    @app.post("/api/sessions/<sid>/run")
    def run(sid):
        s = load(sid)
        result = runner.run_session(s, base_dir=store.runs_dir(sid))
        s.runs.append(result)
        s.runs = s.runs[-MAX_RUN_HISTORY:]
        s.touch()
        store.save(s)
        return jsonify(session=present(s), run_id=result.run_id)

    def _pick_run(s: Session):
        run_id = request.args.get("run")
        if not s.runs:
            raise ValueError("Run the tests first; there is nothing to report on yet.")
        if run_id:
            for r in s.runs:
                if r.run_id == run_id:
                    return r
            raise KeyError("Run not found")
        return s.runs[-1]

    @app.route("/api/sessions/<sid>/report", methods=["GET", "POST"])
    def report(sid):
        s = load(sid)
        fmt = normalise_format((request.get_json(silent=True) or {}).get("format") if request.method == "POST"
                               else request.args.get("format"))
        data, name, mime = build_report(_pick_run(s), fmt, s.id)
        (store.reports_dir() / name).write_bytes(data)
        return Response(data, mimetype=mime, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "X-Report-Name": name})

    @app.post("/api/sessions/<sid>/bug-report")
    def bug_report(sid):
        """Bug report for one failed test of a run (latest unless ?run=). Nothing is filed anywhere."""
        b, s = body(), load(sid)
        uid = str(b.get("uid", ""))
        if not uid:
            raise ValueError("Which test? Send its uid.")
        run = _pick_run(s)
        report = bugreport.build(s, run, uid, provider=provider(), use_ai=bool(b.get("use_ai", True)))
        return jsonify(bugreport.export(s, run, report, include_source=bool(b.get("include_source", True))))

    @app.get("/api/sessions/<sid>/charts/<name>.png")
    def chart(sid, name):
        s = load(sid)
        fn = {"categories": charts.category_chart, "coverage": charts.coverage_chart,
              "durations": charts.duration_chart}.get(name)
        if not fn:
            abort(404)
        png = fn(_pick_run(s))
        if png is None:
            abort(404, description="No data for this chart")
        return Response(png, mimetype="image/png", headers={"Cache-Control": "private, max-age=3600"})

    return app


def main():
    ap = argparse.ArgumentParser(description="AI Test Case Generator - web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()
    hosts = ("localhost", "127.0.0.1", "[::1]") if args.host in ("127.0.0.1", "localhost") else ()
    if not hosts:
        print("WARNING: binding to a non-local address exposes a code-execution endpoint. "
              "Only do this inside a container/VM you trust.")
    app = create_app(Path(args.data_dir) if args.data_dir else None, allowed_hosts=hosts)
    prov = get_provider().describe()
    print(f"LLM provider: {prov['name']}" + (f" ({prov['model']})" if prov["model"] else ""))
    print(f"Open http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
