"""Built-in checks and the custom check registry.

Each case type has two functions:

* a validator, run when the suite is loaded, returning a list of problems
  with the case definition, and
* an evaluator, run against a model output, returning a CheckResult.

Custom checks are registered with :func:`register_check` and referenced
from a suite with ``type: custom`` and ``check: <name>``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from modeldiff.cases import TestCase

MAX_REPORTED_SCHEMA_ERRORS = 5


@dataclass
class CheckResult:
    """Outcome of evaluating one output against one case."""

    passed: bool
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


CustomCheck = Callable[[TestCase, str], "CheckResult | bool"]
_CUSTOM_CHECKS: dict[str, CustomCheck] = {}


def register_check(name: str) -> Callable[[CustomCheck], CustomCheck]:
    """Register a custom check under ``name``.

    The decorated function receives the case and the raw output string and
    returns either a :class:`CheckResult` or a bool::

        from modeldiff import CheckResult, register_check

        @register_check("max_words")
        def max_words(case, output):
            limit = case.params["limit"]
            words = len(output.split())
            return CheckResult(words <= limit, f"{words} words > {limit}" if words > limit else None)
    """

    def decorator(func: CustomCheck) -> CustomCheck:
        existing = _CUSTOM_CHECKS.get(name)
        # Re-running the same definition (a plugin file loaded twice) is fine;
        # two different functions claiming one name is a mistake.
        if existing is not None and (existing.__module__, existing.__qualname__) != (
            func.__module__,
            func.__qualname__,
        ):
            raise ValueError(f"a custom check named {name!r} is already registered")
        _CUSTOM_CHECKS[name] = func
        return func

    return decorator


def registered_checks() -> list[str]:
    return sorted(_CUSTOM_CHECKS)


def unregister_check(name: str) -> None:
    _CUSTOM_CHECKS.pop(name, None)


# --- helpers -----------------------------------------------------------------


def _parse_json(output: str) -> tuple[Any, CheckResult | None]:
    try:
        return json.loads(output), None
    except json.JSONDecodeError as exc:
        reason = f"output is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})"
        return None, CheckResult(False, reason)


def _schema_problems(schema: Any, label: str) -> list[str]:
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return [f"{label} is not a valid JSON Schema: {exc.message}"]
    return []


def _schema_failures(schema: dict[str, Any], instance: Any) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(map(str, e.absolute_path)))
    messages = []
    for err in errors[:MAX_REPORTED_SCHEMA_ERRORS]:
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"{where}: {err.message}")
    if len(errors) > MAX_REPORTED_SCHEMA_ERRORS:
        messages.append(f"... and {len(errors) - MAX_REPORTED_SCHEMA_ERRORS} more")
    return messages


_MISSING = object()


def lookup_path(data: Any, path: str) -> Any:
    """Resolve a dotted path such as ``user.addresses.0.city``.

    Returns the sentinel ``_MISSING`` when any segment does not exist.
    """
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            if idx >= len(current):
                return _MISSING
            current = current[idx]
        else:
            return _MISSING
    return current


def _unknown_options(case: TestCase, allowed: set[str]) -> list[str]:
    extra = sorted(set(case.options) - allowed)
    if not extra:
        return []
    known = ", ".join(sorted(allowed)) or "none"
    return [f"unknown option(s) {', '.join(extra)} for type {case.type!r} (supported: {known})"]


# --- exact -------------------------------------------------------------------


def _validate_exact(case: TestCase) -> list[str]:
    problems = _unknown_options(case, {"strip", "ignore_case", "normalize_whitespace"})
    if not isinstance(case.expected, str):
        problems.append("'expected' must be a string for type 'exact'")
    return problems


def _normalize_text(text: str, options: dict[str, Any]) -> str:
    if options.get("normalize_whitespace", False):
        text = re.sub(r"\s+", " ", text)
    if options.get("strip", True):
        text = text.strip()
    if options.get("ignore_case", False):
        text = text.casefold()
    return text


def _eval_exact(case: TestCase, output: str) -> CheckResult:
    actual = _normalize_text(output, case.options)
    expected = _normalize_text(case.expected, case.options)
    if actual == expected:
        return CheckResult(True)
    position = next(
        (i for i, (a, b) in enumerate(zip(actual, expected, strict=False)) if a != b),
        min(len(actual), len(expected)),
    )
    return CheckResult(False, f"output differs from expected at character {position}")


# --- json --------------------------------------------------------------------


def _validate_json(case: TestCase) -> list[str]:
    problems = _unknown_options(case, set())
    if case.schema is not None:
        problems += _schema_problems(case.schema, "'schema'")
    return problems


def _eval_json(case: TestCase, output: str) -> CheckResult:
    data, failure = _parse_json(output)
    if failure:
        return failure
    if case.schema is not None:
        errors = _schema_failures(case.schema, data)
        if errors:
            return CheckResult(False, "schema validation failed: " + "; ".join(errors))
    return CheckResult(True)


# --- classification ----------------------------------------------------------


def _validate_classification(case: TestCase) -> list[str]:
    problems = _unknown_options(case, {"normalize"})
    if not isinstance(case.expected, str) or not case.expected.strip():
        problems.append("'expected' must be a non-empty label string for type 'classification'")
    elif case.labels is not None:
        norm = case.options.get("normalize", True)
        labels = {_label(lbl, norm) for lbl in case.labels}
        if _label(case.expected, norm) not in labels:
            problems.append(f"expected label {case.expected!r} is not in 'labels'")
    return problems


def _label(text: str, normalize: bool) -> str:
    return text.strip().casefold() if normalize else text


def _eval_classification(case: TestCase, output: str) -> CheckResult:
    normalize = case.options.get("normalize", True)
    predicted = _label(output, normalize)
    expected = _label(case.expected, normalize)
    details = {"predicted_label": predicted, "expected_label": expected}
    if case.labels is not None and predicted not in {_label(lbl, normalize) for lbl in case.labels}:
        return CheckResult(False, f"predicted label {predicted!r} is not an allowed label", details)
    if predicted != expected:
        return CheckResult(False, f"predicted {predicted!r}, expected {expected!r}", details)
    return CheckResult(True, None, details)


# --- required_fields ---------------------------------------------------------


def _validate_required_fields(case: TestCase) -> list[str]:
    problems = _unknown_options(case, set())
    if not case.required and not case.values:
        problems.append("type 'required_fields' needs 'required', 'values', or both")
    return problems


def _eval_required_fields(case: TestCase, output: str) -> CheckResult:
    data, failure = _parse_json(output)
    if failure:
        return failure
    missing = [p for p in case.required or [] if lookup_path(data, p) is _MISSING]
    mismatched = []
    for path, expected in (case.values or {}).items():
        actual = lookup_path(data, path)
        if actual is _MISSING:
            if path not in missing:  # a path may be listed in both `required` and `values`
                missing.append(path)
        elif actual != expected:
            mismatched.append(f"{path}={actual!r} (expected {expected!r})")
    reasons = []
    if missing:
        reasons.append("missing field(s): " + ", ".join(missing))
    if mismatched:
        reasons.append("wrong value(s): " + ", ".join(mismatched))
    if reasons:
        return CheckResult(False, "; ".join(reasons), {"missing": missing})
    return CheckResult(True)


# --- tool_call ---------------------------------------------------------------


def _validate_tool_call(case: TestCase) -> list[str]:
    problems = _unknown_options(case, set())
    exp = case.expected
    if not isinstance(exp, dict) or not isinstance(exp.get("name"), str):
        return problems + ["'expected' must be a mapping with a string 'name' for type 'tool_call'"]
    unknown = sorted(set(exp) - {"name", "arguments", "arguments_schema"})
    if unknown:
        problems.append(f"unknown key(s) in 'expected': {', '.join(unknown)}")
    if "arguments" in exp and not isinstance(exp["arguments"], dict):
        problems.append("'expected.arguments' must be a mapping")
    if "arguments_schema" in exp:
        problems += _schema_problems(exp["arguments_schema"], "'expected.arguments_schema'")
    return problems


def normalize_tool_call(data: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Accept common tool-call shapes and return ``{"name", "arguments"}``.

    Supported shapes:
      * ``{"name": ..., "arguments": {...}}``
      * ``{"function": {"name": ..., "arguments": "<json string>"}}``
      * ``{"tool_calls": [<one of the above>]}`` or a one-element list
    Arguments given as a JSON string are decoded.
    """
    if isinstance(data, dict) and "tool_calls" in data:
        data = data["tool_calls"]
    if isinstance(data, list):
        if len(data) != 1:
            return None, f"expected exactly one tool call, got {len(data)}"
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("function"), dict):
        data = data["function"]
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None, "output is not a tool call (no string 'name' field)"
    args = data.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as exc:
            return None, f"tool call arguments are not valid JSON: {exc.msg}"
    if not isinstance(args, dict):
        return None, "tool call arguments must be an object"
    return {"name": data["name"], "arguments": args}, None


