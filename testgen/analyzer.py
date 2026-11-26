"""Requirement analyzer: find vague, incomplete, untestable or contradictory requirements
and turn them into clarifying questions a QA can take back to the team.

Two engines share one output shape (`Analysis`):

* `rule_based_analysis` - deterministic checks (vague-word list, missing limits, open
  ranges, weak modals, TBDs, conflicting numeric limits, must vs must-not). Used in the
  offline demo mode and needs no API key.
* `ai_analysis`         - asks the LLM through a forced tool call (same transport as test
  generation). Used when an API key is set.

`analyze(text, provider)` picks the engine. `to_markdown(analysis)` produces the text the
QA pastes into email, Slack or JIRA.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

SEVERITIES = ("high", "medium", "low")
ISSUE_TYPES = {
    "vague": "Vague wording",
    "missing_limit": "Missing limit",
    "edge_case": "Unclear edge case",
    "ambiguous": "Ambiguous",
    "incomplete": "Incomplete",
    "untestable": "Hard to test",
    "error_handling": "Missing error behaviour",
    "contradiction": "Contradiction",
}

MAX_CHARS = 50_000


# ----------------------------------------------------------------------------- model
@dataclass
class Issue:
    type: str
    severity: str
    problem: str
    excerpt: str = ""
    suggestion: str = ""
    requirements: List[str] = field(default_factory=list)   # ids; >1 for cross-requirement issues

    def normalise(self) -> "Issue":
        self.type = self.type if self.type in ISSUE_TYPES else "ambiguous"
        self.severity = self.severity.lower() if self.severity.lower() in SEVERITIES else "medium"
        return self


@dataclass
class RequirementReport:
    id: str
    text: str
    issues: List[Issue] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    rewrite: str = ""

    @property
    def status(self) -> str:
        if any(i.severity == "high" for i in self.issues):
            return "needs_clarification"
        return "review" if self.issues else "clear"


@dataclass
class Analysis:
    engine: str                                   # "rules" | "ai"
    requirements: List[RequirementReport]
    cross_issues: List[Issue] = field(default_factory=list)
    general_questions: List[str] = field(default_factory=list)
    model: str = ""
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        issues = [i for r in self.requirements for i in r.issues] + self.cross_issues
        return {
            "requirements": len(self.requirements),
            "clear": sum(r.status == "clear" for r in self.requirements),
            "issues": len(issues),
            "high": sum(i.severity == "high" for i in issues),
            "medium": sum(i.severity == "medium" for i in issues),
            "low": sum(i.severity == "low" for i in issues),
            "contradictions": sum(i.type == "contradiction" for i in issues),
            "questions": sum(len(r.questions) for r in self.requirements)
                         + len(self.general_questions)
                         + sum(1 for i in self.cross_issues if i.suggestion),
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for r, rd in zip(self.requirements, d["requirements"]):
            rd["status"] = r.status
        d["summary"] = self.summary()
        d["issue_labels"] = ISSUE_TYPES
        return d


# ----------------------------------------------------------------------------- parsing
_ID_RE = re.compile(r"^\s*(?:[-*•]\s*)?(?P<id>(?:R|REQ|FR|NFR|US|AC)[-_ ]?\d+(?:\.\d+)*|\d+(?:\.\d+)*)[.):\-\s]\s*(?P<rest>.*)$",
                    re.I)
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(?P<rest>.+)$")


def split_requirements(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Return (preamble, [(id, text), ...]).

    Numbered lines (R1., REQ-2, 3), 1.2 ...) or bullets start a requirement; indented or
    plain lines continue the previous one. Text without any numbering is split into
    sentences and auto-numbered R1, R2, ...
    """
    items: List[List[str]] = []
    preamble: List[str] = []
    auto = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _ID_RE.match(line)
        b = None if m else _BULLET_RE.match(line)
        if m and m.group("rest").strip():
            rid = m.group("id").upper().replace(" ", "").replace("_", "-")
            if rid[0].isdigit():
                rid = "R" + rid
            items.append([rid, m.group("rest").strip()])
        elif b:
            auto += 1
            items.append([f"R{auto}", b.group("rest").strip()])
        elif items:
            items[-1][1] += " " + line.strip()
        else:
            preamble.append(line.strip())
    if not items:
        body = " ".join(preamble)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", body) if len(s.strip()) > 3]
        return "", [(f"R{i}", s) for i, s in enumerate(sentences, 1)]
    return " ".join(preamble), [(rid, re.sub(r"\s+", " ", t)) for rid, t in items]


