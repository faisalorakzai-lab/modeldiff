# ModelDiff

**Regression testing for local AI.**

ModelDiff is a local-first testing framework for detecting behavioral regressions across AI model versions, inference configurations, prompt templates, and runtime changes. You describe test cases in a version-controlled YAML or JSON file, run them against a baseline and a candidate configuration, and get a report of what broke, what got fixed, and what merely changed.

No API keys are needed. The framework, its test suite, and the example workflow run fully offline. Running against a live model is optional and uses a local [Ollama](https://ollama.com) server.

## Why

Swapping a model version, quantization level, prompt template, inference runtime, or generation setting can silently break an application that depends on the model's output:

- JSON that used to parse now arrives wrapped in a Markdown code fence.
- A classifier starts returning a different label.
- A tool call passes `7730` where the schema expects `"7730"`.
- A required field disappears from a structured response.
- Responses get noticeably slower.

Ordinary unit tests do not cover these cases well, because the "code" that changed is a model. ModelDiff gives you repeatable test cases with explicit pass criteria, so a model or config change can be checked like any other change.

## Features

- **Test cases as data.** Suites in YAML or JSON, validated against a schema, with error messages that name the case and field at fault. Every problem in the file is reported at once.
- **Six check types.**
  | Type | Passes when |
  | --- | --- |
| `exact` | output equals `expected` (whitespace-stripped by default; optional case-insensitive and whitespace normalization) |
  | `json` | output parses as JSON and, if `schema` is given, validates against that JSON Schema |
  | `classification` | the predicted label equals `expected` and is one of `labels` (if given) |
  | `required_fields` | the JSON output contains every dotted path in `required` and matches `values` |
  | `tool_call` | the output is a tool call with the expected `name`, and its arguments equal `arguments` and/or validate against `arguments_schema` |
  | `custom` | a Python function you register returns a pass |
- **Baseline vs candidate comparison** that separates *regressions* (pass to fail) from *fixes* (fail to pass), cases still failing, *behavior changes* (passing in both runs but with different output), classification label changes, and cases missing from one side.
- **Reproducible result files** (`modeldiff.run/v1` JSON) recording the output or its hash, status, failure reason, latency, timestamp, runtime and model metadata, and errors. Keys that look like secrets are redacted.
- **Reports** in text, Markdown, or JSON, written to stdout or a file.
- **Latency comparison** with an opt-in threshold. Slowdowns are reported but do not fail the build unless you ask them to.
- **Optional process memory** measurement via `psutil`, clearly labelled as ModelDiff's own process.
- **Two providers.** `replay` serves recorded outputs and needs no model; `ollama` calls a local Ollama server using only the standard library.
- **CI-friendly CLI** with stable exit codes.

What ModelDiff does **not** do: it does not judge semantic correctness, factuality, or hallucination. A case passes or fails only according to its explicit criterion. When an output changes but the criterion is still met, ModelDiff reports a behavior change for you to review, not a regression.

## Installation

Requires Python 3.11 or newer.

```bash
git clone https://github.com/OpenWave-India/modeldiff.git
cd modeldiff
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

Optional extras:

```bash
pip install -e ".[memory]"   # per-case process memory via psutil
pip install -e ".[dev]"      # pytest and ruff for development
```

The package is not yet published to PyPI. When it is, the distribution name will be `openwave-modeldiff`, because the name `modeldiff` is already taken on PyPI by an unrelated project. The import name and the command are both `modeldiff`.

## Quick start

The repository includes an example suite for a customer-support assistant, with two sets of recorded outputs. These outputs are hand-written fixtures that illustrate typical regressions. They are not real model outputs.

```bash
# 1. Check the suite file. The example uses a custom check defined in checks.py.
modeldiff validate examples/quickstart/suite.yaml --plugin examples/quickstart/checks.py

# 2. Produce a baseline and a candidate result file.
modeldiff run examples/quickstart/suite.yaml \
  --config examples/quickstart/configs/baseline.yaml \
  --plugin examples/quickstart/checks.py \
  --output out/baseline.json

modeldiff run examples/quickstart/suite.yaml \
  --config examples/quickstart/configs/candidate.yaml \
  --plugin examples/quickstart/checks.py \
  --output out/candidate.json

# 3. Compare them.
modeldiff compare out/baseline.json out/candidate.json --latency-threshold 25 --min-latency-delta 100
```

Output of step 3 (exit code 1 because regressions were found):

```text
ModelDiff comparison: support-assistant
Baseline: baseline-q8    Candidate: candidate-q4

REGRESSIONS FOUND (4)  -  9 cases: 4 regressed, 1 fixed, 0 still failing, 4 passing in both, 3 passing with changed output

Regressions (4):
  ticket-json [json] pass -> fail: output is not valid JSON (line 1, column 1: Expecting value)
  ticket-required-fields [required_fields] pass -> fail: missing field(s): shipping.city
  intent-billing [classification] pass -> fail: predicted 'technical', expected 'billing'
  tool-lookup [tool_call] pass -> fail: tool arguments differ: order_id=7730 (expected '7730')

Fixed (1):
  sentiment-positive [classification] fail -> pass

Passing with changed output (review, not a regression) (3):
  sentiment-negative [classification] pass -> pass
  tool-refund [tool_call] pass -> pass
  summary-length [custom] pass -> pass

Classification changes (2):
  sentiment-positive: 'neutral' -> 'positive'
  intent-billing: 'billing' -> 'technical'

Latency (median over 9 cases): 420.0 ms -> 450.0 ms (+7.1%; baseline recorded, candidate recorded)
Slower than +25%:
  summary-length: 900.0 ms -> 1720.0 ms
```

For a Markdown report, for example to attach to a pull request:

```bash
modeldiff compare out/baseline.json out/candidate.json --format markdown --output report.md
```

```markdown
| Case | Type | Change | Output changed | Latency | Candidate reason |
| --- | --- | --- | --- | --- | --- |
| `ticket-json` | json | regression (pass -> fail) | yes | 820.0 ms -> 870.0 ms | output is not valid JSON (line 1, column 1: Expecting value) |
| `intent-billing` | classification | regression (pass -> fail) | yes (`billing` -> `technical`) | 148.0 ms -> 151.0 ms | predicted 'technical', expected 'billing' |
| `sentiment-positive` | classification | fixed (fail -> pass) | yes (`neutral` -> `positive`) | 155.0 ms -> 158.0 ms |  |
| `greeting-exact` | exact | unchanged pass (pass -> pass) | no | 310.0 ms -> 330.0 ms |  |
...
```

## Writing test cases

```yaml
suite: support-assistant          # required; both runs must use the same suite
description: Optional free text.

cases:
  - id: ticket-json               # required, unique; letters, digits, . _ -
    type: json                    # required; one of the six types
    input: "Extract the ticket as JSON ..."   # required; the prompt
    schema:                       # type-specific fields
      type: object
      required: [customer, priority]
    tags: [structured]            # optional; select with `run --tag`
    metadata: {owner: support}    # optional; free-form, not interpreted
    generation: {temperature: 0}  # optional; passed to the provider (Ollama options)
    performance:
      max_latency_ms: 1500        # optional; warns by default, see --enforce-performance
```

Fields used by each type:

| Type | Fields | Options |
| --- | --- | --- |
| `exact` | `expected` (string) | `strip` (default `true`), `ignore_case` (default `false`), `normalize_whitespace` (default `false`) |
| `json` | `schema` (JSON Schema, optional) | none |
| `classification` | `expected` (label), `labels` (optional allowed set) | `normalize` (default `true`: strip and case-fold) |
| `required_fields` | `required` (list of dotted paths), `values` (path: expected value) | none |
| `tool_call` | `expected.name`, `expected.arguments`, `expected.arguments_schema` | none |
| `custom` | `check` (registered name), `params` (any mapping) | none |

Dotted paths address nested objects and list items: `customer.name`, `items.0.sku`.

For `exact` checks, `normalize_whitespace: true` collapses every run of whitespace—including
newlines, tabs, and carriage returns—to one space before comparing. It is opt-in so that the
default remains strict about formatting:

```yaml
- id: greeting
  type: exact
  input: "Say hello"
  expected: "Hello world"
  options: {normalize_whitespace: true}
```

Mismatch positions are reported in the normalized text.

`tool_call` accepts these output shapes: `{"name", "arguments"}`, `{"function": {"name", "arguments"}}` (arguments may be a JSON string), and `{"tool_calls": [...]}` or a list containing exactly one call.

### Custom checks

```python
# my_checks.py
from modeldiff import CheckResult, register_check

@register_check("max_words")
def max_words(case, output):
    limit = case.params["limit"]
    words = len(output.split())
    if words > limit:
        return CheckResult(False, f"{words} words, limit is {limit}")
    return CheckResult(True)
```

```yaml
- id: summary-length
  type: custom
  check: max_words
  params: {limit: 25}
  input: "Summarize in under 25 words: ..."
```

Load the checks with `--plugin my_checks.py`, or with `--plugin package.module` for an importable module. A check can return a `bool` instead of a `CheckResult`. If a check raises an exception, the case is recorded with status `error` and the run continues.

## Run configurations

A config describes one side of a comparison.

```yaml
# Replay recorded outputs; no model needed.
id: baseline-q8
provider: replay
outputs: ../recorded/baseline.json   # relative to this config file
capture_output: full                 # full | hash | none
```

A recorded-output file maps case IDs to outputs. An entry can be a plain string or a mapping with `output` and optional `latency_ms` and `metadata`:

```json
{"outputs": {
  "greeting-exact": "Hello! How can I help you today?",
  "ticket-json": {"output": "{\"customer\": \"Priya\"}", "latency_ms": 820}
}}
```

When `latency_ms` is present, ModelDiff uses it and marks the duration as `recorded`. Otherwise it measures the call and marks it `measured`. Reports state which source each run used.

## Using a local model (optional)

ModelDiff can generate outputs with a local [Ollama](https://ollama.com) server. Nothing in the core workflow or test suite requires it.

```bash
ollama pull llama3.1:8b
ollama serve
```

```yaml
# examples/ollama/llama3.1-8b.yaml
id: llama3.1-8b
provider: ollama
model: llama3.1:8b
base_url: http://localhost:11434
options: {temperature: 0, seed: 42}   # Ollama generation options; case `generation` overrides
timeout_s: 120
# format: json                        # optional, passed to Ollama
# system: "You are ..."               # optional system prompt
```

```bash
modeldiff run my-suite.yaml --config examples/ollama/llama3.1-8b.yaml --output out/llama.json
```

The adapter calls `POST /api/generate` with `stream: false`. Token counts and durations reported by Ollama are stored in each case's `provider_metadata`. If the server cannot be reached, the case is recorded with status `error` and a message saying so.

To compare two quantizations, point two configs at two model tags, run both, and compare the result files. Set a fixed `seed` and `temperature: 0` to reduce run-to-run variation. Even so, some runtimes are not bit-for-bit deterministic, so run a config against itself to see how much noise to expect.

## CLI reference

```
modeldiff validate SUITE [--plugin P ...]
modeldiff run SUITE --config CFG --output OUT [--tag T ...] [--capture full|hash|none]
              [--plugin P ...] [--enforce-performance] [--fail-on-failure]
modeldiff compare BASELINE CANDIDATE [--format text|markdown|json] [--output FILE]
              [--latency-threshold PCT] [--min-latency-delta MS] [--fail-on-latency]
              [--allow-suite-mismatch] [--exit-zero]
modeldiff report RESULTS [--format text|markdown|json] [--output FILE]
```

| Exit code | Meaning |
| --- | --- |
| 0 | Success. For `compare`, no regressions. |
| 1 | `compare` found regressions (or flagged slowdowns with `--fail-on-latency`); `run --fail-on-failure` saw a failing or erroring case. |
| 2 | Invalid input: malformed suite, config, or result file, or bad arguments. |

`compare` refuses to compare runs of different suites. If the case definitions differ (the `suite_sha256` in the result files does not match), it also refuses unless you pass `--allow-suite-mismatch`. `python -m modeldiff` works the same as `modeldiff`.

### In CI

```yaml
- run: modeldiff run tests/llm/suite.yaml -c tests/llm/candidate.yaml -o out/candidate.json
- run: modeldiff compare tests/llm/baseline.json out/candidate.json --format markdown --output report.md
```

Commit a baseline result file and compare each candidate against it. Timing in shared CI runners is noisy, so latency is reported but does not fail the build unless you add `--fail-on-latency`.

## Result files and privacy

`run` writes a `modeldiff.run/v1` JSON file containing the suite name and hash, the config (with secret-looking keys such as `api_key`, `token`, `password` replaced by `<redacted>`), runtime metadata (ModelDiff and Python versions, OS name, provider details), and one record per case:

```json
{
  "case_id": "ticket-json",
  "type": "json",
  "status": "fail",
  "timestamp": "2026-10-29T03:53:20.887+00:00",
  "reason": "output is not valid JSON (line 1, column 1: Expecting value)",
  "output": "```json\n{\"customer\": \"Priya\", ...}\n```",
  "output_sha256": "243a96ab...",
  "duration_ms": 870.0,
  "duration_source": "recorded",
  "memory": {"process_rss_bytes": 33091584, "process_rss_delta_bytes": 0},
  "details": {},
  "warnings": [],
  "error": null,
  "provider_metadata": {},
  "tags": ["structured"]
}
```

Model outputs can contain personal or confidential data, especially when prompts are built from real records. Use `capture_output` to control what is stored:

| Mode | Stored | Change detection |
| --- | --- | --- |
| `full` (default) | output text and SHA-256 | yes |
| `hash` | SHA-256 only | yes (whether it changed, not how) |
| `none` | nothing | no; reported as unknown |

Pass/fail is always computed from the full output in memory, whatever the capture mode. Note that prompts (`input`) live in your suite file, and failure reasons can quote fragments of output, for example a wrong label or argument value. Review result files before you commit or share them.

## Measurements

| Metric | Measured | Notes |
| --- | --- | --- |
| Latency | Wall-clock time around the provider call (`time.perf_counter`), or the recorded value from a replay file | Includes HTTP overhead for Ollama. |
| Process memory | RSS of the ModelDiff process before and after each case, when `psutil` is installed | Does **not** include the Ollama server process, the model weights, or GPU memory. |
| Token counts and server timings | Whatever Ollama reports | Stored as-is in `provider_metadata`, not compared. |

When `psutil` is not installed, result files list `memory` under `unavailable_metrics`.

## Architecture

```
src/modeldiff/
  cases.py      load and validate suite files (YAML/JSON + JSON Schema)
  checks.py     the six check types and the custom check registry
  config.py     run config files, secret redaction
  providers.py  replay and Ollama providers
  runner.py     run a suite with a provider; timing and memory
  results.py    result records and the modeldiff.run/v1 file format
  compare.py    baseline vs candidate comparison
  report.py     text / Markdown / JSON rendering
  cli.py        argparse CLI
```

Data flows in one direction: suite + config → runner → result file → compare → report. Each stage reads and writes plain data, so you can produce result files with your own tooling and still use `compare` and `report`. See [docs/architecture.md](docs/architecture.md) for design decisions and dependency choices.

## Development

```bash
pip install -e ".[dev,memory]"
pytest
ruff check src tests examples
ruff format --check src tests examples
```

The test suite uses fixtures and mocks only. It does not download models or make network calls.

## Limitations

- No semantic or similarity scoring. A changed output that still meets its criterion is reported for review, not judged.
- One live provider (Ollama, `/api/generate`, single-turn prompts). Chat-style message lists and native tool-call responses are not sent or parsed from the provider. `tool_call` checks evaluate the output text.
- Cases run sequentially. There is no parallelism and no repeated sampling to estimate variance.
- Memory measurement covers the ModelDiff process only.
- Latency thresholds compare single measurements per case and are sensitive to noise.

## Roadmap

These are ideas, not commitments. See the issue tracker for what is being worked on.

- Repeated runs per case, with variance reported, to separate noise from change.
- A chat/messages input format and a provider for OpenAI-compatible local servers (llama.cpp, vLLM, LM Studio).
- JUnit XML output for CI systems.
- A JSON Schema for result files, published alongside the code.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and look for issues labelled `good first issue`. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md). To report security issues, see [SECURITY.md](SECURITY.md).

## License

ModelDiff is released under the [MIT License](LICENSE). Contributions are accepted under the same license.
