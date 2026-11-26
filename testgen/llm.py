"""LLM providers.

* `AnthropicProvider`  - calls the Anthropic Messages API over plain HTTPS (no SDK needed)
                         and forces a tool call so the answer is structured JSON.
* `OfflineDemoProvider` - returns a CANNED suite for the bundled sample so the whole
                         pipeline can be demoed without an API key. It is not AI and the UI
                         and reports say so.

Add another provider by subclassing `LLMProvider` and implementing `_call_tool`.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from . import prompts
from .models import TestCase
from .samples import password_validator as sample


class LLMError(RuntimeError):
    """Raised for any provider failure with a message that is safe to show the user."""


@dataclass
class SuiteDraft:
    tests: List[TestCase]
    implementation: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class FixDraft:
    code: str
    note: str = ""


def _tests_from_payload(items: Any, warnings: List[str]) -> List[TestCase]:
    tests: List[TestCase] = []
    if not isinstance(items, list):
        raise LLMError("The model returned no 'tests' list.")
    for i, raw in enumerate(items, 1):
        try:
            tests.append(TestCase(
                category=str(raw.get("category", "")),
                title=str(raw.get("title", "")),
                code=str(raw.get("code", "")),
                explanation=str(raw.get("explanation", "")),
                requirement=str(raw.get("requirement", "")),
                origin="ai",
            ).normalise())
        except (ValueError, AttributeError) as exc:
            warnings.append(f"Skipped model test #{i}: {exc}")
    return tests


class LLMProvider:
    name = "base"
    model = ""
    is_demo = False

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "model": self.model, "demo": self.is_demo}

    # -- to be provided by subclasses
    def _call_tool(self, system: str, user: str, tool_name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    # -- public API used by the generator
    def generate_suite(self, requirements: str, language: str, source_code: str,
                       per_category: int) -> SuiteDraft:
        user = prompts.build_suite_prompt(requirements, language, source_code, per_category)
        payload = self._call_tool(prompts.SYSTEM_PROMPT, user, prompts.SUITE_TOOL, prompts.SUITE_SCHEMA)
        warnings: List[str] = []
        tests = _tests_from_payload(payload.get("tests"), warnings)
        if not tests:
            raise LLMError("The model did not return any usable tests. Try again or refine the requirements.")
        impl = payload.get("implementation") or ""
        if not source_code.strip() and not impl.strip():
            raise LLMError("No code under test was supplied and the model did not write one.")
        return SuiteDraft(tests=tests, implementation="" if source_code.strip() else str(impl), warnings=warnings)

    def draft_test(self, requirements: str, language: str, source_code: str, category: str,
                   description: str) -> TestCase:
        user = prompts.build_draft_prompt(requirements, language, source_code, category, description)
        payload = self._call_tool(prompts.SYSTEM_PROMPT, user, prompts.DRAFT_TOOL, prompts.SUITE_SCHEMA)
        warnings: List[str] = []
        tests = _tests_from_payload(payload.get("tests"), warnings)
        if not tests:
            raise LLMError("The model did not return a usable test.")
        test = tests[0]
        test.category = category
        test.origin = "user"
        return test

    def fix_test(self, requirements: str, language: str, source_code: str, test: TestCase,
                 error: str) -> FixDraft:
        """Ask the model to repair a test body that does not parse. Nothing is applied here."""
        user = prompts.build_fix_prompt(requirements, language, source_code, test.title,
                                        test.explanation, test.code, error)
        payload = self._call_tool(prompts.SYSTEM_PROMPT, user, prompts.FIX_TOOL, prompts.FIX_SCHEMA)
        code = str(payload.get("code") or "").rstrip()
        if not code.strip():
            raise LLMError("The model did not return a fixed test.")
        return FixDraft(code=code, note=str(payload.get("change_note") or "").strip())


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: Optional[str] = None, base_url: Optional[str] = None,
                 max_tokens: int = 12000, timeout: int = 240):
        self.api_key = api_key
        self.model = model or os.environ.get("QA_LLM_MODEL", "claude-sonnet-5-5")
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _call_tool(self, system: str, user: str, tool_name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{
                "name": tool_name,
                "description": "Submit the finished result. This is the only way to answer.",
                "input_schema": schema,
            }],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        last_err = "unknown error"
        for attempt in range(3):
            try:
                resp = requests.post(f"{self.base_url}/v1/messages", headers=headers,
                                     data=json.dumps(body), timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = f"network error: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 529):
                last_err = f"HTTP {resp.status_code} (will retry)"
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise LLMError(f"Anthropic API error {resp.status_code}: {_error_text(resp)}")
            return self._extract_tool_input(resp.json(), tool_name)
        raise LLMError(f"Anthropic API unavailable after retries: {last_err}")

    @staticmethod
    def _extract_tool_input(data: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                inp = block.get("input")
                if isinstance(inp, dict):
                    return inp
        if data.get("stop_reason") == "max_tokens":
            raise LLMError("The model's answer was cut off (max_tokens). Ask for fewer tests per category.")
        raise LLMError("The model did not call the expected tool.")


def _error_text(resp: "requests.Response") -> str:
    try:
        err = resp.json().get("error", {})
        return err.get("message") or resp.text[:300]
    except ValueError:
        return resp.text[:300]


class OfflineDemoProvider(LLMProvider):
    """Canned results for the bundled password-validator sample. NOT an AI."""

    name = "offline-demo"
    model = "canned sample"
    is_demo = True

    def _is_sample(self, requirements: str) -> bool:
        low = requirements.lower()
        return any(m in low for m in sample.SAMPLE_MARKERS)

    def _filter(self, requirements: str):
        """Only keep canned tests whose requirement id (R1..R6) is still in the text."""
        present = set(re.findall(r"^\s*(R\d+)\b", requirements, flags=re.M))
        return present

    def generate_suite(self, requirements, language, source_code, per_category) -> SuiteDraft:
        if not self._is_sample(requirements):
            raise LLMError(
                "Offline demo mode only knows the bundled sample (click 'Load sample'). "
                "To use your own requirements set ANTHROPIC_API_KEY and restart."
            )
        present = self._filter(requirements)
        canned = sample.PY_TESTS if language == "python" else sample.JS_TESTS
        tests = [
            TestCase(category=t["category"], title=t["title"], code=t["code"],
                     explanation=t["explanation"], requirement=t["requirement"], origin="ai").normalise()
            for t in canned if not present or t["req_id"] in present
        ]
        warnings = ["Offline demo mode: these tests are canned, not AI generated."]
        if present and len(present) < 6:
            warnings.append("Tests for requirements you removed were dropped. New requirements "
                            "cannot be turned into tests without an API key.")
        if not tests:
            raise LLMError("No requirement ids (R1, R2, ...) found; the demo sample needs them.")
        impl = "" if source_code.strip() else (sample.PY_SOURCE if language == "python" else sample.JS_SOURCE)
        return SuiteDraft(tests=tests, implementation=impl, warnings=warnings)

    def draft_test(self, requirements, language, source_code, category, description) -> TestCase:
        raise LLMError("AI drafting needs an API key (offline demo mode cannot write new tests). "
                       "You can still add a test by typing its code.")

    def fix_test(self, requirements, language, source_code, test, error) -> FixDraft:
        from .repair import quick_fix
        found = quick_fix(test.code, language)
        if not found:
            raise LLMError("Offline demo mode could not fix this one with its simple rules. "
                           "Set ANTHROPIC_API_KEY for an AI fix, or edit the test by hand.")
        code, note = found
        return FixDraft(code=code, note=f"Offline demo (rule-based, not AI): {note}.")


def get_provider(preference: Optional[str] = None) -> LLMProvider:
    """auto (default): Anthropic when ANTHROPIC_API_KEY is set, otherwise the offline demo."""
    pref = (preference or os.environ.get("QA_LLM_PROVIDER") or "auto").lower()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if pref in ("demo", "offline", "offline-demo", "mock"):
        return OfflineDemoProvider()
    if pref in ("auto", "anthropic"):
        if key:
            return AnthropicProvider(api_key=key)
        if pref == "anthropic":
            raise LLMError("QA_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        return OfflineDemoProvider()
    raise LLMError(f"Unknown provider {pref!r}")