# ----------------------------------------------------------------------------- rules
# term -> (question, suggested measurable replacement)
VAGUE_TERMS: Dict[str, Tuple[str, str]] = {
    "fast": ("What response time counts as fast, and under what load (e.g. 95th percentile)?",
             "responds within [N] ms at the 95th percentile with [M] concurrent users"),
    "quick": ("What is the maximum acceptable time, in seconds or milliseconds?", "completes within [N] ms"),
    "quickly": ("What is the maximum acceptable time, in seconds or milliseconds?", "within [N] ms"),
    "slow": ("What duration counts as slow?", "takes longer than [N] ms"),
    "responsive": ("What response time or breakpoints define responsive?",
                   "renders correctly at widths [320, 768, 1280] px and responds within [N] ms"),
    "performant": ("Which metric and target define acceptable performance?", "meets [metric] of [N]"),
    "efficient": ("Efficient in what: time, memory, cost? What is the target number?", "uses at most [N] MB / [N] ms"),
    "user-friendly": ("What observable behaviour makes it user friendly (task time, clicks, error rate, accessibility level)?",
                      "a first-time user completes [task] in under [N] steps without help"),
    "user friendly": ("What observable behaviour makes it user friendly (task time, clicks, error rate, accessibility level)?",
                      "a first-time user completes [task] in under [N] steps without help"),
    "easy": ("How do we measure easy (steps, time, success rate in a usability test)?", "takes at most [N] steps"),
    "easily": ("How do we measure easily (steps, time, success rate)?", "in at most [N] steps"),
    "intuitive": ("What would a tester check to decide whether it is intuitive?",
                  "[N]% of test users complete [task] without help"),
    "simple": ("Simple by which measure (steps, fields, reading level)?", "has at most [N] fields/steps"),
    "clean": ("What concrete rules define clean here?", "follows [style guide / rule]"),
    "modern": ("Which specific design system, browsers or standards does modern mean?", "follows [design system vX]"),
    "nice": ("What concrete, checkable property is meant by nice?", "[measurable property]"),
    "seamless": ("What exactly must not happen (reloads, re-login, data loss) for it to be seamless?",
                 "without a page reload or re-entering data"),
    "robust": ("Which failures must it survive, and what should happen in each case?",
               "handles [failure] by [behaviour]"),
    "reliable": ("What availability or error-rate target is required?", "available [99.9]% per month"),
    "stable": ("What uptime or crash-rate target is required?", "crash rate below [N] per 1,000 sessions"),
    "scalable": ("Scale to what: users, requests per second, data size? By when?",
                 "supports [N] concurrent users / [N] requests per second"),
    "secure": ("Which threats or standards apply (OWASP ASVS level, encryption, auth rules)?",
               "meets [OWASP ASVS L2 / specific control]"),
    "safe": ("Safe against what, and how would a tester verify it?", "[specific control]"),
    "flexible": ("Which specific changes must be possible without code changes?", "can be configured via [setting]"),
    "appropriate": ("Who decides what is appropriate, and what are the exact rules?", "[explicit rule]"),
    "adequate": ("What is the minimum that counts as adequate?", "at least [N]"),
    "sufficient": ("What is the minimum that counts as sufficient?", "at least [N]"),
    "reasonable": ("What exact value or range is reasonable?", "between [N] and [M]"),
    "acceptable": ("What exact threshold is acceptable?", "no more than [N]"),
    "optimal": ("Optimal by which metric, and compared to what baseline?", "[metric] at or below [N]"),
    "best": ("Best by which metric? Is there a threshold that is good enough?", "[metric] at or below [N]"),
    "minimal": ("What exact maximum is meant by minimal?", "at most [N]"),
    "minimum": ("What is the minimum value, as a number?", "at least [N]"),
    "maximum": ("What is the maximum value, as a number?", "at most [N]"),
    "large": ("How large, as a number with a unit?", "up to [N] [unit]"),
    "small": ("How small, as a number with a unit?", "at most [N] [unit]"),
    "long": ("How long, as a number with a unit?", "[N] characters / seconds"),
    "short": ("How short, as a number with a unit?", "at most [N] characters / seconds"),
    "many": ("How many exactly? Is there an upper limit?", "up to [N]"),
    "few": ("How many exactly?", "at most [N]"),
    "several": ("How many exactly?", "[N]"),
    "some": ("Which ones exactly? Can they be listed?", "[explicit list]"),
    "various": ("Which ones exactly? Can they be listed?", "[explicit list]"),
    "multiple": ("How many: is there a maximum?", "up to [N]"),
    "recent": ("How recent: within how many minutes, hours or days?", "within the last [N] days"),
    "soon": ("Within what time limit?", "within [N] seconds"),
    "timely": ("Within what time limit?", "within [N] seconds"),
    "frequently": ("How often exactly?", "every [N] minutes"),
    "periodically": ("How often exactly?", "every [N] minutes"),
    "regularly": ("How often exactly?", "every [N] minutes"),
    "real-time": ("What maximum delay still counts as real time?", "within [N] ms"),
    "real time": ("What maximum delay still counts as real time?", "within [N] ms"),
    "as soon as possible": ("What is the actual deadline or latency target?", "within [N] seconds"),
    "asap": ("What is the actual deadline or latency target?", "within [N] seconds"),
    "if possible": ("Is this required or not? If optional, what happens when it is not possible?",
                    "(make it mandatory, or state the fallback)"),
    "where possible": ("Is this required or not? In which cases is it allowed to be skipped?",
                       "(list the cases)"),
    "as needed": ("Who decides when it is needed, and based on what rule?", "when [condition]"),
    "as appropriate": ("What is the exact rule for when it applies?", "when [condition]"),
    "and so on": ("What is the complete list?", "[complete list]"),
    "etc": ("What is the complete list? 'etc.' leaves the scope open.", "[complete list]"),
    "e.g.": ("Are the examples the complete list, or only a sample? What is the full list?", "[complete list]"),
    "such as": ("Is the list after 'such as' complete? If not, what else is included?", "[complete list]"),
    "normally": ("What happens in the abnormal case?", "(state both cases)"),
    "usually": ("When does it not happen, and what is expected then?", "(state both cases)"),
    "typically": ("When does it not happen, and what is expected then?", "(state both cases)"),
    "generally": ("What are the exceptions?", "(state the exceptions)"),
    "relevant": ("Which items exactly are relevant? What is the rule?", "[explicit rule]"),
    "properly": ("What does correct behaviour look like, step by step?", "[expected result]"),
    "correctly": ("What is the exact expected output?", "[expected result]"),
    "gracefully": ("What should the user see and what state must be kept when it fails?",
                   "shows [message] and keeps [state]"),
    "high quality": ("Which measurable quality bar applies?", "[metric] at or above [N]"),
    "high performance": ("Which metric and target define high performance?", "[metric] of [N]"),
    "low latency": ("What latency target, at which percentile?", "under [N] ms at p95"),
    "lightweight": ("What size or memory limit applies?", "under [N] KB / MB"),
    "support": ("What exactly does 'support' mean here: which versions, which behaviour?", "works on [list]"),
    "handle": ("What exactly should happen? 'Handle' does not say the expected result.", "[expected result]"),
    "tbd": ("This is marked TBD. Who will decide it and by when?", ""),
    "tbc": ("This is marked TBC. Who will confirm it and by when?", ""),
    "todo": ("This is marked TODO. What is the missing content?", ""),
}
# words where a nearby number already makes the requirement measurable
_QUANTITY_WORDS = {"fast", "quick", "quickly", "slow", "large", "small", "long", "short", "many", "few",
                   "several", "multiple", "minimum", "maximum", "minimal", "recent", "soon", "timely",
                   "frequently", "periodically", "regularly", "real-time", "real time", "low latency",
                   "lightweight", "adequate", "sufficient", "reasonable", "acceptable", "efficient",
                   "reliable", "stable", "scalable", "responsive", "performant", "high performance"}
