"""Session workflow: requirements -> LLM -> tests, plus the edit operations.

Everything here is UI-agnostic; the web app and the CLI both call these functions.
"""
from __future__ import annotations

from typing import List, Optional

from .llm import LLMError, LLMProvider
from .models import LANGUAGES, Session, TestCase


def create_session(requirements: str, language: str, source_code: str = "",
                   per_category: int = 4) -> Session:
    requirements = (requirements or "").strip()
    if len(requirements) < 10:
        raise ValueError("Please describe the requirements (at least a sentence).")
    if language not in LANGUAGES:
        raise ValueError(f"Language must be one of {', '.join(LANGUAGES)}")
    per_category = max(1, min(int(per_category or 4), 10))
    return Session(requirements=requirements, language=language,
                   source_code=(source_code or "").strip("\n"),
                   source_origin="user" if (source_code or "").strip() else "ai",
                   tests_per_category=per_category)


def generate(session: Session, provider: LLMProvider, keep_user_tests: bool = True) -> List[str]:
    """(Re)generate the AI tests. Manually added tests survive unless told otherwise.

    Returns a list of warnings to show to the user.
    """
    # Only the reviewer's own code is "fixed". AI-written code is rewritten together with the
    # tests, otherwise a changed requirement would be tested against an outdated implementation.
    fixed_source = session.source_code if session.source_origin == "user" else ""
    draft = provider.generate_suite(session.requirements, session.language, fixed_source,
                                    session.tests_per_category)
    kept = [t for t in session.tests if t.origin == "user"] if keep_user_tests else []
    session.tests = draft.tests + kept
    if session.source_origin == "ai":
        session.source_code = draft.implementation.strip("\n")
    session.provider, session.model = provider.name, provider.model
    session.touch()
    return draft.warnings


def update_requirements(session: Session, new_text: str, provider: Optional[LLMProvider] = None,
                        regenerate: bool = True, keep_user_tests: bool = True) -> List[str]:
    new_text = (new_text or "").strip()
    if len(new_text) < 10:
        raise ValueError("Requirements cannot be empty.")
    session.requirements = new_text
    session.touch()
    if regenerate:
        if provider is None:
            raise LLMError("A provider is needed to regenerate tests.")
        return generate(session, provider, keep_user_tests=keep_user_tests)
    return []


def set_source_code(session: Session, code: str) -> None:
    """Replace the code under test with the reviewer's own code."""
    session.source_code = (code or "").strip("\n")
    session.source_origin = "user" if session.source_code.strip() else "ai"
    session.touch()


def add_test(session: Session, category: str, title: str, code: str, explanation: str = "",
             requirement: str = "") -> TestCase:
    test = TestCase(category=category, title=title, code=code, explanation=explanation,
                    requirement=requirement, origin="user").normalise()
    session.tests.append(test)
    session.touch()
    return test


def find_test(session: Session, uid: str) -> TestCase:
    for t in session.tests:
        if t.uid == uid:
            return t
    raise KeyError(f"No test with id {uid}")


def update_test(session: Session, uid: str, **fields) -> TestCase:
    test = find_test(session, uid)
    candidate = TestCase(**{**test.__dict__, **{k: v for k, v in fields.items()
                                                if k in ("category", "title", "code", "explanation", "requirement")}})
    candidate.normalise()
    candidate.origin = "user" if test.origin == "user" or candidate.code != test.code else test.origin
    idx = session.tests.index(test)
    session.tests[idx] = candidate
    session.touch()
    return candidate


def delete_test(session: Session, uid: str) -> None:
    test = find_test(session, uid)
    session.tests.remove(test)
    session.touch()


def draft_test(session: Session, provider: LLMProvider, category: str, description: str) -> TestCase:
    """Ask the LLM to write ONE missing test; the caller decides whether to keep it."""
    if not description.strip():
        raise ValueError("Describe the missing test in a sentence.")
    return provider.draft_test(session.requirements, session.language, session.source_code,
                               category, description)