def _eval_tool_call(case: TestCase, output: str) -> CheckResult:
    data, failure = _parse_json(output)
    if failure:
        return failure
    call, problem = normalize_tool_call(data)
    if call is None:
        return CheckResult(False, problem)
    details = {"tool_name": call["name"]}
    exp = case.expected
    if call["name"] != exp["name"]:
        return CheckResult(
            False, f"called tool {call['name']!r}, expected {exp['name']!r}", details
        )
    if "arguments" in exp and call["arguments"] != exp["arguments"]:
        got, want = call["arguments"], exp["arguments"]
        diffs = []
        for key in sorted(set(got) | set(want)):
            if key not in got:
                diffs.append(f"{key} missing")
            elif key not in want:
                diffs.append(f"{key} unexpected")
            elif got[key] != want[key]:
                diffs.append(f"{key}={got[key]!r} (expected {want[key]!r})")
        return CheckResult(False, "tool arguments differ: " + ", ".join(diffs), details)
    if "arguments_schema" in exp:
        errors = _schema_failures(exp["arguments_schema"], call["arguments"])
        if errors:
            return CheckResult(False, "tool arguments failed schema: " + "; ".join(errors), details)
    return CheckResult(True, None, details)


# --- custom ------------------------------------------------------------------


def _validate_custom(case: TestCase) -> list[str]:
    if not case.check:
        return ["type 'custom' requires 'check' naming a registered check"]
    if case.check not in _CUSTOM_CHECKS:
        available = ", ".join(registered_checks()) or "none registered"
        return [
            f"custom check {case.check!r} is not registered (available: {available}); "
            "load the module that defines it with --plugin"
        ]
    return []