_OPEN_LIST = {"etc", "e.g.", "such as", "and so on", "some", "various"}
_HIGH_VAGUE = {"tbd", "tbc", "todo", "fast", "user-friendly", "user friendly", "intuitive", "easy",
               "secure", "robust", "scalable", "reliable", "appropriate", "as needed", "handle",
               "properly", "correctly", "gracefully", "seamless", "real-time", "real time",
               "as soon as possible", "asap", "if possible"}

_NUM = r"(\d+(?:[.,]\d+)?)"
_NUMBER_RE = re.compile(r"\d")
_UNITS = {
    "character": "characters", "characters": "characters", "chars": "characters", "char": "characters",
    "letter": "letters", "letters": "letters", "digit": "digits", "digits": "digits",
    "word": "words", "words": "words",
    "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    "s": "seconds", "sec": "seconds", "secs": "seconds", "second": "seconds", "seconds": "seconds",
    "min": "minutes", "mins": "minutes", "minute": "minutes", "minutes": "minutes",
    "hour": "hours", "hours": "hours", "day": "days", "days": "days",
    "kb": "KB", "mb": "MB", "gb": "GB", "item": "items", "items": "items",
    "attempt": "attempts", "attempts": "attempts", "tries": "attempts", "try": "attempts",
    "user": "users", "users": "users", "year": "years", "years": "years", "file": "files", "files": "files",
    "request": "requests", "requests": "requests", "result": "results", "results": "results",
    "message": "messages", "messages": "messages", "connection": "connections", "connections": "connections",
    "post": "posts", "posts": "posts", "time": "times", "times": "times", "row": "rows", "rows": "rows",
    "%": "%", "percent": "%",
}
_UNIT_RE = r"(" + "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True)) + r")\b"
_MIN_RE = re.compile(r"\b(?:at least|minimum(?: of)?|min\.?|no (?:less|fewer) than|not (?:less|fewer) than|more than|greater than|over)\s+"
                     + _NUM + r"(?:\s*" + _UNIT_RE + r")?", re.I)
_MAX_RE = re.compile(r"\b(?:at most|maximum(?: of)?|max\.?|no more than|not more than|up to|less than|fewer than|under|within|"
                     r"must not exceed|not exceed|cannot exceed|can't exceed|limited to)\s+" + _NUM + r"(?:\s*" + _UNIT_RE + r")?", re.I)
_RANGE_RE = re.compile(r"\b(?:between|from)\s+" + _NUM + r"\s*(?:" + _UNIT_RE + r")?\s*(?:and|to|-)\s*" + _NUM + r"\s*" + _UNIT_RE, re.I)
_RANGE_DASH_RE = re.compile(r"\b" + _NUM + r"\s*(?:-|–|to)\s*" + _NUM + r"\s*" + _UNIT_RE, re.I)
_EXACT_RE = re.compile(r"\b(?:exactly|must be|equal to)\s+" + _NUM + r"\s*" + _UNIT_RE, re.I)

# things that usually need a limit when mentioned
_LIMIT_TOPICS = {
    "length": "What are the minimum and maximum lengths, and are the bounds inclusive?",
    "password": "What are the length limits and allowed characters for the password?",
    "username": "What are the length limits and allowed characters for the username?",
    "name": "What is the maximum length of the name and which characters are allowed?",
    "upload": "What file types and maximum file size are allowed for upload?",
    "file": "What file types and maximum file size are allowed?",
    "attachment": "What file types and maximum size are allowed for attachments?",
    "image": "What image formats, dimensions and maximum file size are allowed?",
    "timeout": "How long is the timeout, exactly?",
    "session": "How long does a session last before it expires?",
    "retry": "How many retries, and with what delay between them?",
    "retries": "How many retries, and with what delay between them?",
    "attempts": "How many attempts are allowed, and what happens after the last one?",
    "amount": "What are the minimum and maximum amounts, and how many decimal places?",
    "price": "What is the allowed range, currency and precision?",
    "quantity": "What are the minimum and maximum quantities?",
    "age": "What is the allowed age range, and are the bounds inclusive?",
    "limit": "What is the limit, as a number?",
    "page size": "How many items per page, and what is the maximum?",
    "pagination": "How many items per page, and what is the maximum?",
    "search": "What is the maximum number of results, and how are they ordered?",
    "comment": "What is the maximum comment length?",
    "message": "What is the maximum message length?",
    "description": "What is the maximum description length?",
    "title": "What is the maximum title length?",
    "bio": "What is the maximum length of the bio?",
    "headline": "What is the maximum headline length?",
    "email": "Which email formats are valid, and what is the maximum length?",
    "phone": "Which phone formats and countries are accepted?",
}
_ERROR_WORDS = re.compile(r"\b(error|errors|reject|rejects|rejected|raise|raises|throw|throws|exception|message|"
                          r"warn|warning|display|show|shows|return|returns|fail|fails|deny|denied|block|blocked|"
                          r"prevent|prevented|disable|disabled|invalid|code|codes|status)\b", re.I)
