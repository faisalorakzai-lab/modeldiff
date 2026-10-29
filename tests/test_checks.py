from __future__ import annotations

import json

import pytest

from modeldiff.checks import CheckResult, evaluate, register_check

# --- exact -------------------------------------------------------------------


def test_exact_strips_whitespace_by_default(make_case):
    case = make_case(type="exact", expected="Hello")
    assert evaluate(case, "  Hello\n").passed


def test_exact_is_case_sensitive_by_default(make_case):
    result = evaluate(make_case(type="exact", expected="Hello"), "hello")
    assert not result.passed
    assert result.reason == "output differs from expected at character 0"


def test_exact_ignore_case_and_no_strip(make_case):
    case = make_case(type="exact", expected="Hello", options={"ignore_case": True, "strip": False})
    assert evaluate(case, "HELLO").passed
    assert not evaluate(case, "HELLO ").passed


def test_exact_normalize_whitespace_collapses_all_whitespace(make_case):
    case = make_case(type="exact", expected="Hello world", options={"normalize_whitespace": True})
    assert evaluate(case, "Hello\r\n  world\t").passed


def test_exact_whitespace_normalization_is_opt_in(make_case):
    case = make_case(type="exact", expected="Hello world")
    assert not evaluate(case, "Hello\nworld").passed


def test_exact_normalized_mismatch_reports_normalized_position(make_case):
    case = make_case(
        type="exact", expected="Hello brave world", options={"normalize_whitespace": True}
    )
    result = evaluate(case, "Hello\nworld")
    assert result.reason == "output differs from expected at character 6"


def test_exact_reports_position_for_prefix(make_case):
    result = evaluate(make_case(type="exact", expected="abc"), "abcdef")
    assert result.reason.endswith("character 3")


# --- json --------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["name", "age"],
    "properties": {"name": {"type": "string"}, "age": {"type": "integer", "minimum": 0}},
}


def test_json_without_schema_only_checks_parsing(make_case):
    case = make_case(type="json")
    assert evaluate(case, "[1, 2]").passed
    result = evaluate(case, "```json\n{}\n```")
    assert not result.passed and result.reason.startswith("output is not valid JSON")


def test_json_schema_pass_and_fail(make_case):
    case = make_case(type="json", schema=SCHEMA)
    assert evaluate(case, '{"name": "a", "age": 3}').passed
    result = evaluate(case, '{"name": 5, "age": -1}')
    assert not result.passed
    assert "age: -1 is less than the minimum of 0" in result.reason
    assert "name: 5 is not of type 'string'" in result.reason


def test_json_schema_missing_property(make_case):
    result = evaluate(make_case(type="json", schema=SCHEMA), '{"name": "a"}')
    assert "'age' is a required property" in result.reason


def test_json_schema_error_list_is_truncated(make_case):
    schema = {"type": "object", "properties": {k: {"type": "string"} for k in "abcdefg"}}
    output = json.dumps({k: 1 for k in "abcdefg"})
    result = evaluate(make_case(type="json", schema=schema), output)
    assert "... and 2 more" in result.reason


# --- classification ----------------------------------------------------------


def test_classification_normalizes_case_and_whitespace(make_case):
    case = make_case(type="classification", expected="Positive")
    result = evaluate(case, " positive\n")
    assert result.passed
    assert result.details == {"predicted_label": "positive", "expected_label": "positive"}


def test_classification_mismatch_records_labels(make_case):
    result = evaluate(make_case(type="classification", expected="billing"), "technical")
    assert not result.passed
    assert result.details["predicted_label"] == "technical"
    assert result.reason == "predicted 'technical', expected 'billing'"


def test_classification_rejects_label_outside_allowed_set(make_case):
    case = make_case(type="classification", expected="yes", labels=["yes", "no"])
    result = evaluate(case, "Yes, definitely")
    assert "is not an allowed label" in result.reason


def test_classification_without_normalize_is_exact(make_case):
    case = make_case(type="classification", expected="Yes", options={"normalize": False})
    assert not evaluate(case, "yes").passed


# --- required_fields ---------------------------------------------------------


def test_required_fields_nested_and_list_paths(make_case):
    case = make_case(type="required_fields", required=["user.name", "items.0.sku"])
    assert evaluate(case, '{"user": {"name": "a"}, "items": [{"sku": "x"}]}').passed
    result = evaluate(case, '{"user": {}, "items": []}')
    assert result.reason == "missing field(s): user.name, items.0.sku"


def test_required_fields_null_value_counts_as_present(make_case):
    case = make_case(type="required_fields", required=["a"])
    assert evaluate(case, '{"a": null}').passed