def _eval_custom(case: TestCase, output: str) -> CheckResult:
    result = _CUSTOM_CHECKS[case.check](case, output)
    if isinstance(result, bool):
        return CheckResult(result, None if result else f"custom check {case.check!r} failed")
    if not isinstance(result, CheckResult):
        raise TypeError(
            f"custom check {case.check!r} returned {type(result).__name__}; expected CheckResult or bool"
        )
    return result


_VALIDATORS: dict[str, Callable[[TestCase], list[str]]] = {
    "exact": _validate_exact,
    "json": _validate_json,
    "classification": _validate_classification,
    "required_fields": _validate_required_fields,
    "tool_call": _validate_tool_call,
    "custom": _validate_custom,
}

_EVALUATORS: dict[str, Callable[[TestCase, str], CheckResult]] = {
    "exact": _eval_exact,
    "json": _eval_json,
    "classification": _eval_classification,
    "required_fields": _eval_required_fields,
    "tool_call": _eval_tool_call,
    "custom": _eval_custom,
}


def validate_case(case: TestCase) -> list[str]:
    """Return problems with a case definition (empty when valid)."""
    return _VALIDATORS[case.type](case)


def evaluate(case: TestCase, output: str) -> CheckResult:
    """Evaluate ``output`` against ``case``."""
    return _EVALUATORS[case.type](case, output)