_VALIDATION_WORDS = re.compile(r"\b(must|shall|required|only|valid|validate|validated|cannot|can't|must not|"
                               r"not allowed|allowed|accept|accepts)\b", re.I)
_WEAK_MODAL = re.compile(r"\b(should|may|might|could|can|ideally|preferably|optionally)\b", re.I)
_STRONG_MODAL = re.compile(r"\b(must|shall|will|is required to|required)\b", re.I)
_ABSOLUTE = re.compile(r"\b(never|always|100\s*%|all cases|every case|any input|any situation|zero (?:bugs|errors|downtime)|"
                       r"under all|in all)\b", re.I)
_AND_OR = re.compile(r"\band/or\b|\b\w+/\w+\b", re.I)
_PRONOUN_START = re.compile(r"^(it|this|that|they|these|those)\b", re.I)
_PASSIVE_NO_ACTOR = re.compile(r"\b(?:is|are|will be|must be|should be)\s+(notified|informed|alerted|sent|logged|reviewed|approved)\b(?!.*\bby\b)",
                               re.I)

_STOP = set("""a an the and or of to in on for with without by from at as is are be been being must shall should
will can may might could not no it its this that these those than then when if else each every any all some
user users system app application page field value values valid invalid only least most more less one two three
characters character must also which who what where have has had do does done into over under up down out
""".split())


def _term_pattern(term: str) -> re.Pattern:
    if term in ("etc", "e.g."):
        return re.compile(r"\b" + re.escape(term.rstrip(".")) + r"\.?", re.I)
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.I)


_TERM_PATTERNS = {t: _term_pattern(t) for t in VAGUE_TERMS}


def _excerpt(text: str, start: int, end: int, pad: int = 28) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z\-]{3,}", text.lower()) if w not in _STOP}


