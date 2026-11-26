"""Bundled sample used by the *offline demo provider* and the "Load sample" button.

It is built to show every feature in offline demo mode:

* R7 is DELIBERATELY vague ("should", "user-friendly"), so *Check requirements* has
  something to flag and a rewrite to suggest. No canned test covers it.
* One canned negative test has a DELIBERATE syntax error (Python: a missing ':',
  JavaScript: an unclosed bracket), so the run shows an ``error`` and *Fix with AI*
  (rule-based in demo mode) can repair it, with *Undo AI fix* to go back.
* The implementation contains one DELIBERATE off-by-one bug (upper length bound), so
  a real edge-case test fails and *Create bug report* has a real failure to report.
"""

SAMPLE_ID = "password_validator"
SAMPLE_MARKERS = ("validate_password", "validatepassword")

REQUIREMENTS = """\
Password validator -- function `validate_password(password)` (JavaScript: `validatePassword`).

R1. The password must be between 8 and 64 characters long (both bounds inclusive).
R2. It must contain at least one uppercase letter, one lowercase letter and one digit.
R3. It must contain at least one special character from: ! @ # $ % ^ & *
R4. It must not contain any whitespace.
R5. It must not be one of the common passwords "password", "12345678" or "qwerty123"
    (compared case-insensitively; a password that merely *contains* one is fine).
R6. It returns {"valid": bool, "errors": [codes]} using the codes too_short, too_long,
    no_upper, no_lower, no_digit, no_special, has_whitespace, common_password.
    All problems are reported, not just the first. A non-string input raises a
    TypeError.
R7. Error messages should be user-friendly.
"""

PY_SOURCE = '''\
"""Password validator (demo implementation).

NOTE: contains a deliberate off-by-one at the upper length bound (R1) so the demo
report shows what a failing edge-case test looks like.
"""

SPECIAL_CHARS = set("!@#$%^&*")
COMMON_PASSWORDS = {"password", "12345678", "qwerty123"}

MESSAGES = {
    "too_short": "Use at least 8 characters.",
    "too_long": "Use at most 64 characters.",
    "no_upper": "Add an uppercase letter.",
    "no_lower": "Add a lowercase letter.",
    "no_digit": "Add a digit.",
    "no_special": "Add one of ! @ # $ % ^ & *",
    "has_whitespace": "Remove spaces and other whitespace.",
    "common_password": "Choose a less common password.",
}


def validate_password(password):
    """Return {"valid": bool, "errors": [codes]} for the given password."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    errors = []
    if len(password) < 8:
        errors.append("too_short")
    if len(password) >= 64:  # BUG (deliberate): should be `> 64`
        errors.append("too_long")
    if not any(c.isupper() for c in password):
        errors.append("no_upper")
    if not any(c.islower() for c in password):
        errors.append("no_lower")
    if not any(c in "0123456789" for c in password):
        errors.append("no_digit")
    if not any(c in SPECIAL_CHARS for c in password):
        errors.append("no_special")
    if any(c.isspace() for c in password):
        errors.append("has_whitespace")
    if password.lower() in COMMON_PASSWORDS:
        errors.append("common_password")
    return {"valid": not errors, "errors": errors}


def describe_errors(errors):
    """Turn error codes into human-readable messages (helper, not in the spec)."""
    return [MESSAGES.get(code, code) for code in errors]
'''

JS_SOURCE = '''\
'use strict';
// Password validator (demo implementation).
//
// NOTE: contains a deliberate off-by-one at the upper length bound (R1) so the demo
// report shows what a failing edge-case test looks like.

const SPECIAL_CHARS = new Set('!@#$%^&*');
const COMMON_PASSWORDS = new Set(['password', '12345678', 'qwerty123']);

const MESSAGES = {
  too_short: 'Use at least 8 characters.',
  too_long: 'Use at most 64 characters.',
  no_upper: 'Add an uppercase letter.',
  no_lower: 'Add a lowercase letter.',
  no_digit: 'Add a digit.',
  no_special: 'Add one of ! @ # $ % ^ & *',
  has_whitespace: 'Remove spaces and other whitespace.',
  common_password: 'Choose a less common password.',
};

function validatePassword(password) {
  if (typeof password !== 'string') {
    throw new TypeError('password must be a string');
  }
  const chars = [...password];
  const errors = [];
  if (chars.length < 8) errors.push('too_short');
  if (chars.length >= 64) errors.push('too_long'); // BUG (deliberate): should be `> 64`
  if (!/\\p{Lu}/u.test(password)) errors.push('no_upper');
  if (!/\\p{Ll}/u.test(password)) errors.push('no_lower');
  if (!/[0-9]/.test(password)) errors.push('no_digit');
  if (!chars.some((c) => SPECIAL_CHARS.has(c))) errors.push('no_special');
  if (/\\s/u.test(password)) errors.push('has_whitespace');
  if (COMMON_PASSWORDS.has(password.toLowerCase())) errors.push('common_password');
  return { valid: errors.length === 0, errors };
}

function describeErrors(errors) {
  return errors.map((code) => MESSAGES[code] || code);
}

module.exports = { validatePassword, describeErrors };
'''


