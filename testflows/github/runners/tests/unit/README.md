# Tests

Unit tests for the runner controller, written with the
[TestFlows](https://testflows.com) framework. They run fast and fully offline —
all cloud/SSH/GitHub I/O is mocked (`unittest.mock`), so no credentials or
network are needed.

## Running

Requires TestFlows: `pip install testflows`.

```bash
# from the repo root (or this directory) — runs the whole suite
python testflows/github/runners/tests/unit/regression.py

# run one feature (match by its @Name path)
python testflows/github/runners/tests/unit/regression.py --only "/runners/dedicated static provider/*"

# run one scenario
python testflows/github/runners/tests/unit/regression.py --only "/runners/scale_up helpers/get volume name*"
```

At the end you get a `Passing`/`Failing` summary with counts of features,
scenarios, and steps. Non-zero exit on failure, so it works in CI.

## Layout

- `regression.py` — entry point. Lists the features that make up the suite; a
  new feature must be added here to run.
- `features/` — one file per feature (a group of related test scenarios).
- `steps/` — shared helpers/fixtures used across features (e.g. building a
  mocked provider).

## TestFlows in 60 seconds

- A **`@TestScenario`** is one test (a function taking `self`). Use plain
  `assert` for checks. Optional `with Given(...) / When(...) / Then(...)`
  blocks add readable structure to the log.
- A **`@TestFeature`** groups scenarios. Most features here auto-discover their
  scenarios:

  ```python
  @TestFeature
  @Name("dedicated static provider")
  def feature(self):
      for scenario in loads(current_module(), Scenario):
          scenario()
  ```

  So to add a test you just write another `@TestScenario` in the file — no
  registration needed. (A couple of older features call scenarios explicitly;
  prefer discovery for new ones.)
- **`@Name(...)`** sets the path segment used by `--only`.

## Adding a test

1. Add a `@TestScenario` to the most relevant `features/*.py` file.
2. Mock at the boundary — patch the function that does I/O, not deep internals.
   Example: the dedicated-static provider's SSH calls are patched via
   `patch.object(provider_mod, "ssh", fake)`, where `fake` returns the remote
   command's **exit code** (that's what `ssh()` returns).
3. Run the feature with `--only` to check it, then the full suite.
4. New feature file? Add a `Feature(run=load(...))` line in `regression.py`.