def _limits(text: str) -> List[Tuple[str, str, float]]:
    """[(kind, unit, value)] with kind in min|max."""
    out: List[Tuple[str, str, float]] = []
    taken: List[Tuple[int, int]] = []
    f = lambda s: float(s.replace(",", "."))
    for rx in (_RANGE_RE, _RANGE_DASH_RE):
        for m in rx.finditer(text):
            g = m.groups()
            unit = _UNITS[g[-1].lower()]
            out += [("min", unit, f(g[0])), ("max", unit, f(g[-2]))]
            taken.append(m.span())
    inside = lambda m: any(a <= m.start() < b for a, b in taken)
    # "limited to 200 per week": no unit after the number, so borrow the first unit word in the sentence
    first_unit = re.search(r"\b" + _UNIT_RE, text, re.I)
    fallback = _UNITS[first_unit.group(1).lower()] if first_unit else ""
    for kind, rx in (("min", _MIN_RE), ("max", _MAX_RE)):
        for m in rx.finditer(text):
            unit = _UNITS[m.group(2).lower()] if m.group(2) else fallback
            if not inside(m) and unit and not (kind == "max" and m.group(0).lower().startswith("within") and not m.group(2)):
                out.append((kind, unit, f(m.group(1))))
    for m in _EXACT_RE.finditer(text):
        if not inside(m):
            v, u = f(m.group(1)), _UNITS[m.group(2).lower()]
            out += [("min", u, v), ("max", u, v)]
    return out


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def _check_one(rid: str, text: str, has_context: bool = False) -> RequirementReport:
    rep = RequirementReport(id=rid, text=text)
    has_number = bool(_NUMBER_RE.search(text))
    low = text.lower()
    rewrite = text
    seen_questions = set()

    def add(issue: Issue, question: str = ""):
        rep.issues.append(issue.normalise())
        if question and question not in seen_questions:
            seen_questions.add(question)
            rep.questions.append(question)

    # 1. vague words
    matched_spans: List[Tuple[int, int]] = []
    for term, (question, better) in VAGUE_TERMS.items():
        pat = _TERM_PATTERNS[term]
        m = pat.search(text)
        if not m or any(a <= m.start() < b for a, b in matched_spans):
            continue
        if term in _QUANTITY_WORDS and has_number:
            continue
        if term in ("long", "short", "minimum", "maximum", "support", "handle", "some", "multiple",
                    "simple", "clean", "best", "small", "large", "few") and not _vague_usage(term, text, m):
            continue
        matched_spans.append(m.span())
        if term in ("tbd", "tbc", "todo"):
            add(Issue("incomplete", "high", f"Marked '{m.group(0)}': the requirement is not finished.",
                      _excerpt(text, *m.span()), "Fill in the missing detail before test design.", [rid]), question)
            continue
        sev = "high" if term in _HIGH_VAGUE else "medium"
        problem = (f"'{m.group(0)}' leaves the list open, so testers cannot know what must be covered."
                   if term in _OPEN_LIST else f"'{m.group(0)}' is subjective; two testers could disagree on pass or fail.")
        if better and not better.startswith("(") and term not in _OPEN_LIST:
            rewrite = pat.sub(lambda mm: f"{mm.group(0)} (define: {better})", rewrite, count=1)
        add(Issue("vague", sev, problem,
                  _excerpt(text, *m.span()),
                  "List every item explicitly." if term in _OPEN_LIST
                  else (f"Replace with a measurable criterion, e.g. \"{better}\"." if better else ""),
                  [rid]), question)

    # 2. weak modal verbs: is it mandatory?
    wm = _WEAK_MODAL.search(text)
    if wm and not _STRONG_MODAL.search(text):
        word = wm.group(0)
        add(Issue("ambiguous", "medium" if word.lower() in ("should", "may", "might", "could") else "low",
                  f"'{word}' does not say whether this is mandatory, so a failure may not count as a defect.",
                  _excerpt(text, *wm.span()), "Use 'must' for mandatory behaviour, or mark it explicitly optional.", [rid]),
            f"Is this mandatory ('must') or optional? If optional, what is the expected behaviour when it is absent?")
        if word.lower() in ("should", "may", "might", "could"):
            rewrite = re.sub(r"\b" + word + r"\b", "must", rewrite, count=1)

    # 3. and/or, slashes
    ao = _AND_OR.search(text)
    if ao and not re.search(r"\d+/\d+|https?://|[a-z]+\.[a-z]+/", ao.group(0), re.I) and \
            ao.group(0).lower() not in ("i/o", "n/a", "24/7", "w/o") and "/" in ao.group(0):
        add(Issue("ambiguous", "medium", f"'{ao.group(0)}' can mean both or either.",
                  _excerpt(text, *ao.span()), "Say 'both', 'either', or 'at least one of'.", [rid]),
            f"Does '{ao.group(0)}' mean both, either one, or at least one of them?")

    # 4. open range: bounds inclusive?
    if (_RANGE_RE.search(text) or _RANGE_DASH_RE.search(text)) and not re.search(r"inclusive|exclusive|including|excluding", low):
        m = _RANGE_RE.search(text) or _RANGE_DASH_RE.search(text)
        add(Issue("edge_case", "medium", "The range does not say whether the boundary values themselves are allowed.",
                  _excerpt(text, *m.span()), "Add 'inclusive' or 'exclusive' so boundary tests have a clear expected result.", [rid]),
            "Are the boundary values included (inclusive) or excluded?")
    for m in re.finditer(r"\b(more than|greater than|less than|fewer than|over|under|above|below)\s+\d", text, re.I):
        add(Issue("edge_case", "low", f"'{m.group(1)}' excludes the number itself; confirm that is intended.",
                  _excerpt(text, *m.span()), "Use 'at least' / 'at most' if the number itself is allowed.", [rid]),
            f"Is the exact value after '{m.group(1)}' allowed, or only values beyond it?")
        break

    # 5. topic that normally needs a limit, but no number at all
    if not has_number:
        for topic, question in _LIMIT_TOPICS.items():
            m = re.search(r"\b" + re.escape(topic) + r"s?\b", low)
            if m:
                add(Issue("missing_limit", "medium", f"Mentions '{topic}' but gives no limit or size.",
                          _excerpt(text, *m.span()), "State the minimum and maximum (with units).", [rid]), question)
                break

    # 7. absolute / untestable claims
    ab = _ABSOLUTE.search(text)
    if ab:
        add(Issue("untestable", "medium", f"'{ab.group(0)}' is an absolute claim that no finite test run can prove.",
                  _excerpt(text, *ab.span()), "Narrow it to named scenarios, inputs or a measurable rate.", [rid]),
            f"Can '{ab.group(0)}' be narrowed to specific scenarios or a measurable rate we can test?")

    # 8. passive voice with no actor
    pv = _PASSIVE_NO_ACTOR.search(text)
    if pv:
        add(Issue("incomplete", "low", f"'{pv.group(0)}' does not say who or what does it, or who receives it.",
                  _excerpt(text, *pv.span()), "Name the actor and the recipient, and the channel (email, in-app, log).", [rid]),
            f"Who is {pv.group(1)}, by what, and through which channel?")

    # 9. dangling reference
    if not has_context and _PRONOUN_START.match(text) and len(text.split()) < 25:
        add(Issue("ambiguous", "low", "Starts with a pronoun, so the subject depends on the surrounding text.",
                  _excerpt(text, 0, len(text.split()[0])), "Name the subject explicitly.", [rid]),
            "Which component, screen or field does this requirement refer to?")

    # 10. contradiction inside one requirement
    for issue in _limit_conflicts([(rid, text)]):
        add(issue, issue.suggestion)

    # 11. too short to test
    if len(text.split()) < 4 and not rep.issues:
        add(Issue("incomplete", "medium", "Too short to derive expected results from.", text,
                  "Describe the input, the action and the expected outcome.", [rid]),
            "What is the input, the action, and the expected result for this requirement?")

    if rep.issues and rewrite != text:
        rep.rewrite = rewrite
    return rep


def _vague_usage(term: str, text: str, m: re.Match) -> bool:
    """Filter words that are only vague in some uses ('long' in 'as long as', 'support' as a noun...)."""
    before = text[max(0, m.start() - 12):m.start()].lower()
    after = text[m.end():m.end() + 14].lower()
    if term == "long":
        return not before.endswith("as ") and not after.startswith(" as") and "characters long" not in text.lower()
    if term == "short":
        return "too short" not in text.lower() and "too_short" not in text.lower()
    if term in ("minimum", "maximum"):
        return not _NUMBER_RE.search(after)
    if term == "support":
        return before.endswith("must ") or before.endswith("should ") or before.endswith("shall ")
    if term == "handle":
        return bool(re.search(r"(must|should|shall|will|to)\s+$", before))
    if term == "some":
        return not after.startswith(" of the following")
    if term in ("simple", "clean", "best", "small", "large", "few", "multiple"):
        return True
    return True


