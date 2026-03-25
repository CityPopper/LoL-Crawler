# Sequential TDD Workflow

Standard Red → Green → Refactor handoff between the `tester` and `developer` agents.

Use when:
- Creating a new module or feature with no existing implementation
- Filling coverage gaps on existing code
- The expected behavior has a single unambiguous interpretation

Do NOT use when the function already exists and has 3+ distinct test scenarios — use the Parallel TDD Pattern (`docs/patterns/parallel-tdd-pattern.md`) instead.

---

## Steps

### 1. Red — Tester writes failing tests

Spawn the `tester` agent. Give it:
- The source file(s) to test (or a description of the behavior if the file doesn't exist yet)
- The relevant `tests/conftest.py` for available fixtures
- `TODO.md` for the specific test cases to write

The tester:
1. Reads the source file and existing test patterns
2. Writes tests that fail for the right reason (not `ImportError` — the code should be importable but wrong)
3. Runs each test, confirms it fails
4. Returns test files + failure output

**The tester does not write implementation code. The test is the spec — do not modify it.**

### 2. Green — Developer implements

Spawn the `developer` agent. Give it:
- The failing test files returned by the tester
- The source file(s) to implement
- Instruction: implement the minimum code to make the tests pass

The developer:
1. Reads the test files to understand the spec
2. Implements the minimum code to pass (no extra features)
3. Runs the tests, confirms they pass
4. Returns the implementation file(s)

### 3. Refactor — Developer cleans up

The developer refactors the implementation while keeping all tests green.

The tester does **not** participate in this phase. No new tests are written unless refactoring reveals untested edge cases — which gets a new Red→Green cycle.

### 4. Self-Review — Developer checks against Code Review checklist

Before returning results, the developer applies the Code Review checklist from its agent file (`## Code Review` section). For each category (Correctness, Security, Standards, Tests), verify the implementation and fix any violations. Run `just lint` and `just typecheck` and resolve all issues — do not just report them.

### 5. Verify

Run the full test suite for the affected service:
```bash
just test-service {service}
just lint
just typecheck
```

All must pass before the task is complete.

---

## Rules

- Never modify a failing test without user confirmation — the test is the spec
- Never change contracts to match broken output
- If behavior is ambiguous, ask before the tester writes tests — not after
- Max attempts: if tests still fail after 2 developer rounds, fall back to user consultation

## Related

- Parallel variant: `docs/patterns/parallel-tdd-pattern.md`
- After implementation: `docs/workflows/review-cycle.md`
- Doc sync: `docs/workflows/doc-bookend.md`
