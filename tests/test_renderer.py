from testgen import generator, renderer
from testgen.models import Session, TestCase


def make(language="python"):
    s = Session(requirements="R1. something", language=language)
    # deliberately added out of order
    for cat, title in (("edge", "e-one"), ("positive", "p-one"), ("negative", "n-one"),
                       ("positive", "p-two"), ("edge", "e-two")):
        body = "assert True" if language == "python" else "assert.ok(true);"
        s.tests.append(TestCase(cat, title, body, explanation=f"why {title}", requirement="R1").normalise())
    return s


def test_order_is_positive_negative_edge_in_both_languages():
    for lang in ("python", "javascript"):
        s = make(lang)
        src, names = renderer.render(s)
        titles = [t.title for t in s.ordered_tests()]
        assert titles == ["p-one", "p-two", "n-one", "e-one", "e-two"]
        positions = [src.index(t) for t in titles]
        assert positions == sorted(positions)
        assert list(s.labels().values()) != []
        assert [s.labels()[t.uid] for t in s.ordered_tests()] == ["P1", "P2", "N1", "E1", "E2"]


def test_every_test_has_explanation_comment():
    src, _ = renderer.render_python(make())
    assert src.count("# Explanation:") == 5
    assert "# Explanation: why p-one" in src
    jsrc, _ = renderer.render_javascript(make("javascript"))
    assert jsrc.count("// Explanation:") == 5


def test_long_and_multiline_explanations_cannot_break_out_of_comments():
    s = make()
    s.tests[0].explanation = "line one\nimport os; os.system('x')\n" + "word " * 60
    src, _ = renderer.render_python(s)
    compile(src, "t", "exec")  # still valid python, injected text stays inside the comment
    assert "\nimport os" not in src


def test_pasted_full_function_is_unwrapped():
    s = Session(requirements="R1. x", language="python")
    s.tests.append(TestCase("positive", "t", "def test_x():\n    assert 1 == 1\n    assert 2 == 2").normalise())
    src, _ = renderer.render_python(s)
    compile(src, "t", "exec")
    assert src.count("def test_") == 1


def test_validation_flags_only_the_broken_tests():
    s = make()
    bad = TestCase("negative", "broken", "assert (").normalise()
    s.tests.append(bad)
    assert list(renderer.validate(s)) == [bad.uid]
    js = make("javascript")
    jbad = TestCase("negative", "broken", "const = ;").normalise()
    js.tests.append(jbad)
    assert list(renderer.validate(js)) == [jbad.uid]
    # await is legal in JS test bodies (they run inside an async function)
    js.tests.append(TestCase("edge", "async", "await Promise.resolve(1);").normalise())
    assert list(renderer.validate(js)) == [jbad.uid]


def test_skip_excludes_tests():
    s = make()
    src, names = renderer.render_python(s, skip={s.tests[0].uid})
    assert "e-one" not in src.replace("e_one", "") and len(names) == 4


def test_title_with_quotes_is_safe_in_js(tmp_path):
    s = Session(requirements="R1. x", language="javascript")
    s.tests.append(TestCase("positive", 'say "hi" and \'bye\'\\', "assert.ok(true);").normalise())
    src, _ = renderer.render_javascript(s)
    import subprocess
    f = tmp_path / "t.js"
    f.write_text(src)
    r = subprocess.run(["node", "--check", str(f)], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def test_testcase_validation_errors():
    import pytest
    with pytest.raises(ValueError):
        TestCase("bogus", "x", "pass").normalise()
    with pytest.raises(ValueError):
        TestCase("edge", "x", "   ").normalise()
    assert TestCase("Edge Case", "x", "pass").normalise().category == "edge"