def _limit_conflicts(reqs: List[Tuple[str, str]]) -> List[Issue]:
    issues: List[Issue] = []
    data = [(rid, text, _limits(text), _keywords(text)) for rid, text in reqs]
    single = len(reqs) == 1
    seen = set()
    for i, (ra, ta, la, ka) in enumerate(data):
        for j, (rb, tb, lb, kb) in enumerate(data):
            if single:
                if i != j:
                    continue
            elif j <= i:
                continue
            shared_all = ka & kb
            for kind_a, unit_a, va in la:
                for kind_b, unit_b, vb in lb:
                    if unit_a != unit_b or (single and kind_a != "min"):
                        continue
                    shared = {w for w in shared_all if _UNITS.get(w) != unit_a}
                    if not single and not shared:
                        continue
                    subject = ", ".join(sorted(shared)[:3])
                    ids = [ra] if single else [ra, rb]
                    key = (tuple(ids), unit_a, kind_a, kind_b, va, vb)
                    if key in seen:
                        continue
                    if kind_a == "min" and kind_b == "max" and va > vb or kind_a == "max" and kind_b == "min" and vb > va:
                        lo, hi = (va, vb) if kind_a == "min" else (vb, va)
                        seen.add(key)
                        where = f"{ra} requires" if single else f"{ra} and {rb} require"
                        issues.append(Issue(
                            "contradiction", "high",
                            f"{where} at least {_fmt(lo)} {unit_a} but at most {_fmt(hi)} {unit_a}; no value can satisfy both.",
                            "", f"Which limit is correct: at least {_fmt(lo)} {unit_a} or at most {_fmt(hi)} {unit_a}?",
                            ids))
                    elif not single and kind_a == kind_b and va != vb:
                        seen.add(key)
                        word = "minimum" if kind_a == "min" else "maximum"
                        issues.append(Issue(
                            "contradiction", "medium",
                            f"{ra} and {rb} give different {word}s for {unit_a} ({_fmt(va)} vs {_fmt(vb)})"
                            + (f" and both mention {subject}." if subject else "."),
                            "", f"{ra} says a {word} of {_fmt(va)} {unit_a} and {rb} says {_fmt(vb)} {unit_a}. "
                                f"Which one is correct, or do they apply to different things?", ids))
    return issues


_POLARITY_RE = re.compile(r"\b(must|shall|should|will|can|may)(\s+not|n't)?\s+((?:\w+\s+){1,4}?\w+)\b", re.I)
_FILLER = {"any", "a", "an", "the", "be", "contain", "contains", "include", "includes", "allow", "allowed", "always", "ever"}


def _polarity_phrases(text: str) -> List[Tuple[bool, str, str]]:
    out = []
    for m in _POLARITY_RE.finditer(text):
        neg = bool(m.group(2))
        words = [w.lower() for w in m.group(3).split()]
        core = [w for w in words if w not in _FILLER]
        if not core:
            continue
        verb = next((w for w in words if w in _FILLER - {"any", "a", "an", "the", "be", "always", "ever"}), "")
        out.append((neg, (verb + " " if verb else "") + " ".join(core[:2]), m.group(0)))
    return out


def _polarity_conflicts(reqs: List[Tuple[str, str]]) -> List[Issue]:
    issues = []
    phrases = [(rid, _polarity_phrases(text)) for rid, text in reqs]
    for i, (ra, pa) in enumerate(phrases):
        for rb, pb in phrases[i + 1:]:
            for neg_a, core_a, raw_a in pa:
                for neg_b, core_b, raw_b in pb:
                    if neg_a != neg_b and core_a == core_b:
                        issues.append(Issue(
                            "contradiction", "high",
                            f"{ra} says \"{raw_a}\" but {rb} says \"{raw_b}\".", "",
                            f"{ra} and {rb} disagree about \"{core_a}\". Which one is correct, and in which situation?",
                            [ra, rb]))
    return issues


_REQ_OPT_RE = re.compile(r"\b([a-z][\w ]{1,30}?)\s+(?:is|are|field is|fields are)\s+(optional|required|mandatory)\b", re.I)


def _optional_required_conflicts(reqs: List[Tuple[str, str]]) -> List[Issue]:
    found: Dict[str, List[Tuple[str, str]]] = {}
    for rid, text in reqs:
        for m in _REQ_OPT_RE.finditer(text):
            subj = " ".join(w for w in m.group(1).lower().split() if w not in _STOP)[-30:].strip()
            if subj:
                found.setdefault(subj, []).append((rid, "optional" if m.group(2).lower() == "optional" else "required"))
    issues = []
    for subj, vals in found.items():
        kinds = {v for _, v in vals}
        if len(kinds) > 1:
            ids = sorted({r for r, _ in vals})
            issues.append(Issue("contradiction", "high",
                                f"'{subj}' is called optional in one place and required in another ({', '.join(ids)}).",
                                "", f"Is '{subj}' optional or required? If it depends, on what?", ids))
    return issues