def _t(req_id, category, title, explanation, code, requirement):
    return {
        "req_id": req_id,
        "category": category,
        "title": title,
        "explanation": explanation,
        "requirement": requirement,
        "code": code,
    }


PY_TESTS = [
    # ------------------------------------------------------------- positive
    _t("R2", "positive", "Strong password is accepted",
       "A password that satisfies every rule must be reported as valid with no errors. "
       "This is the main happy path.",
       '''\
result = solution.validate_password("Str0ng!Pass")
assert result["valid"] is True
assert result["errors"] == []''',
       "R1-R3 happy path"),
    _t("R3", "positive", "Every allowed special character is accepted",
       "R3 lists ! @ # $ % ^ & * as special characters; each one on its own must "
       "satisfy the special-character rule.",
       '''\
for ch in "!@#$%^&*":
    result = solution.validate_password("Abcdef1" + ch)
    assert result["valid"] is True, f"{ch!r} should count as a special character"''',
       "R3 special characters"),
    _t("R5", "positive", "Password that merely contains a common word is allowed",
       "Only an exact (case-insensitive) match against the common-password list is "
       "rejected, so 'MyPassword1!' must pass.",
       '''\
result = solution.validate_password("MyPassword1!")
assert result["valid"] is True
assert "common_password" not in result["errors"]''',
       "R5 substring is fine"),
    _t("R6", "positive", "Result has exactly the documented shape",
       "Callers rely on the keys 'valid' and 'errors', so the result must expose "
       "exactly those two and 'errors' must be a list.",
       '''\
result = solution.validate_password("Str0ng!Pass")
assert set(result) == {"valid", "errors"}
assert isinstance(result["errors"], list)''',
       "R6 return shape"),
    # ------------------------------------------------------------- negative
    _t("R1", "negative", "Too-short password is rejected",
       "A 4-character password violates the 8-character minimum and must produce the "
       "'too_short' code.",
       '''\
result = solution.validate_password("Ab1!")
assert result["valid"] is False
assert "too_short" in result["errors"]''',
       "R1 minimum length"),
    _t("R2", "negative", "Password without an uppercase letter is rejected",
       "Everything else is fine here, so 'no_upper' must be the one and only error.",
       '''\
result = solution.validate_password("lowercase1!")
assert result["valid"] is False
assert result["errors"] == ["no_upper"]''',
       "R2 uppercase"),
    _t("R3", "negative", "Password without a special character is rejected",
       "Both passwords meet every other rule, so 'no_special' must be the only error. "
       "(This canned test has a deliberate syntax error, a missing ':', so the demo can "
       "show Fix with AI.)",
       '''\
for password in ("Abcdefg1", "ABCdef12")
    result = solution.validate_password(password)
    assert result["valid"] is False
    assert result["errors"] == ["no_special"]''',
       "R3 special character required"),
    _t("R4", "negative", "Password containing whitespace is rejected",
       "A space in the middle of an otherwise strong password must raise "
       "'has_whitespace' and nothing else.",
       '''\
result = solution.validate_password("Abcd 1234!")
assert result["valid"] is False
assert result["errors"] == ["has_whitespace"]''',
       "R4 whitespace"),
    _t("R5", "negative", "Common password is rejected regardless of case",
       "'QWERTY123' matches 'qwerty123' once lower-cased, so it must be flagged as a "
       "common password.",
       '''\
result = solution.validate_password("QWERTY123")
assert result["valid"] is False
assert "common_password" in result["errors"]''',
       "R5 common passwords"),
    _t("R6", "negative", "Non-string input raises TypeError",
       "Numbers and None are not passwords; the function must fail loudly with a "
       "TypeError instead of returning a result.",
       '''\
for bad in (12345, None, ["Abcdef1!"]):
    with pytest.raises(TypeError):
        solution.validate_password(bad)''',
       "R6 type check"),
    # ----------------------------------------------------------------- edge
    _t("R1", "edge", "Exactly 8 characters is accepted",
       "Lower boundary of R1: the shortest allowed password (8 characters) must be valid.",
       '''\
password = "Abcdef1!"
assert len(password) == 8
assert solution.validate_password(password)["valid"] is True''',
       "R1 lower bound"),
    _t("R1", "edge", "7 characters is rejected",
       "One below the lower boundary must be rejected with 'too_short'.",
       '''\
password = "Abcde1!"
assert len(password) == 7
result = solution.validate_password(password)
assert result["valid"] is False
assert "too_short" in result["errors"]''',
       "R1 lower bound - 1"),
    _t("R1", "edge", "Exactly 64 characters is accepted",
       "Upper boundary of R1 is inclusive, so a 64-character password must be valid. "
       "(The demo implementation has a deliberate off-by-one, so this test is expected "
       "to FAIL and shows how failures are reported.)",
       '''\
password = "Aa1!" + "x" * 60
assert len(password) == 64
result = solution.validate_password(password)
assert result["valid"] is True, result["errors"]''',
       "R1 upper bound"),
    _t("R1", "edge", "65 characters is rejected",
       "One above the upper boundary must be rejected with 'too_long'.",
       '''\
password = "Aa1!" + "x" * 61
assert len(password) == 65
result = solution.validate_password(password)
assert result["valid"] is False
assert "too_long" in result["errors"]''',
       "R1 upper bound + 1"),
    _t("R6", "edge", "Empty string reports every missing rule without crashing",
       "An empty string is still a string, so it must return a result listing all "
       "missing requirements rather than raising.",
       '''\
result = solution.validate_password("")
assert result["valid"] is False
assert set(result["errors"]) == {"too_short", "no_upper", "no_lower", "no_digit", "no_special"}''',
       "R6 report all problems"),
    _t("R2", "edge", "Non-ASCII letters count as upper/lower case",
       "Accented characters such as 'É' and 'é' are real letters; the length check must "
       "count characters, not bytes.",
       '''\
password = "\\u00c9a1!\\u00e9\\u00e9\\u00e9\\u00e9"
assert len(password) == 8
assert solution.validate_password(password)["valid"] is True''',
       "R1/R2 unicode"),
]