def test_required_fields_values(make_case):
    case = make_case(type="required_fields", values={"status": "ok", "count": 2})
    assert evaluate(case, '{"status": "ok", "count": 2}').passed
    result = evaluate(case, '{"status": "error", "count": 2}')
    assert result.reason == "wrong value(s): status='error' (expected 'ok')"


def test_required_fields_path_in_both_lists_reported_once(make_case):
    # Regression: a path in both `required` and `values` was listed twice.
    case = make_case(type="required_fields", required=["city"], values={"city": "Pune"})
    result = evaluate(case, "{}")
    assert result.reason == "missing field(s): city"


def test_required_fields_on_non_object_output(make_case):
    case = make_case(type="required_fields", required=["a"])
    assert evaluate(case, "[]").reason == "missing field(s): a"
    assert evaluate(case, "not json").reason.startswith("output is not valid JSON")


# --- tool_call ---------------------------------------------------------------


def test_tool_call_plain_shape(make_case):
    case = make_case(type="tool_call", expected={"name": "get", "arguments": {"id": "1"}})
    result = evaluate(case, '{"name": "get", "arguments": {"id": "1"}}')
    assert result.passed and result.details == {"tool_name": "get"}


def test_tool_call_function_shape_with_string_arguments(make_case):
    case = make_case(type="tool_call", expected={"name": "get", "arguments": {"id": "1"}})
    output = json.dumps({"function": {"name": "get", "arguments": json.dumps({"id": "1"})}})
    assert evaluate(case, output).passed


def test_tool_call_list_wrapper(make_case):
    case = make_case(type="tool_call", expected={"name": "get"})
    assert evaluate(case, '{"tool_calls": [{"name": "get", "arguments": {}}]}').passed
    result = evaluate(case, '[{"name": "get"}, {"name": "get"}]')
    assert result.reason == "expected exactly one tool call, got 2"


def test_tool_call_wrong_name(make_case):
    result = evaluate(
        make_case(type="tool_call", expected={"name": "refund"}), '{"name": "lookup"}'
    )
    assert result.reason == "called tool 'lookup', expected 'refund'"


def test_tool_call_argument_differences_are_listed(make_case):
    case = make_case(type="tool_call", expected={"name": "f", "arguments": {"id": "7", "x": 1}})
    result = evaluate(case, '{"name": "f", "arguments": {"id": 7, "y": 2}}')
    assert result.reason == "tool arguments differ: id=7 (expected '7'), x missing, y unexpected"


def test_tool_call_argument_schema(make_case):
    schema = {
        "type": "object",
        "required": ["amount"],
        "properties": {"amount": {"type": "number"}},
    }
    case = make_case(type="tool_call", expected={"name": "f", "arguments_schema": schema})
    assert evaluate(case, '{"name": "f", "arguments": {"amount": 5}}').passed
    result = evaluate(case, '{"name": "f", "arguments": {"amount": "5"}}')
    assert "tool arguments failed schema: amount: '5' is not of type 'number'" == result.reason


def test_tool_call_malformed_inputs(make_case):
    case = make_case(type="tool_call", expected={"name": "f"})
    assert "no string 'name'" in evaluate(case, '{"tool": "f"}').reason
    assert (
        "arguments are not valid JSON"
        in evaluate(case, '{"name": "f", "arguments": "{bad"}').reason
    )
    assert "must be an object" in evaluate(case, '{"name": "f", "arguments": [1]}').reason


# --- custom ------------------------------------------------------------------


def test_custom_check_returning_bool(make_case):
    @register_check("always_true")
    def always_true(case, output):
        return output == "ok"

    case = make_case(type="custom", check="always_true")
    assert evaluate(case, "ok").passed
    assert evaluate(case, "no").reason == "custom check 'always_true' failed"


def test_custom_check_receives_params(make_case):
    @register_check("max_words")
    def max_words(case, output):
        n = len(output.split())
        return CheckResult(n <= case.params["limit"], f"{n} words", {"words": n})

    case = make_case(type="custom", check="max_words", params={"limit": 2})
    assert evaluate(case, "one two").passed
    result = evaluate(case, "one two three")
    assert not result.passed and result.details == {"words": 3}


def test_custom_check_bad_return_type(make_case):
    @register_check("bad_return")
    def bad(case, output):
        return "yes"

    with pytest.raises(TypeError, match="expected CheckResult or bool"):
        evaluate(make_case(type="custom", check="bad_return"), "x")


def test_registering_a_different_function_twice_fails():
    @register_check("dup")
    def first(case, output):
        return True

    with pytest.raises(ValueError, match="already registered"):

        @register_check("dup")
        def second(case, output):
            return True