def _drop_limits_defined_elsewhere(reports: List[RequirementReport]) -> None:
    """'Headline' has no limit in R7, but R3 gives one: do not ask again."""
    limited = " ".join(r.text.lower() for r in reports if _limits(r.text))
    for r in reports:
        keep, dropped = [], []
        for i in r.issues:
            m = re.match(r"Mentions '(.+?)'", i.problem) if i.type == "missing_limit" else None
            (dropped if m and re.search(r"\b" + re.escape(m.group(1)), limited) else keep).append(i)
        if dropped:
            r.issues = keep
            drop_q = {_LIMIT_TOPICS[re.match(r"Mentions '(.+?)'", i.problem).group(1)] for i in dropped}
            r.questions = [q for q in r.questions if q not in drop_q]


def rule_based_analysis(text: str) -> Analysis:
    text = _validate(text)
    preamble, reqs = split_requirements(text)
    reports = [_check_one(rid, t, has_context=bool(preamble) or i > 0) for i, (rid, t) in enumerate(reqs)]
    _drop_limits_defined_elsewhere(reports)
    cross = _limit_conflicts(reqs) + _polarity_conflicts(reqs) + _optional_required_conflicts(reqs) if len(reqs) > 1 else []
    ids = [r for r, _ in reqs]
    dup = sorted({r for r in ids if ids.count(r) > 1})

    general: List[str] = []
    all_text = text.lower()
    if not _ERROR_WORDS.search(all_text):
        general.append("None of the requirements describe what happens on invalid input or failure. "
                       "What should the user see, and what error codes or messages are expected?")
    if not re.search(r"\b(empty|blank|null|none|missing|zero)\b", all_text):
        general.append("What is the expected behaviour for empty, blank or missing input?")
    if len(reqs) >= 3 and not re.search(r"\b(role|admin|guest|logged[- ]in|authenticated|permission|anonymous)\b", all_text) \
            and re.search(r"\b(user|users|member|members|account)\b", all_text):
        general.append("Do these rules apply to every user type (guest, signed-in member, admin), or only some?")
    warnings = ["Offline mode: rule-based checks (vague-word list, missing numbers, conflicting limits). "
                "Set ANTHROPIC_API_KEY for a deeper AI review."]
    if dup:
        warnings.append(f"Duplicate requirement ids: {', '.join(dup)}. Give each requirement a unique id.")
    if not any(_ID_RE.match(l) or _BULLET_RE.match(l) for l in text.splitlines()):
        warnings.append("No numbered requirements found, so sentences were numbered R1, R2, ... automatically.")
    return Analysis(engine="rules", requirements=reports, cross_issues=cross, general_questions=general,
                    warnings=warnings)


def _validate(text: str) -> str:
    text = (text or "").strip()
    if len(text) < 10:
        raise ValueError("Enter the requirements first (at least a sentence).")
    if len(text) > MAX_CHARS:
        raise ValueError(f"Requirements are too long to analyze ({len(text):,} characters, max {MAX_CHARS:,}).")
    return text


# ----------------------------------------------------------------------------- AI
ANALYZE_TOOL = "submit_requirement_analysis"

_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(ISSUE_TYPES)},
        "severity": {"type": "string", "enum": list(SEVERITIES),
                     "description": "high = cannot write a test with a clear expected result; "
                                    "medium = testers would likely disagree; low = worth confirming."},
        "excerpt": {"type": "string", "description": "The exact words from the requirement that cause the problem."},
        "problem": {"type": "string", "description": "One sentence: why this is a problem for testing."},
        "suggestion": {"type": "string", "description": "How to fix it, concretely."},
    },
    "required": ["type", "severity", "problem"],
}

ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "description": "One entry per requirement, in the original order, using the original ids.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string", "description": "The requirement text as given."},
                    "issues": {"type": "array", "items": _ISSUE_SCHEMA},
                    "questions": {"type": "array", "items": {"type": "string"},
                                  "description": "Clarifying questions for the product owner / team, "
                                                 "specific and answerable (ideally with a number or yes/no)."},
                    "rewrite": {"type": "string",
                                "description": "A clearer, testable version. Use [placeholders] for values only the "
                                               "team can decide. Empty when the requirement is already clear."},
                },
                "required": ["id", "text", "issues", "questions"],
            },
        },
        "contradictions": {
            "type": "array",
            "description": "Conflicts BETWEEN requirements (or inside one).",
            "items": {
                "type": "object",
                "properties": {
                    "requirements": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "problem": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["requirements", "problem", "question"],
            },
        },
        "general_questions": {"type": "array", "items": {"type": "string"},
                              "description": "Questions about gaps not tied to one requirement "
                                             "(missing scenarios, roles, error handling, non-functional needs)."},
    },
    "required": ["requirements"],
}

ANALYZE_SYSTEM = """\
You are a senior QA analyst reviewing requirements BEFORE any tests are written. Your job
is to find what would stop a tester from writing a test with an unambiguous expected result,
and to produce clarifying questions the QA can send to the product owner and developers.

Look for:
- vague or subjective wording (fast, user-friendly, intuitive, secure, appropriate, etc.)
- missing limits, units, formats, or boundary rules (inclusive/exclusive)
- unclear edge cases (empty, null, max+1, duplicates, concurrency, time zones, locales)
- ambiguity (should vs must, and/or, unclear pronouns, passive voice with no actor)
- untestable absolutes (never, always, 100%)
- missing error behaviour (what happens when a rule is violated)
- incomplete items (TBD, missing actor, missing expected result)
- contradictions between requirements, or inside one

Rules
- Only report real problems. A clear requirement gets an empty issues list.
- Quote the exact problem words in `excerpt`.
- Questions are specific, polite, and answerable; prefer ones that can be answered with a
  number, a list, or yes/no. Do not ask the same question twice.
- Rewrites keep the original intent and use [placeholders] for values the team must decide.
- Keep the original requirement ids. If the text has no ids, number them R1, R2, ...

Always answer by calling the provided tool. Never reply with prose."""