JS_TESTS = [
    # ------------------------------------------------------------- positive
    _t("R2", "positive", "Strong password is accepted",
       "A password that satisfies every rule must be reported as valid with no errors. "
       "This is the main happy path.",
       '''\
const result = solution.validatePassword('Str0ng!Pass');
assert.equal(result.valid, true);
assert.deepEqual(result.errors, []);''',
       "R1-R3 happy path"),
    _t("R3", "positive", "Every allowed special character is accepted",
       "R3 lists ! @ # $ % ^ & * as special characters; each one on its own must "
       "satisfy the special-character rule.",
       '''\
for (const ch of '!@#$%^&*') {
  const result = solution.validatePassword('Abcdef1' + ch);
  assert.equal(result.valid, true, `${ch} should count as a special character`);
}''',
       "R3 special characters"),
    _t("R5", "positive", "Password that merely contains a common word is allowed",
       "Only an exact (case-insensitive) match against the common-password list is "
       "rejected, so 'MyPassword1!' must pass.",
       '''\
const result = solution.validatePassword('MyPassword1!');
assert.equal(result.valid, true);
assert.ok(!result.errors.includes('common_password'));''',
       "R5 substring is fine"),
    _t("R6", "positive", "Result has exactly the documented shape",
       "Callers rely on the keys 'valid' and 'errors', so the result must expose "
       "exactly those two and 'errors' must be an array.",
       '''\
const result = solution.validatePassword('Str0ng!Pass');
assert.deepEqual(Object.keys(result).sort(), ['errors', 'valid']);
assert.ok(Array.isArray(result.errors));''',
       "R6 return shape"),
    # ------------------------------------------------------------- negative
    _t("R1", "negative", "Too-short password is rejected",
       "A 4-character password violates the 8-character minimum and must produce the "
       "'too_short' code.",
       '''\
const result = solution.validatePassword('Ab1!');
assert.equal(result.valid, false);
assert.ok(result.errors.includes('too_short'));''',
       "R1 minimum length"),
    _t("R2", "negative", "Password without an uppercase letter is rejected",
       "Everything else is fine here, so 'no_upper' must be the one and only error.",
       '''\
const result = solution.validatePassword('lowercase1!');
assert.equal(result.valid, false);
assert.deepEqual(result.errors, ['no_upper']);''',
       "R2 uppercase"),
    _t("R3", "negative", "Password without a special character is rejected",
       "'Abcdefg1' meets every other rule, so 'no_special' must be the only error. "
       "(This canned test has a deliberate syntax error, an unclosed bracket, so the demo "
       "can show Fix with AI.)",
       '''\
const result = solution.validatePassword('Abcdefg1');
assert.equal(result.valid, false);
assert.deepEqual(result.errors, ['no_special'];''',
       "R3 special character required"),
    _t("R4", "negative", "Password containing whitespace is rejected",
       "A space in the middle of an otherwise strong password must raise "
       "'has_whitespace' and nothing else.",
       '''\
const result = solution.validatePassword('Abcd 1234!');
assert.equal(result.valid, false);
assert.deepEqual(result.errors, ['has_whitespace']);''',
       "R4 whitespace"),
    _t("R5", "negative", "Common password is rejected regardless of case",
       "'QWERTY123' matches 'qwerty123' once lower-cased, so it must be flagged as a "
       "common password.",
       '''\
const result = solution.validatePassword('QWERTY123');
assert.equal(result.valid, false);
assert.ok(result.errors.includes('common_password'));''',
       "R5 common passwords"),
    _t("R6", "negative", "Non-string input throws TypeError",
       "Numbers, null and arrays are not passwords; the function must fail loudly with a "
       "TypeError instead of returning a result.",
       '''\
for (const bad of [12345, null, ['Abcdef1!']]) {
  assert.throws(() => solution.validatePassword(bad), TypeError);
}''',
       "R6 type check"),
    # ----------------------------------------------------------------- edge
    _t("R1", "edge", "Exactly 8 characters is accepted",
       "Lower boundary of R1: the shortest allowed password (8 characters) must be valid.",
       '''\
const password = 'Abcdef1!';
assert.equal(password.length, 8);
assert.equal(solution.validatePassword(password).valid, true);''',
       "R1 lower bound"),
    _t("R1", "edge", "7 characters is rejected",
       "One below the lower boundary must be rejected with 'too_short'.",
       '''\
const password = 'Abcde1!';
assert.equal(password.length, 7);
const result = solution.validatePassword(password);
assert.equal(result.valid, false);
assert.ok(result.errors.includes('too_short'));''',
       "R1 lower bound - 1"),
    _t("R1", "edge", "Exactly 64 characters is accepted",
       "Upper boundary of R1 is inclusive, so a 64-character password must be valid. "
       "(The demo implementation has a deliberate off-by-one, so this test is expected "
       "to FAIL and shows how failures are reported.)",
       '''\
const password = 'Aa1!' + 'x'.repeat(60);
assert.equal(password.length, 64);
const result = solution.validatePassword(password);
assert.equal(result.valid, true, result.errors.join(','));''',
       "R1 upper bound"),
    _t("R1", "edge", "65 characters is rejected",
       "One above the upper boundary must be rejected with 'too_long'.",
       '''\
const password = 'Aa1!' + 'x'.repeat(61);
assert.equal(password.length, 65);
const result = solution.validatePassword(password);
assert.equal(result.valid, false);
assert.ok(result.errors.includes('too_long'));''',
       "R1 upper bound + 1"),
    _t("R6", "edge", "Empty string reports every missing rule without throwing",
       "An empty string is still a string, so it must return a result listing all "
       "missing requirements rather than throwing.",
       '''\
const result = solution.validatePassword('');
assert.equal(result.valid, false);
assert.deepEqual(
  [...result.errors].sort(),
  ['no_digit', 'no_lower', 'no_special', 'no_upper', 'too_short'],
);''',
       "R6 report all problems"),
    _t("R2", "edge", "Non-ASCII letters count as upper/lower case",
       "Accented characters such as 'É' and 'é' are real letters; the length check must "
       "count characters, not bytes.",
       '''\
const password = '\\u00c9a1!\\u00e9\\u00e9\\u00e9\\u00e9';
assert.equal([...password].length, 8);
assert.equal(solution.validatePassword(password).valid, true);''',
       "R1/R2 unicode"),
]