def build_analyze_prompt(text: str, rule_hints: Optional[Analysis] = None) -> str:
    preamble, reqs = split_requirements(text)
    parts = ["=== REQUIREMENTS ===", text.strip(), ""]
    if reqs:
        parts += ["Detected requirement ids: " + ", ".join(r for r, _ in reqs), ""]
    if rule_hints:
        hints = [f"- {r.id}: {i.problem}" for r in rule_hints.requirements for i in r.issues][:30]
        hints += [f"- {', '.join(i.requirements)}: {i.problem}" for i in rule_hints.cross_issues][:10]
        if hints:
            parts += ["A keyword scanner flagged these (may include false positives; verify each, "
                      "drop the wrong ones, and add anything it missed):", *hints, ""]
    parts.append("Analyze every requirement.")
    return "\n".join(parts)


def _str_list(v: Any) -> List[str]:
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def analysis_from_payload(payload: Dict[str, Any], model: str = "") -> Analysis:
    items = payload.get("requirements")
    if not isinstance(items, list) or not items:
        from .llm import LLMError
        raise LLMError("The model returned no requirement analysis. Try again.")
    reports = []
    for n, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            continue
        rid = str(raw.get("id") or f"R{n}").strip()
        issues = []
        for it in raw.get("issues") or []:
            if isinstance(it, dict) and str(it.get("problem", "")).strip():
                issues.append(Issue(str(it.get("type", "ambiguous")), str(it.get("severity", "medium")),
                                    str(it["problem"]).strip(), str(it.get("excerpt", "")).strip(),
                                    str(it.get("suggestion", "")).strip(), [rid]).normalise())
        reports.append(RequirementReport(rid, str(raw.get("text", "")).strip(), issues,
                                         _str_list(raw.get("questions")), str(raw.get("rewrite") or "").strip()))
    cross = []
    for c in payload.get("contradictions") or []:
        if isinstance(c, dict) and str(c.get("problem", "")).strip():
            cross.append(Issue("contradiction", str(c.get("severity", "high")), str(c["problem"]).strip(), "",
                               str(c.get("question", "")).strip(), _str_list(c.get("requirements"))).normalise())
    return Analysis(engine="ai", requirements=reports, cross_issues=cross,
                    general_questions=_str_list(payload.get("general_questions")), model=model)


def ai_analysis(text: str, provider) -> Analysis:
    text = _validate(text)
    hints = rule_based_analysis(text)
    payload = provider._call_tool(ANALYZE_SYSTEM, build_analyze_prompt(text, hints), ANALYZE_TOOL, ANALYZE_SCHEMA)
    return analysis_from_payload(payload, getattr(provider, "model", ""))


def analyze(text: str, provider=None) -> Analysis:
    """AI analysis when a real provider is configured, rule-based otherwise."""
    if provider is None or getattr(provider, "is_demo", True):
        return rule_based_analysis(text)
    return ai_analysis(text, provider)


# ----------------------------------------------------------------------------- output
_SEV_MARK = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def to_markdown(a: Analysis, title: str = "Requirement clarification questions") -> str:
    """Questions first (that is what goes to the team), findings after."""
    sm = a.summary()
    out = [f"## {title}", ""]
    reqs = f"{sm['requirements']} requirement{'s' if sm['requirements'] != 1 else ''}"
    if sm["issues"]:
        out.append(f"I reviewed {reqs} before writing tests and found {sm['issues']} "
                   f"point{'s' if sm['issues'] != 1 else ''} to clarify"
                   + (f", including {sm['contradictions']} possible contradiction{'s' if sm['contradictions'] != 1 else ''}"
                      if sm["contradictions"] else "") + ". Could you help with the questions below?")
    else:
        out.append(f"I reviewed {reqs} before writing tests. They look specific and testable"
                   + (", with a few general questions below." if a.general_questions else "."))
    out.append("")
    n = 0
    if a.cross_issues:
        out += ["### Conflicts between requirements", ""]
        for c in a.cross_issues:
            n += 1
            ids = ", ".join(c.requirements)
            out.append(f"{n}. **{ids}** {c.suggestion or c.problem}")
            if c.suggestion:
                out.append(f"   - _Why:_ {c.problem}")
        out.append("")
    per_req = [r for r in a.requirements if r.questions]
    if per_req:
        out += ["### Questions by requirement", ""]
        for r in per_req:
            out.append(f"**{r.id}** {_clip(r.text, 160)}")
            out.append("")
            for q in r.questions:
                n += 1
                out.append(f"{n}. {q}")
            if r.rewrite:
                out.append(f"   - _Suggested wording:_ {r.rewrite}")
            out.append("")
    if a.general_questions:
        out += ["### General", ""]
        for q in a.general_questions:
            n += 1
            out.append(f"{n}. {q}")
        out.append("")
    if n == 0:
        out += ["No open questions: every requirement looks specific and testable.", ""]
    findings = [(r.id, i) for r in a.requirements for i in r.issues]
    if findings:
        out += ["<details><summary>Findings detail</summary>", "",
                "| Req | Severity | Type | Problem |", "|---|---|---|---|"]
        for rid, i in findings:
            prob = i.problem.replace("|", "\\|")
            out.append(f"| {rid} | {_SEV_MARK[i.severity]} | {ISSUE_TYPES[i.type]} | {prob} |")
        out += ["", "</details>", ""]
    return "\n".join(out).rstrip() + "\n"


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
