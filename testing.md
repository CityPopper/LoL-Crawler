# Software Testing Best Practices (2025-2026)

Research compiled March 2026. Focused on actionable findings with broad consensus.

---

## 1. Testing Pyramid and Its Modern Alternatives

### The Classic Pyramid

The traditional testing pyramid (Mike Cohn, 2009) suggests roughly **70% unit tests, 20% integration tests, 10% E2E tests**. The rationale: unit tests are fast, cheap, and isolate failures. Integration tests validate component interactions. E2E tests are slow, expensive, and brittle.

The most common mistake is **inverting the pyramid** into the "ice cream cone" anti-pattern: many slow E2E tests, few fast unit tests. This leads to slow CI, flaky pipelines, and difficult debugging.

Source: [Devzery - Software Testing Pyramid Guide 2025](https://www.devzery.com/post/software-testing-pyramid-guide-2025)

### The Testing Trophy (Kent C. Dodds)

Kent C. Dodds proposed the Testing Trophy as an alternative: **static analysis at the base, then unit tests, then mostly integration tests, then minimal E2E tests**. The guiding principle: "The more your tests resemble the way your software is used, the more confidence they can give you."

The Trophy argues that integration tests offer the best **confidence-to-cost ratio**. Unit tests are cheap but do not confirm components work together. E2E tests give maximum confidence but are slow and expensive to maintain.

The famous one-liner summary: **"Write tests. Not too many. Mostly integration."**

Sources:
- [Kent C. Dodds - Write Tests](https://kentcdodds.com/blog/write-tests)
- [Kent C. Dodds - The Testing Trophy](https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications)

### The Testing Honeycomb (Spotify)

Spotify's Testing Honeycomb applies specifically to microservices: **most testing effort goes to integration tests**, with a thin layer of unit tests and minimal E2E tests. The rationale: in microservices, the most significant complexity is not within the service itself, but in how it interacts with others.

Source: [Spotify Engineering - Testing of Microservices](https://engineering.atspotify.com/2018/01/testing-of-microservices)

### Martin Fowler's Conclusion

Martin Fowler concluded that the percentage debate is a **distraction**. The terms "unit test" and "integration test" are poorly defined across the industry, and people using different shape models often describe the same practices under different labels. The real priority: write tests that are **expressive, reliable, have clear boundaries, and only fail for useful reasons**. Nearly zero teams achieve this baseline, making it more important than debating ratios.

Source: [Martin Fowler - On the Diverse And Fantastical Shapes of Testing](https://martinfowler.com/articles/2021-test-shapes.html)

### Actionable Takeaway

Do not dogmatically follow any single shape. **Match test distribution to your architecture**:
- Logic-heavy code (parsers, validators, algorithms): more unit tests
- Integration-heavy code (services calling other services, databases): more integration tests
- UI-heavy code: more E2E/component tests

The universal consensus: **every project needs tests at multiple levels**, and the exact ratio depends on where the risk lives.

Source: [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)

---

## 2. Consumer-Driven Contract Testing (CDCT)

### What Pact Does

Pact is a code-first consumer-driven contract testing tool. The consumer writes tests describing expected interactions, which generate a contract (Pact file). The provider verifies it can satisfy those contracts. Only the parts of communication actually used by consumers get tested, meaning any provider behavior not used by current consumers is free to change without breaking tests.

Pact v3 introduced message contracts (for queues/streams). Pact v4 evolved message testing further. Pact abstracts away the protocol and specific queuing technology, focusing on the message shapes passing between services.

Sources:
- [Pact Docs - Introduction](https://docs.pact.io/)
- [Pact Docs - Event Driven Systems](https://docs.pact.io/implementation_guides/javascript/docs/messages)

### Best Practices

- **Use loose matchers**: Match on data types, not exact values. If you need to match exact field contents, the API should probably be providing that field separately.
- **Provider states must be reproducible and idempotent**: No complex mutations or reliance on external data sources.
- **Each test should exist to detect a breaking change**: If removing a test means no type of provider change could break the consumer unnoticed, the test is unnecessary.
- **Publish to a Pact Broker with proper versioning**: Use `can-i-deploy` to gate deployments.
- **Integrate into CI/CD**: Contract verification should block merges, not be an afterthought.

Sources:
- [Sachith - Contract Testing with Pact Best Practices 2025](https://www.sachith.co.uk/contract-testing-with-pact-best-practices-in-2025-practical-guide-feb-10-2026/)
- [Pact Docs - Consumer Tests](https://docs.pact.io/consumer)

### When CDCT Is Essential

- Multiple teams independently deploying services that communicate via APIs or messages
- Breaking changes in a provider would be invisible until production (no shared deploy)
- Services evolve at different speeds and cannot be tested together in a staging environment

### When CDCT Is Overkill

- **Monorepo with a single team**: You control both sides of every contract. Schema validation (e.g., Pydantic models) plus integration tests may be sufficient.
- **Single-process applications**: No service boundary to test across.
- **Very early prototyping**: Contracts add overhead when interfaces are still fluid.
- **Code access required on both sides**: Pact needs the ability to write unit tests on the consumer side and manipulate state on the provider side. If you cannot do this (e.g., third-party APIs), Pact is not the right tool.

Pact adds operational complexity (broker infrastructure, contract versioning, team coordination). For a monorepo with shared schema definitions (like this project's `lol-pipeline-common/contracts/schemas/`), the schemas themselves can serve as the contract, with provider contract tests verifying compliance.

Sources:
- [Craig Risi - Pros and Cons of Pact](https://www.craigrisi.com/post/the-pros-and-cons-of-using-pact-for-contract-testing)
- [Pact Docs - Contract Tests vs Functional Tests](https://docs.pact.io/consumer/contract_tests_not_functional_tests)
- [Microsoft ISE - PACT Contract Testing](https://devblogs.microsoft.com/ise/pact-contract-testing-because-not-everything-needs-full-integration-tests/)

---

## 3. TDD with AI Agents

### The 2026 Agile Workshop Consensus

25 years after the Agile Manifesto, a workshop of experts concluded: **"Test-driven development produces dramatically better results from AI coding agents."** The key insight: "When the tests exist before the code, agents cannot cheat by writing a test that simply confirms whatever incorrect implementation they produced."

Source: [The Register - TDD Ideal for AI](https://www.theregister.com/2026/02/20/from_agile_to_ai_anniversary/)

### Tweag Agentic Coding Handbook

Tweag's handbook (from their controlled experiment where AI-assisted teams delivered projects 45% faster) frames TDD and agentic coding as complementary: **"TDD gives structure to your flow, and Agentic coding gives speed to your structure."**

Key workflow recommendations:
1. **Tests act as prompts** -- a test is a natural language spec that guides the AI toward exact behavior
2. **Use descriptive test names** -- clearer tests produce better AI output
3. **Keep test scopes tight** -- one behavior per prompt
4. **Let the AI refactor** -- ask it to "clean up the logic but keep all tests green"
5. **Deploy pre-commit hooks** -- prevent invalid code from merging

TDD with AI reduces hallucination because precise prompts (tests) lead to more accurate generation.

Source: [Tweag - Agentic Coding Handbook TDD](https://tweag.github.io/agentic-coding-handbook/WORKFLOW_TDD/)

### Simon Willison's Red/Green Pattern

Simon Willison identifies two risks with AI coding agents that TDD mitigates: (1) generated code that does not work, and (2) unnecessary code that never gets used. Red/green TDD addresses both.

The discipline requires two phases:
- **Red phase**: Write tests first, confirm they fail
- **Green phase**: Implement until tests pass

**Skipping the red phase is the most common mistake.** Without confirming failure first, you risk tests that already pass, defeating the purpose.

Source: [Simon Willison - Red/Green TDD](https://simonwillison.net/guides/agentic-engineering-patterns/red-green-tdd/)

### Codemanship: Why TDD Works for AI

Jason Gorman explains that TDD's micro-iterative process (one feature, one scenario, one example at a time) prevents the chaos that emerges when AI generates large amounts of code at once. Developers report that asking an LLM to generate entire applications produces inconsistency and duplication -- "like 10 devs worked on it without talking to each other."

TDD's rapid feedback loops (continuous testing, code review, refactoring, coupled with CI) minimize the risks of having an AI write code. Micro-iterative practices are not just compatible with AI-assisted development -- **they are essential**.

Source: [Codemanship - Why TDD Works Well for AI](https://codemanship.wordpress.com/2026/01/09/why-does-test-driven-development-work-so-well-in-ai-assisted-programming/)

### TDAD Paper (March 2026)

The TDAD paper (arxiv 2603.17973) combines AST-based code-test dependency graphs with impact analysis to identify which tests an AI agent's proposed change affects.

Key findings:
- **GraphRAG+TDD reduced test regressions by 70%** (6.08% to 1.82%)
- **Surprising result**: TDD prompting alone *increased* regressions (9.94%) in smaller models. Agents benefit more from knowing **which tests to check** than from instructions on **how to do TDD**.
- An autonomous auto-improvement loop raised resolution from 12% to 60% with 0% regression on a subset.

Source: [arxiv - TDAD](https://arxiv.org/abs/2603.17973)

### TDFlow Paper (October 2025)

TDFlow frames repository-scale software engineering as a test-resolution task. When provided human-written tests, it achieves **88.8% pass rate on SWE-Bench Lite** and **94.3% on SWE-Bench Verified**. The primary obstacle to human-level AI software engineering is **writing accurate reproduction tests**, not solving them.

Source: [arxiv - TDFlow](https://arxiv.org/abs/2510.23761)

---

## 4. Property-Based Testing

### What It Is

Property-based testing (PBT) defines general expectations about code behavior and lets the framework generate hundreds of random inputs. Hypothesis is the standard Python library for this. When it finds a failure, it automatically **shrinks** the input to the smallest example that still fails, making debugging easier.

Source: [Hypothesis Documentation](https://hypothesis.readthedocs.io/)

### When to Use It

PBT excels in specific scenarios:

- **Serialization/deserialization roundtrips**: `decode(encode(x)) == x` for any valid `x`. This is one of the most important properties to test.
- **Parsers and data transformers**: Functions that accept complex inputs and must handle edge cases (empty strings, Unicode, negative numbers, boundary values).
- **Inverse operations**: Any pair of functions where one should undo the other.
- **Idempotent operations**: `f(f(x)) == f(x)` -- common in data pipelines and caching.
- **Stateful systems**: Verifying no sequence of valid operations puts an object into an invalid state (Hypothesis `RuleBasedStateMachine`).
- **Complex business logic**: Conditional logic with multiple paths where the developer cannot enumerate all cases.

Sources:
- [Hypothesis - What is Property Based Testing](https://hypothesis.works/articles/what-is-property-based-testing/)
- [Hypothesis - Canonical Serialization](https://hypothesis.works/articles/canonical-serialization/)
- [Semaphore - Property-Based Testing Python](https://semaphore.io/blog/property-based-testing-python-hypothesis-pytest)

### When NOT to Use It

- **Trivial code**: If unit tests cover it sufficiently, PBT adds noise and slowness.
- **Slow functions**: PBT runs 100+ iterations by default. If each call is slow (API calls, rendering, heavy computation), tests become prohibitively slow.
- **Hard-to-define properties**: If you cannot articulate an invariant, PBT is the wrong tool. Forcing an artificial property is worse than writing good example-based tests.
- **Every single function**: Overuse is the most common mistake. PBT is a precision tool for code where edge cases matter, not a replacement for example-based tests.

The consensus: **use PBT alongside example-based tests**, not as a replacement. Example-based tests document specific known behaviors. PBT explores the unknown.

Sources:
- [Nurkiewicz - Property-Based Testing](https://nurkiewicz.com/2021/09/property-based-testing.html)
- [fast-check - Why Property-Based Testing](https://fast-check.dev/docs/introduction/why-property-based/)

---

## 5. Test Anti-Patterns

### Testing Implementation Details

Tests that verify **how** code works rather than **what** it produces. When you refactor the internals without changing behavior, these tests break. This is the single most common testing anti-pattern.

**Fix**: Test observable behavior (return values, side effects, state changes). Ask: "If I refactor the implementation but keep the same behavior, would this test break?" If yes, the test is coupled to implementation.

Sources:
- [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)
- [Java67 - Don't Write Brittle Unit Tests](https://www.java67.com/2025/11/dont-write-brittle-unit-tests-focus-on.html)

### Over-Mocking ("The Mockery")

A test contains so many mocks, stubs, and fakes that the system under test is not being tested at all. The data returned from mocks is what is being tested.

**Google's guidance** (from "Software Engineering at Google"): Prefer real implementations first. Use mocks only when real implementations are slow, nondeterministic, or have complex dependencies. **When you must use test doubles, prefer fakes** (lightweight implementations with real behavior) over mocking frameworks.

The preference hierarchy: **Real implementations > Fakes > Stubs > Interaction testing (mocks)**.

Sources:
- [Google SWE Book - Test Doubles](https://abseil.io/resources/swe-book/html/ch13.html)
- [DZone - Unit Testing Anti-Patterns](https://dzone.com/articles/unit-testing-anti-patterns-full-list)

### Don't Mock What You Don't Own

Mocking third-party libraries is dangerous because you are asserting on behavior you do not control and cannot guarantee. If the library changes, your mocked tests still pass while production breaks.

**Fix**: Wrap third-party dependencies in your own thin adapter. Test the adapter with integration tests against the real library. Mock your adapter in unit tests.

Source: [testdouble - Don't Mock What You Don't Own](https://github.com/testdouble/contributing-tests/wiki/Don't-mock-what-you-don't-own)

### Testing Private Methods

Private methods are implementation details. Making them public to test them is a design smell. If a private method is hard to test indirectly through the public API, it either contains dead code or needs to be extracted into its own class/module.

Source: [Enterprise Craftsmanship - Structural Inspection](https://enterprisecraftsmanship.com/posts/structural-inspection/)

### Flaky Tests

Even a small number of flaky tests destroys the credibility of the entire test suite. If you have 5 flaky tests and get 3 failures, it is impossible to tell if the failures are real regressions or the known-flaky tests. **Quarantine flaky tests** in a separate suite. Fix or delete them. Never let them pollute the reliable suite.

Source: [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)

### Excessive Setup ("The Giant")

Tests requiring hundreds of lines of setup to exercise one behavior. The noise of setup obscures what is being tested. Fix: use factories, builders, fixtures, and test helpers to reduce setup to the essentials.

Source: [Codurance - TDD Anti-Patterns Chapter 2](https://www.codurance.com/publications/tdd-anti-patterns-chapter-2)

### The Liar

A test that passes but does not actually test the intended behavior. It exists, it runs, it passes -- but it validates nothing meaningful. Often caused by assertions on mock return values or missing assertions entirely.

Source: [Digital Tapestry - Anti-Patterns](https://digitaltapestry.net/testify/manual/AntiPatterns.html)

### Treating TDD as a Religion

Blindly following TDD regardless of context. There are situations where writing tests after implementation is more appropriate (exploratory prototyping, one-off scripts, learning spikes). TDD is a tool, not a moral obligation.

Source: [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)

---

## 6. What NOT to Test

### Testing the Framework

Do not write tests that verify your framework works. If you are testing that Redis `SET` and `GET` work, that pytest `parametrize` works, or that FastAPI routes correctly, you are testing someone else's code. Write integration tests for **your** code that uses the framework, not the framework itself.

Source: [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)

### Trivial Code

Getters, setters, simple property accessors, constructors that just assign fields. If there is no logic, there is no bug. Testing these adds maintenance cost with zero value.

Source: [DZone - Unit Testing Anti-Patterns](https://dzone.com/articles/unit-testing-anti-patterns-full-list)

### Chasing 100% Coverage

Coverage beyond ~80% yields sharply diminishing returns. The last 20% typically consists of error handling paths, defensive checks, and edge cases that are expensive to reach in tests and rarely break. **Unit tests alone find only 15-50% of defects** (averaging 30%). Time beyond the 80% mark is often better spent on exploratory testing, code review, stress testing, or fuzzing.

Sources:
- [Nicola Lindgren - Economics of Software Testing](https://nicolalindgren.com/the-economics-of-software-testing-the-law-of-diminishing-returns/)
- [NDepend - Should You Aim for 100% Coverage](https://blog.ndepend.com/aim-100-percent-test-coverage/)

### Testing the Wrong Functionality

Focus testing effort on **critical business logic** where bugs cause the most damage. Do not spend equal effort testing trivial CRUD operations and complex pricing engines. Not all code has equal risk.

Source: [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)

### Configuration Boilerplate

Tests that verify environment variable loading, config file parsing, or Docker Compose service definitions. These are better validated by a single integration smoke test than by unit tests.

---

## 7. Redis Testing Patterns

### Fakeredis (Unit/Fast Tests)

Fakeredis is a pure-Python Redis implementation for testing without a running server. It supports the full Redis API including Streams, Lua scripting (via `lupa`), RedisJSON, RedisBloom, and GeoCommands.

Install with Lua support: `pip install fakeredis[lua]`.

**When to use**: Fast unit tests, CI without Docker, testing Redis command sequences in isolation.

**Limitation**: It is an approximation. Behavior may diverge from real Redis in subtle ways, especially for newer commands or edge cases in Lua scripting.

Sources:
- [Fakeredis Documentation](https://fakeredis.readthedocs.io/)
- [Fakeredis PyPI](https://pypi.org/project/fakeredis/)

### Testcontainers (Integration Tests)

Testcontainers spins up real Redis in Docker during test runs. This is the gold standard for integration testing because it exercises the real Redis engine.

Best practices:
- **Session-scoped container, function-scoped client**: Start Redis once per test session for performance. Create a fresh client per test and `FLUSHALL` after each test for isolation.
- **Pin the Docker image version**: Do not use `latest`. Use the same version as production.
- **Use `get_exposed_port(6379)`** for dynamic port mapping.
- **CI compatibility**: Works on GitHub Actions Ubuntu runners (Docker available by default).

Source: [Docker - Testcontainers Best Practices](https://www.docker.com/blog/testcontainers-best-practices/)

### Lua Script Testing

Test Lua scripts in three ways:

1. **Fakeredis with Lua**: Fast, no Docker. Good for unit testing script logic. Install `fakeredis[lua]` and run `EVAL` directly.
2. **Testcontainers with real Redis**: The definitive test. Run the exact Lua script against real Redis to catch any behavioral differences.
3. **Dual-run pattern**: Run all Redis tests twice -- once against fakeredis, once against testcontainers. This catches both regressions in your code and inaccuracies in the mock.

Avoid dynamically generating Lua scripts. Keep scripts generic and parameterize via `KEYS` and `ARGV`. Dynamic script generation is a Redis anti-pattern that exhausts the script cache.

Sources:
- [Fakeredis - Lua Scripting](https://fakeredis.readthedocs.io/)
- [Redis Docs - Scripting with Lua](https://redis.io/docs/latest/develop/programmability/eval-intro/)
- [Gitter - Unit Testing Redis Lua Scripts](https://blog.gitter.im/2015/01/13/testing-redis-lua-scripts/)

---

## 8. Async Testing Patterns

### pytest-asyncio Configuration

**Auto mode** is recommended for projects that only use asyncio. It automatically marks all `async def` test functions and takes ownership of all async fixtures without explicit decorators. Configure in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Strict mode** (the default if unconfigured) requires explicit `@pytest.mark.asyncio` on every async test. Use this only if you support multiple async frameworks (asyncio + trio).

Sources:
- [pytest-asyncio - Auto Mode](https://pytest-asyncio.readthedocs.io/en/latest/concepts.html)
- [pytest-asyncio - Configuration](https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html)

### Key Pitfalls

- **Never call blocking functions inside async tests**: `requests.get()` inside `async def` will hang the event loop. Use `httpx.AsyncClient` or similar.
- **Event loop scope issues**: Session-scoped async fixtures can receive different event loop objects than tests. Pin the loop scope explicitly and test for this in CI.
- **Resource cleanup**: Always use `async with` or `try/finally` in async fixtures. Leaked connections and unclosed clients are the primary source of flaky async tests.
- **Timeouts**: Always set timeouts on async operations in tests. An `await` that never resolves will hang the test suite indefinitely. Use `asyncio.wait_for()` or `pytest.mark.timeout`.

Sources:
- [Tony Baloney - Async Test Patterns for Pytest](https://tonybaloney.github.io/posts/async-test-patterns-for-pytest-and-unittest.html)
- [Pytest with Eric - pytest-asyncio Guide](https://pytest-with-eric.com/pytest-advanced/pytest-asyncio/)

### Testing Event-Driven Systems

For Redis Streams-based pipelines:
- **Produce a message, consume it, assert on the output**: The fundamental integration test pattern. Produce to a stream, let the consumer process it, verify the downstream effect (Redis state, output stream message).
- **Verify PEL behavior**: Produce a message, let the consumer crash mid-processing, verify the message remains in the pending entry list (PEL) and is redelivered.
- **Test XAUTOCLAIM**: Produce a message, let it idle beyond the claim timeout, verify another consumer picks it up.
- **Assert on XACK**: After successful processing, verify the message is no longer in the PEL.
- **Use `XLEN` and `XPENDING`** to verify stream and consumer group state in assertions.

Source: [Redis - Microservices Interservice Communication](https://redis.io/tutorials/howtos/solutions/microservices/interservice-communication/)

---

## 9. Microservice Testing

### The Layered Strategy

For a pipeline of services connected by streams/queues:

1. **Unit tests**: Test each service's business logic in isolation (parsing, transformation, validation). Mock the stream client. Fast, no infrastructure.
2. **Contract tests**: Verify message schemas between producer and consumer. In a monorepo, shared schema definitions + validation tests serve this purpose.
3. **Integration tests (per-service)**: Spin up real Redis (testcontainers), produce input messages, run the service, verify output messages and Redis state. Each service tested independently.
4. **Pipeline integration tests**: Spin up multiple services and Redis, produce a seed message, verify it flows through the entire pipeline producing the correct final state. These are expensive -- keep them to 5-10 critical paths.
5. **E2E smoke tests**: Run the full Docker Compose stack, trigger a real workflow, verify the happy path completes. One or two of these is sufficient.

Sources:
- [Bunnyshell - E2E Testing for Microservices 2026](https://www.bunnyshell.com/blog/end-to-end-testing-for-microservices-a-2025-guide/)
- [Martin Fowler - Testing Strategies in a Microservice Architecture](https://martinfowler.com/articles/microservice-testing/)

### Test Data Management

Each service should manage its own test data. Avoid coupling through shared test databases. Implement proper data cleanup between test runs. For Redis: `FLUSHALL` between tests, or use key-prefix isolation.

Source: [Testkube - Microservices Testing Strategies](https://testkube.io/blog/cloud-native-microservices-testing-strategies)

### Contract Testing in a Monorepo

When all services live in one repo with shared schema definitions, full Pact broker infrastructure is likely overkill. Instead:
- Keep canonical schemas in a shared location (e.g., `contracts/schemas/`)
- Write provider contract tests that validate each service produces messages matching the schema
- Write consumer contract tests that validate each service can parse messages in the schema format
- Run these as unit tests in CI -- no broker needed

Source: [Pact Docs - How Pact Works](https://docs.pact.io/getting_started/how_pact_works)

---

## 10. Test Maintenance Cost

### The Hidden Cost

Test maintenance can consume **30-50% of the total test automation budget**. As test suites grow, the time required to update, debug, and refine scripts grows proportionally. Enterprise environments commonly swell to 20,000-30,000 test cases, many of which are redundant.

Source: [Katalon - Hidden Costs of Test Maintenance](https://katalon.com/resources-center/blog/the-hidden-costs-of-test-maintenance)

### Signs of Over-Testing

- **Longer testing cycles**: Test suite takes so long that developers stop running it locally.
- **Frequent test breakage on refactors**: Tests break when behavior has not changed -- they are coupled to implementation.
- **Redundant tests**: Multiple tests covering the same path with no additional confidence.
- **Low signal-to-noise ratio**: A failure requires investigation to determine if it is a real regression or a flaky/outdated test.
- **Strategic time loss**: Maintenance consumes time that should be spent on exploratory testing or improving coverage of critical areas.

Sources:
- [Functionize - Hidden Costs of Traditional Test Automation](https://www.functionize.com/blog/test-management-count-is-inflated-hidden-costs-of-traditional-test-automation)
- [Katalon - Hidden Costs of Test Maintenance](https://katalon.com/resources-center/blog/the-hidden-costs-of-test-maintenance)

### When Tests Become a Burden

Tests hurt when:
1. **A refactor requires updating more test code than production code.** This means tests are coupled to implementation, not behavior.
2. **The test suite is slower than the deployment pipeline.** Tests should accelerate delivery, not block it.
3. **Developers disable or skip tests rather than fix them.** The suite has lost credibility.
4. **Every new feature requires modifying dozens of existing tests.** Coupling is too high.
5. **Test failures do not correlate with real bugs.** False positives erode trust.

### Mitigation Strategies

- **Delete tests that provide no value**: If a test has never caught a bug and tests trivial code, remove it.
- **Quarantine flaky tests**: Move them to a separate suite. Fix or delete within a sprint.
- **Maintain a "reliable suite"**: Even if it is a subset, this suite must be rock solid. A failure in this suite means something is genuinely wrong.
- **Convert production bugs to tests**: When a bug escapes to production, write a test that would have caught it. This is the highest-value test you can write.
- **Treat test code as first-class code**: Apply the same design rigor (DRY, clear naming, no duplication) to tests as to production code.
- **Review tests in code review**: If a test is hard to understand, it is hard to maintain. Readability matters.

Sources:
- [Codepipes - Software Testing Anti-patterns](https://blog.codepipes.com/testing/software-testing-antipatterns.html)
- [Testim - Software Testing Cost Guide](https://www.testim.io/blog/leaders-guide-how-to-rationalize-software-testing-cost/)

---

## Summary of Consensus Positions

| Topic | Consensus |
|-------|-----------|
| Test distribution | Match to architecture, not a fixed ratio |
| Integration tests | Broadly agreed to offer the best confidence/cost ratio |
| TDD with AI agents | Strong consensus that it produces better results than unconstrained generation |
| Red/green discipline | Essential -- never skip confirming tests fail first |
| Property-based testing | Use for serialization, parsing, complex logic -- not everything |
| Mocking | Prefer real implementations; mock only when necessary; never mock what you do not own |
| Coverage targets | 80% is a sensible ceiling; 100% has diminishing returns |
| Flaky tests | Quarantine immediately; fix or delete within a sprint |
| Contract testing in monorepos | Shared schemas + validation tests often sufficient without a broker |
| Test maintenance | Delete valueless tests; treat test code as first-class |

---

## Test Audit Results

**Date**: 2026-03-22
**Total tests audited**: ~1,305 unit tests + 12 fuzz tests across 11 services
**Methodology**: Read every test file and its corresponding source. Classified each test class/function by signal value. Identified untested behaviors by comparing source code to coverage.

---

### Summary

| Service | Tests | High Value | Low Value | Remove Candidates | Missing (estimated) |
|---------|-------|-----------|-----------|-------------------|---------------------|
| common (all) | ~357 | ~310 | ~40 | ~7 | ~10 |
| seed | 27 | 27 | 0 | 0 | 2 |
| crawler | 57 | 52 | 5 | 0 | 3 |
| fetcher | 20 | 20 | 0 | 0 | 2 |
| parser | 66 | 60 | 6 | 0 | 3 |
| analyzer | 65 | 60 | 5 | 0 | 3 |
| recovery | 51 | 51 | 0 | 0 | 2 |
| delay-scheduler | 54 | 50 | 4 | 0 | 2 |
| discovery | 67 | 62 | 5 | 0 | 2 |
| admin | 146 | 130 | 16 | 0 | 3 |
| UI | 490 | 240 | 170 | ~80 | 5 |
| **TOTAL** | **~1,400** | **~1,062** | **~251** | **~87** | **~37** |

**Recommendation**: Remove ~87 tests (all in UI). Add ~37 tests across the codebase. Net effect: fewer tests, higher signal-to-noise ratio, faster CI.

---

### Service-by-Service Analysis

---

#### 1. lol-pipeline-common (357 tests across 16 files)

**Overall**: Excellent coverage. Most tests are high value -- they test core library contracts used by every service.

**HIGH VALUE (310)**:
- `test_streams.py` (63 tests): Publish/consume/ack round-trips, DLQ routing, nack behavior, maxlen enforcement, replay_from_dlq, consume_typed. Every test catches a real regression.
- `test_riot_api.py` (67 tests): HTTP status handling (404/403/429/5xx), retry-after parsing, routing, User-Agent. Critical path testing.
- `test_rate_limiter.py` (22 tests): Stored limits, config fallbacks, long window, wait_for_token retry. All high value.
- `test_priority.py` (44 tests): SET-based priority, TTL, clear, downgrade, active set. Important contract.
- `test_raw_store.py` (37 tests): Set/get/exists, write-once semantics, TTL, disk persistence. Core storage contract.
- `test_models.py` (22 tests): Envelope round-trip, DLQ field completeness, default handling. Schema contracts.
- `test_service.py` (16 tests): run_consumer, halted detection, graceful shutdown. Infrastructure.
- `test_config.py` (8 tests): Required vars, defaults, validation. High value.
- `test_log.py` (8 tests): JSON output format, required fields. High value.
- `test_redis_client.py` (7 tests): Ping, singleton, unreachable. High value.
- `test_resolve.py` (6 tests): PUUID resolution, caching. High value.
- `test_xautoclaim.py` (2 tests): Autoclaim behavior. High value.

**LOW VALUE (40)**:
- `test_helpers.py` (26 tests): Some are testing trivial helpers (e.g., `safe_int("5") == 5`). The fuzz tests in `test_helpers_fuzz.py` overlap significantly with the explicit tests -- testing the same "never raises" property.
- `test_models_fuzz.py` (8 tests): Fuzz tests for models. Redundant with the round-trip tests in `test_models.py` but acceptable as defense-in-depth.
- `test_riot_api_fuzz.py` (8 tests): Fuzz tests for riot API. Low marginal value over the 67 deterministic tests.

**REMOVE CANDIDATES (7)**:

| File | Test | Reason |
|------|------|--------|
| `test_helpers_fuzz.py` (7 of 13 tests) | Fuzz tests that duplicate deterministic `test_helpers.py` tests | These duplicate the explicit `test_helpers.py` tests and add 500+ hypothesis examples each. The deterministic tests already cover all branches. If kept, reduce `max_examples` to 50. Remove the 7 that overlap directly with deterministic tests, keep 6 unique fuzzing strategies. |

**MISSING TESTS (10)**:
1. `streams.py`: No test for `consume_typed` with a malformed payload that causes `from_redis_fields` to throw -- should verify DLQ routing on deserialization error.
2. `rate_limiter.py`: No test for concurrent `wait_for_token` calls under contention (asyncio.gather with 50 tasks).
3. `riot_api.py`: No test for network timeout (httpx.TimeoutException) -- only HTTP status codes are tested.
4. `raw_store.py`: No test for concurrent `set` on the same key (write-once race condition).
5. `priority.py`: No test for `downgrade_priority` when the key has already expired (TTL race).
6. `service.py`: No test for `run_consumer` receiving a malformed stream message that cannot be deserialized.
7. `config.py`: No test for environment variable with whitespace (`RIOT_API_KEY=" RGAPI-test "`).
8. `resolve.py`: No test for case-insensitive lookup normalization.
9. `streams.py`: No test for `publish` when Redis connection drops mid-XADD.
10. `models.py`: No test for `DLQEnvelope.from_redis_fields` with extra unknown fields (forward compatibility).

---

#### 2. lol-pipeline-seed (27 tests)

**Overall**: Excellent. Every test verifies a specific acceptance criterion.

**HIGH VALUE (27)**: All tests are high value. They cover:
- Happy path with stream verification
- Cooldown logic (5 distinct scenarios)
- Error handling (404, 403, system:halted)
- CLI parsing
- Priority setting before publish (ordering guarantee)
- Publish-before-hset ordering
- players:all ZSET registration
- Name cache TTL
- Region normalization

**MISSING TESTS (2)**:
1. No test for 429 (rate limit) during seed -- what happens when Riot API returns 429?
2. No test for re-seeding a player who already exists in `players:all` -- verify score is updated (not duplicated).

---

#### 3. lol-pipeline-crawler (57 tests)

**Overall**: Good coverage. Some redundancy in priority clearing tests.

**HIGH VALUE (52)**: Core crawl logic, pagination, early-exit, seen:matches dedup, rank fetching, activity rate computation, halted check.

**LOW VALUE (5)**:

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py ~100-165 | `TestCrawlPriority`, `TestCrawlPriorityPreservation`, `TestCrawlPriorityClearCallBehavior` | Three separate test classes (6 tests) all verify the same thing: priority is cleared after crawl. `TestCrawlPriority.test_crawl__zero_matches__clears_priority_key` and `TestCrawlPriorityPreservation.test_crawl__no_matches__priority_cleared` are identical scenarios. `TestCrawlPriorityClearCallBehavior` uses mock to verify the same behavior already tested by state assertion. Consolidate to 2 tests (matches found + no matches). |

**MISSING TESTS (3)**:
1. No test for pagination beyond 2 pages -- verify 3+ API calls with correct `start` offset.
2. No test for API returning duplicate match IDs across pages.
3. No test for `_compute_activity_rate` with edge inputs (zero matches, one match).

---

#### 4. lol-pipeline-fetcher (20 tests)

**Overall**: Excellent. High signal density -- 20 tests for 213 LOC source. Every test catches a distinct failure mode.

**HIGH VALUE (20)**: All tests are high value: idempotency, 200/404/429/500/403 handling, max attempts, timeout, TTL, seen:matches, timeline fetch, store errors.

**MISSING TESTS (2)**:
1. No test for fetching a match that was already in `seen:matches` -- verify early dedup before API call.
2. No test for match data exceeding RawStore size limits (large JSON response).

---

#### 5. lol-pipeline-parser (66 tests)

**Overall**: Good coverage with real fixture files. Some tests verify overlapping properties of the same parse operation.

**HIGH VALUE (60)**: Parse from fixture, participant extraction, matchup writing, ban writing, timeline parsing, patch normalization, perk extraction, idempotency, status tracking, priority propagation.

**LOW VALUE (6)**: Several fixture-based tests check individual fields of the same parsed result. For example, testing that `kills` is correct AND `deaths` is correct AND `assists` is correct from the same fixture parse -- a single test checking all three fields would be more maintainable.

**MISSING TESTS (3)**:
1. No test for a match with fewer than 10 participants (remake, disconnects).
2. No test for a match with non-standard `queueId` (ARAM, custom games) -- verify correct handling.
3. No test for `_normalize_patch` with unusual patch strings (e.g., "Season 2025 14.1.1").

---

#### 6. lol-pipeline-analyzer (65 tests, including 3 in test_derived.py)

**Overall**: Good. Lock acquisition, cursor-based processing, champion/role aggregation, derived stat computation.

**HIGH VALUE (60)**: Lock semantics, cursor management, safe division (deaths=0), champion stats HINCRBY, role aggregation, match status guard (idempotency), priority clearing.

**LOW VALUE (5)**: Some tests in the lock/cursor area are very similar -- testing "lock held by another worker" and "lock expired" as separate scenarios when they exercise almost the same code path.

**MISSING TESTS (3)**:
1. No test for `_refresh_lock` when the lock holder changes between acquire and refresh.
2. No test for concurrent analyzer instances processing the same PUUID (lock contention).
3. No test for extremely large `player:matches` sorted set (1000+ entries) -- verify cursor-based iteration terminates correctly.

---

#### 7. lol-pipeline-recovery (51 tests)

**Overall**: Excellent. Every failure code is tested, backoff is verified, exhaustion leads to archival.

**HIGH VALUE (51)**: All tests are high value: http_429, http_5xx, parse_error, auth_403, requeue with incremented dlq_attempts, exhaustion archival, backoff calculation, halted check, main entry point.

**MISSING TESTS (2)**:
1. No test for malformed DLQ entry that cannot be deserialized -- verify graceful skip.
2. No test for `_backoff_ms` at very high dlq_attempts values (overflow protection).

---

#### 8. lol-pipeline-delay-scheduler (54 tests)

**Overall**: Good coverage. Circuit breaker tests are thorough.

**HIGH VALUE (50)**: Empty set, future/past/boundary timing, multiple messages, ZREM failure, circuit breaker (open/close/half-open), main entry point.

**LOW VALUE (4)**: Some circuit breaker tests overlap -- `_record_failure` tested both directly AND through `_tick` exercising the same path.

**MISSING TESTS (2)**:
1. No test for a delayed message whose target stream does not exist yet (auto-creation).
2. No test for clock skew -- message score is in the past but system time jumped forward.

---

#### 9. lol-pipeline-discovery (67 tests)

**Overall**: Good coverage. Name resolution, idle detection, batch promotion all well tested.

**HIGH VALUE (62)**: HMGET optimization, missing fields, API fallback, idle check (all streams empty, some active), batch promotion, priority players skip, halted check.

**LOW VALUE (5)**: Some idle check tests are nearly identical -- testing "stream:puuid has entries" and "stream:match_id has entries" separately when they exercise the same XLEN check.

**MISSING TESTS (2)**:
1. No test for `_parse_member` with corrupt JSON in delayed:messages.
2. No test for `_promote_batch` when the batch size exceeds available idle players.

---

#### 10. lol-pipeline-admin (146 tests)

**Overall**: Good coverage for a CLI tool with many subcommands. Some tests are inherently lower value (testing print output format).

**HIGH VALUE (130)**: DLQ list/replay/clear, system halt/resume, reseed, stats display, replay_parse, replay_fetch, delayed list/flush, archive operations, recalc operations, format helpers.

**LOW VALUE (16)**: Tests checking exact CLI output formatting (`capsys` assertions on specific strings). These are brittle -- any wording change breaks them without indicating a real bug. Consider testing behavior (Redis state changes) rather than print output.

**MISSING TESTS (3)**:
1. No test for `cmd_reseed` when the Riot API returns a different PUUID for an existing player (name change).
2. No test for `cmd_dlq_replay` with concurrent replays of the same entry.
3. No test for `cmd_reset_stats` -- verify all stat keys are actually deleted.

---

#### 11. lol-pipeline-ui (490 tests) -- PRIMARY CONCERN

**Overall**: This is the main finding of the audit. The UI has 490 tests for 3,429 LOC source -- a test-to-code ratio of 7,629 test lines for 3,429 source lines (2.2:1). Many tests are low-value HTML string assertions that will break on any CSS class rename, HTML restructuring, or wording change without catching actual bugs.

**HIGH VALUE (240)**: Tests that verify:
- Business logic: auto-seed ordering, priority setting, cooldown, rate limiting before API calls, name cache eviction, PUUID validation, region validation
- Security: XSS escaping, input validation, CSP headers, PUUID regex, DLQ entry_id validation, HTML escaping in all user-facing renders
- Data correctness: stats formatting, match history data rendering, DLQ replay envelope reconstruction, champion diversity computation, streak/tilt indicator math, PBI tier assignment, patch delta computation, breakdown accumulation
- Error handling: 404/429/403/500 error messages, corrupt DLQ entries, Redis connection errors
- Route behavior: DLQ replay (303 redirect, 404/422 errors), pagination, match history pagination, streams fragment

**LOW VALUE (170)**: Tests that assert:
- Presence of specific CSS class names (`assert "badge--success" in result` x50+)
- Presence of specific HTML structure (`assert "<thead>" in body`)
- CSS constant contents (`assert "--color-bg:" in _CSS`, `assert ".card" in _CSS`)
- Navigation link presence (tested repeatedly across multiple classes)
- Halt banner shown on every page (6 nearly identical test classes, 18 tests total)
- Same DLQ entry rendering verified from 4 different angles
- Exact HTML attribute formatting (`assert 'scope="col"' in body`)
- Repeated `import fakeredis` / `import MagicMock` inside every test method rather than using fixtures

**REMOVE CANDIDATES (80 tests)**:

**Category A: CSS constant tests (8 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:181-184 | `TestCssConstant.test_css_contains_design_tokens` | Tests that a Python string constant contains specific CSS tokens. Zero bug-catching value -- if the CSS is wrong, users see it visually. |
| test_main.py:188-196 | `TestCssConstant.test_css_contains_component_classes` | Same: asserts CSS string contains `.card`, `.badge`, etc. |
| test_main.py:197-199 | `TestCssConstant.test_css_contains_responsive_breakpoints` | Asserts `@media (min-width: 768px)` is in CSS string. |
| test_main.py:201-205 | `TestCssConstant.test_css_contains_log_viewer_styles` | Asserts CSS contains `.log-wrap`, `.log-line`. |
| test_main.py:207-211 | `TestCssConstant.test_css_dark_log_colors__no_light_artifacts` | Asserts specific hex colors are NOT in CSS. |
| test_main.py:213-215 | `TestCssConstant.test_css_accessibility` | Asserts `:focus-visible` is in CSS. |
| test_main.py:217-218 | `TestCssConstant.test_css_mobile_first_form` | Asserts `flex-direction: column` is in CSS. |
| test_main.py:4301-4305 | `TestCssTableScrollNowrap` | Asserts CSS has `white-space: nowrap`. |

These 8 tests are testing a CSS string constant. CSS correctness is a visual concern, not a unit test concern.

**Category B: Duplicate halt banner tests (18 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:3258-3334 | `TestHaltBannerPlayers` (3 tests) | Duplicates halt banner logic already tested in `TestStreamsFragmentHtmlHaltBanner`. |
| test_main.py:3336-3415 | `TestHaltBannerDlq` (3 tests) | Same pattern: halted + not halted + empty+halted. |
| test_main.py:3418-3494 | `TestHaltBannerLogs` (3 tests) | Same pattern again. |
| test_main.py:3497-3550 | `TestHaltBannerStatsMatches` (2 tests) | Same pattern again. |
| test_main.py:3314-3333 | `test_players__empty__shows_halt_banner_when_halted` | Identical to `test_players__shows_halt_banner_when_halted` but with no player data -- the halt check is independent of data. |
| test_main.py:3396-3415 | `test_dlq__empty__shows_halt_banner_when_halted` | Same: empty DLQ + halted is redundant with DLQ + halted. |
| test_main.py:3468-3494 | `test_logs__with_files__shows_halt_banner_when_halted` | Logs with files + halted is redundant with empty logs + halted. |

The halt banner is a single `_HALT_BANNER` constant injected by `_page()`. Testing it once per route is sufficient; testing halted+not_halted+empty_halted on every route is excessive. Keep 1 test per route (5 total), remove the other 13. Also remove the 5 "not halted" variants since they test the absence of a feature -- a single "not halted" test suffices.

Recommend keeping: `TestStreamsFragmentHtmlHaltBanner.test_streams_fragment_html__halted__shows_halt_banner`, `TestStreamsFragmentHtmlHaltBanner.test_streams_fragment_html__normal__no_halt_shows_running`. Remove the other 16.

**Category C: Redundant DLQ entry rendering tests (12 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:2919-3033 | `TestDlqEntryFields` (3 tests) | `test_show_dlq__entry_shows_original_stream`, `test_show_dlq__entry_shows_failure_code_as_badge`, `test_show_dlq__entry_shows_dlq_attempts` -- all three are already covered by `TestDlqBrowser.test_show_dlq__displays_entries` which checks the same fields. |
| test_main.py:3558-3629 | `TestDlqReplayButton` (2 tests) | `test_show_dlq__each_entry_has_replay_button` and `test_show_dlq__action_column_header` -- already covered by `test_show_dlq__displays_entries`. |
| test_main.py:3861-3975 | `TestDlqPagination` 3 of 6 tests | `test_dlq__default_page_shows_first_25` duplicates `test_show_dlq__shows_up_to_max_per_page_entries`. `test_dlq__per_page_capped_at_50` is testing a constant. `test_dlq__page_shows_per_page_info` tests UI text presence. |
| test_main.py:4050-4083 | `TestDlqPagination.test_dlq__page_shows_per_page_info` | Tests that "per page" text appears -- no behavioral signal. |
| test_main.py:5452-5666 | `TestDlqSummary` 3 of 8 tests | `test_dlq_summary__empty_queue__shows_zero_counts`, `test_show_dlq__includes_summary_when_entries_exist`, `test_show_dlq__includes_summary_when_empty` test the same "summary appears" behavior from different angles. Keep 1. |

**Category D: Navigation and layout string assertions (10 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:149-152 | `TestPage.test_dark_color_scheme_meta` | Asserts `<meta name="color-scheme" content="dark">` is in output. HTML layout detail. |
| test_main.py:153-157 | `TestPage.test_css_uses_custom_properties` | Asserts specific CSS custom property values. |
| test_main.py:2204-2210 | `TestDlqNav.test_page__nav_contains_dlq_link` | Already covered by `TestPage.test_contains_navigation_links`. |
| test_main.py:4243-4247 | `TestNavItemsDashboardLink.test_nav_items__contains_dashboard_link` | Tests that a tuple is in a list. Trivially true if the constant is defined correctly. |
| test_main.py:4255-4264 | `TestRiotAttributionFooter.test_page__contains_riot_attribution` | Tests presence of legal text in HTML. Low regression risk. |
| test_main.py:4318-4324 | `TestCssSortControlsMinHeight` | Tests CSS has `min-height: 44px`. |
| test_main.py:4327-4333 | `TestCssPauseBtnMinHeight` | Tests CSS has `min-height: 44px`. |
| test_main.py:2195-2201 | `TestStatsGrid.test_stats_table__wraps_champs_and_roles_in_stats_grid` | Tests presence of a CSS class in HTML output. |
| test_main.py:1684-1690 | `TestFavicon.test_page__contains_favicon_link` | Tests that `rel="icon"` is in HTML. |
| test_main.py:5071-5074 | `TestMatchupsPage.test_matchups_in_nav` | Tests nav tuple contains `/matchups`. |

**Category E: Constant value assertions (6 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:2626-2628 | `TestNameCacheIndex.test_name_cache_max_is_10000` | `assert _NAME_CACHE_MAX == 10_000` -- testing a constant equals itself. |
| test_main.py:2630-2632 | `TestNameCacheIndex.test_name_cache_index_key` | `assert _NAME_CACHE_INDEX == "name_cache:index"` -- testing a constant. |
| test_main.py:2843-2845 | `TestAutoSeedCooldown.test_autoseed_cooldown_constant` | `assert _AUTOSEED_COOLDOWN_S == 300` -- testing a constant. |
| test_main.py:6055-6057 | `TestStatsTableDiversity.test_min_games_constant_is_20` | `assert _DIVERSITY_MIN_GAMES == 20` -- testing a constant. |
| test_main.py:6169-6171 | `TestStreakIndicator.test_constants` | Tests two constants equal specific values. |
| test_main.py:7627-7629 | `TestBreakdownMatchCount.test_constant_is_50` | Tests a constant. |

These provide zero signal -- if someone changes the constant, they intend to change the value.

**Category F: Redundant region tests (8 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:2231-2254 | `TestRegionsComplete` 4 of 5 tests | `test_regions_has_16_entries`, `test_regions_includes_sea_platforms`, `test_regions_includes_ru_and_tr1`, `test_regions_includes_la1_la2` -- all are subsets of `test_regions_contains_all_platform_keys` which already verifies every platform key. Keep only `test_regions_contains_all_platform_keys`. |
| test_main.py:2256-2273 | `TestRegionDropdownSelectedSpace` 2 of 3 tests | `test_selected_has_leading_space` duplicates `TestStatsForm.test_region_default_na1_selected`. `test_all_regions_render_as_options` duplicates the dashboard region test. Keep `test_non_selected_no_selected_attr` which is unique. |
| test_main.py:2716-2719 | `TestRegionValidation400.test_regions_set_matches_regions_list` | Tests that a frozenset equals a set of a list. Implementation detail. |
| test_main.py:2700-2714 | `TestRegionValidation400.test_valid_region__no_400` | Tests 3 valid regions do not return 400. Trivially true -- the real test is the invalid region test. |

**Category G: Duplicate match history and badge rendering (8 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:383-394 | `TestMatchHistoryHtml.test_win_uses_badge` | Already covered by `test_renders_match_rows` which checks WIN. |
| test_main.py:408-418 | `TestMatchHistoryHtml.test_loss_uses_badge` | Already covered by `test_loss_renders_correctly`. |
| test_main.py:476-485 | `TestMatchHistoryHtml.test_match_list_wrapper` | Tests CSS class presence. |
| test_main.py:116-119 | `TestMatchHistorySection.test_event_delegation_script` | Overlaps with `test_uses_data_attributes`. |
| test_main.py:123-130 | `TestMatchHistorySection.test_loading_indicator_uses_spinner` | Tests HTML structure detail. |
| test_main.py:238-241 | `TestBadge.test_returns_span` | Trivially true -- already covered by `test_valid_variants`. |
| test_main.py:243-245 | `TestBadge.test_plain_text_preserved` | Subsumed by `test_valid_variants`. |
| test_main.py:267-270 | `TestBadgeHtml.test_returns_span` | Trivially true. |

**Category H: Over-tested formatting and structure (10 tests to remove)**

| File:Line | Test | Reason |
|-----------|------|--------|
| test_main.py:247-249 | `TestBadge.test_ampersand_escaped` | Subsumed by `test_auto_escapes_html_in_text`. |
| test_main.py:253-256 | `TestBadgeHtml.test_valid_variants` | Exact duplicate of `TestBadge.test_valid_variants` for a parallel function. |
| test_main.py:258-260 | `TestBadgeHtml.test_invalid_variant_raises` | Duplicate of `TestBadge.test_invalid_variant_raises`. |
| test_main.py:5645-5666 | `TestDlqSummary.test_show_dlq__includes_summary_when_empty` | Duplicate of the existing empty DLQ test. |
| test_main.py:5698-5726 | `TestDlqSummary.test_dlq_summary__escapes_original_stream` | Redundant with `test_dlq_summary__escapes_failure_code` -- same XSS escaping property. |
| test_main.py:5800-5818 | `TestStreamsConsumerLag.test_streams_fragment__pending_count_displayed` | Overlaps with `test_streams_fragment__displays_consumer_group_info`. |
| test_main.py:5882-5889 | `TestFormatGroupCells.test_empty_groups__returns_dashes` | Already tested by the streams fragment "no groups shows dash" test. |
| test_main.py:7491-7518 | `TestChampionTableHeader` + `TestRoleTableHeader` (4 tests) | Tests that header strings contain "Champion", "Games". Pure HTML assertions. |

**MISSING TESTS (5)**:
1. **Dashboard route (`index`)**: No test for the main dashboard page rendering pipeline stats, player count, recent activity. This is the landing page.
2. **`show_stats` cache hit path**: No test verifying that when `player:name` cache returns a valid PUUID and stats exist, NO Riot API call is made AND the response includes correct stats.
3. **`show_champions` sort order**: No test verifying champion tier table is sorted by PBI descending.
4. **`dlq_replay` atomicity**: No test verifying that XDEL and XADD happen atomically (pipeline). If XADD fails after XDEL, the entry is lost.
5. **Error recovery in `_resolve_and_cache_puuid`**: No test for what happens when Redis SET fails after successful Riot API call.

---

### Cross-Cutting Findings

#### Pattern: Import-inside-test-method (UI only)

The UI test file imports `fakeredis`, `MagicMock`, `show_stats`, etc. inside every async test method instead of using module-level imports and fixtures. This adds ~3,000 lines of boilerplate. Not a test value issue per se, but it inflates the file size and makes reviews harder. Recommend refactoring to use fixtures.

#### Pattern: Redundant "empty state" + "with data" + "halted" permutations

Many UI route tests follow a template:
1. Empty data shows empty state
2. With data shows table
3. Halted shows banner
4. Halted + empty shows banner
5. Not halted does not show banner

This produces 5 tests per route when 2-3 suffice (empty, with data, halted). The "not halted" and "halted+empty" variants add no signal.

#### Pattern: HTML string assertions are fragile

~170 UI tests assert specific HTML substrings. Any one of these changes would break:
- Renaming a CSS class (e.g., `badge--success` to `badge-success`)
- Adding a wrapper `<div>`
- Changing column order in a table
- Rewording a user-facing message

None of these changes would indicate a bug. These tests create drag on refactoring.

#### Pattern: Fuzz tests are valuable but oversized

The `test_ui_fuzz.py` (12 tests, 500 max_examples each) and common fuzz tests are well-designed but run 500-1000 examples per test. For CI speed, reduce to 50-100 examples. The diminishing returns above 100 are minimal for these input spaces.

---

### Priority-Ordered Action Items

1. **Remove ~80 UI tests** as listed above. Net savings: ~1,600 lines of test code, ~4 seconds of CI time.
2. **Add 5 missing UI tests** (dashboard route, cache hit path, sort order, replay atomicity, resolve error).
3. **Add 10 missing common tests** (timeout, concurrent access, malformed input, forward compatibility).
4. **Consolidate 5 crawler priority tests** into 2.
5. **Add 15 missing service tests** across seed/crawler/fetcher/parser/analyzer/recovery/delay-scheduler/discovery.
6. **Reduce fuzz test max_examples** from 500 to 100 across all fuzz files (saves ~10s CI time).
7. **Refactor UI test imports** to use module-level imports and shared fixtures (reduces file from 7,629 to ~5,000 lines).

## Correctness Verification Review

Formal analysis of whether the test suite proves the invariants it claims to prove.
Each finding below identifies a gap where a real bug could escape undetected.

---

### Finding 1: Rate limiter tests do not verify atomicity under concurrent access

**Invariant**: At no point do more than N requests exist in the 1-second sliding window (INV-4).

**What the unit tests prove**: Sequential correctness only. `TestStoredLimits` calls `acquire_token` in a serial loop and verifies that the (N+1)th call is denied. This proves the Lua script counts correctly when calls arrive one at a time.

**What the unit tests do not prove**: That two concurrent `acquire_token` calls cannot both read `ZCARD < limit` and both add a member, producing `ZCARD = limit + 1`. The Lua script *is* atomic on a single Redis instance (EVAL is single-threaded), so this invariant holds -- but the test suite does not exercise this. If someone refactored the Lua script into two separate Redis commands (breaking atomicity), no unit test would fail.

**Integration test coverage**: IT-12 (`test_concurrent_acquire_token__never_exceeds_limit`) does test this with 20 concurrent `asyncio.gather` calls against a real Redis container and asserts `admitted <= limit`. This is the correct test, but it lives only in integration tests that require testcontainers and are likely not run on every commit. IT-07 also monitors ZCARD under load with 3 concurrent fetchers.

**Risk**: Low (integration tests exist), but the unit test suite gives false confidence about atomicity.

**Recommendation**: Add a unit-level note or marker documenting that atomicity is verified only at the integration tier, or add a concurrent unit test using `asyncio.gather` against fakeredis (which does support Lua via lupa).

---

### Finding 2: Analyzer lock tests verify acquisition and release but not mutual exclusion

**Invariant**: At most one Analyzer processes a given PUUID at any time (INV-3).

**What the tests prove**: `test_lock_held_by_another_worker_acks_immediately` shows that if worker B finds the lock held, it discards the message and ACKs. `test_lock_acquired_processes_and_releases` shows that a worker acquires, processes, and releases. `test_lock_stolen_logs_warning` shows correct handling when a lock expires mid-processing.

**What the tests do not prove**: That two concurrent workers calling `_analyze_player` for the same PUUID cannot both acquire the lock and both write stats. The `SET NX` is atomic, but the test never exercises the concurrent scenario. If someone changed `nx=True` to a check-then-set pattern, no test would catch it.

**Integration test coverage**: IT-06 (`test_concurrent_workers`) runs 2 analyzers concurrently on 10 matches for the same player and asserts `total_games == 10`. This is the right test. However, since all 10 matches share one PUUID, this implicitly tests that the lock prevents double-counting -- but only because the cursor mechanism prevents reprocessing. The lock and cursor are tested together, never independently under concurrency.

**Gap in IT-06**: The test publishes 10 distinct matches. Both analyzers will lock the same PUUID but process different match subsets (the cursor prevents overlap). This tests cursor correctness, not lock mutual exclusion. To test lock exclusion, you would need two workers receiving duplicate analyze messages for the same PUUID with the same match set, and verify stats are counted exactly once.

**Risk**: Medium. A subtle bug where the lock is acquired but the cursor is not read atomically under the lock could cause double-counting. The `_PROCESS_MATCH_LUA` script mitigates this by checking lock ownership inside the Lua script, but the test suite never constructs a scenario where this guard is the deciding factor.

---

### Finding 3: Parser idempotency test does not verify stream:analyze message count on re-parse

**Invariant**: Processing the same message twice produces the same final state (INV-2).

**What `test_reparse_idempotent` proves**: The `player:matches` sorted set is idempotent (ZADD with same score is a no-op). The match hash is overwritten with the same values.

**What it does not verify**: On re-parse, 10 new `stream:analyze` messages are published again (the code comment acknowledges this: "stream:analyze gets new messages (idempotent at consumer)"). The test does not assert on `xlen(_OUT_STREAM)`. After two parses of the same match, there will be 20 analyze messages, not 10. The system relies on the analyzer's cursor to make this safe, but no test verifies that the cursor correctly deduplicates these duplicate analyze messages.

**`test_reparse__bans_matchups_not_double_counted` is strong**: The SADD atomic guard for bans/matchups is properly tested. Parse same match twice, verify HINCRBY counts are 1, not 2. This is a genuine idempotency test.

**Risk**: Medium. If the analyzer cursor logic has a bug, double-counted stats would not be caught by any parser test. The tests assume cross-service idempotency without verifying it end-to-end in a single test.

---

### Finding 4: No test verifies the DLQ lifecycle invariant end-to-end under crash conditions

**Invariant**: Every failed message is either retried (up to dlq_max_attempts) or archived. No infinite loops. No message loss (INV-1).

**What is tested**: IT-03 tests the happy path: 429 -> DLQ -> recovery -> delay -> retry -> success. Unit tests verify nack_to_dlq writes to DLQ, recovery routes by failure_code, archive writes to stream:dlq:archive, and the delay scheduler dispatches from delayed:messages.

**What is not tested**: The crash-between-steps scenarios for the DLQ lifecycle:

1. Recovery crashes after ZADD but before XACK: The `_requeue_delayed` function uses `pipeline(transaction=True)` to make ZADD + XACK atomic. But no test verifies that if this pipeline partially fails (e.g., Redis connection drops between ZADD and XACK), the message is not lost. On restart, the message would still be in the DLQ PEL and re-consumed -- but this path is untested.

2. Delay scheduler crashes after XADD but before ZREM: The `_DISPATCH_LUA` script makes this atomic, and `TestDuplicateDispatchGuard` tests the guard. This is well-covered.

3. **`_handle_with_retry` crashes during nack_to_dlq itself**: If `nack_to_dlq` raises (e.g., Redis connection lost), the message stays in the PEL and will be redelivered. But the retry counter has already been incremented. On the next delivery, the counter will be incremented again. If the nack keeps failing, the counter can exceed max_retries without ever successfully nacking to DLQ. No test covers this scenario -- the message would effectively be stuck: too many retries to process, but unable to reach DLQ. Looking more carefully at the code: `_handle_with_retry` calls `nack_to_dlq` inside a try block where the handler exception is caught. If nack_to_dlq itself raises, that exception propagates up to `_dispatch_batch`, which catches `(RedisError, OSError)` and sleeps 1s. On next iteration, the message is redelivered from PEL. The counter keeps incrementing. After many cycles, `count >= max_retries` is true on every redelivery, so nack_to_dlq is called on every redelivery. If nack always fails, this is an infinite loop of failed nack attempts. No test covers this degenerate case.

**Risk**: Finding 4.3 is a real gap. A message could be stuck indefinitely between the PEL and a persistently failing nack_to_dlq.

---

### Finding 5: `_PROCESS_MATCH_LUA` cursor does not enforce monotonicity in the Lua script

**Invariant**: `player:stats:cursor:{puuid}` only increases (INV-5).

**What is tested**: `test_cursor_advances_per_match_atomically` verifies that after processing 3 matches with scores 1000, 2000, 3000, the cursor is 3000. `test_five_new_matches` verifies cursor == highest score after 5 matches.

**What is not tested**: The Lua script sets the cursor to `ARGV[7]` (the score of the current match) on each iteration. If matches are processed in order [1000, 3000, 2000], the cursor would be set to 2000 after the last iteration, which is a regression from 3000. The production code processes matches in sorted order from `ZRANGEBYSCORE` (which returns ascending), so this scenario does not arise in practice. However, the Lua script itself does not enforce monotonicity -- it unconditionally sets the cursor to the provided value.

**Risk**: Low. The caller (`_process_matches`) iterates over `new_matches` which comes from `ZRANGEBYSCORE ... withscores=True`, inherently sorted ascending. But the Lua script's contract is weaker than the invariant suggests -- it does not check `ARGV[7] > current_cursor` before writing.

**Recommendation**: Either add a `tonumber(ARGV[7]) > tonumber(redis.call("GET", cursor_key) or 0)` guard in the Lua script, or add a test that verifies the caller always passes matches in ascending score order.

---

### Finding 6: `has_priority_players` spot-check can leave orphans blocking Discovery indefinitely

**Invariant**: Priority detection correctly reflects whether any priority players exist.

**What is tested**: IT-08, IT-09, IT-10 test set/clear/TTL of priority keys and verify `has_priority_players` returns the correct value.

**What is not tested**: `has_priority_players` uses `SRANDMEMBER` to spot-check one random member. If the active set contains N expired members and 0 live members, the function removes one orphan per call, requiring N calls to fully clean up. During this time, Discovery remains paused. No test verifies that Discovery eventually resumes when all priority keys have expired but the set still contains orphans.

**Risk**: Medium in production. If many seeds are created and their priority keys expire (via TTL) while the system is halted (no analyzer running to call `clear_priority`), Discovery could be blocked for `N * polling_interval` duration. The probabilistic cleanup (one random member per check) means worst-case cleanup time is `O(n * polling_interval)` where n is the number of orphans.

---

### Finding 7: Analyzer `system:halted` test does not verify message preservation in PEL

**Invariant**: When `system:halted=1`, all in-flight messages remain in PEL for later redelivery (INV-6).

**What `test_system_halted_skips` proves**: When `system:halted=1`, `_analyze_player` returns without writing stats. The test verifies `total_games is None`.

**What it does not verify**: The test does not assert that the message remains in the PEL (unACKed). Looking at the source code, `_analyze_player` returns early without calling `ack()` when halted, which is correct. But the test only checks the absence of side effects (no stats written), not the preservation of the message. If someone added an `ack()` call in the halted path, this test would still pass.

The parser's `test_system_halted_skips` has the same gap: it checks `xlen(_OUT_STREAM) == 0` but not PEL state.

IT-05 (`test_system_halted`) is stronger: it verifies `pending_info["pending"] >= 1` after a 403 halt, confirming the message stays in PEL.

**Risk**: Low (integration test covers it), but unit tests could silently pass if an `ack()` call is added to the halted path.

**Recommendation**: Add `pending = await r.xpending(_IN_STREAM, _GROUP); assert pending["pending"] == 1` to the halted unit tests.

---

### Finding 8: `_REPLAY_LUA` lacks existence guard, unlike `_DISPATCH_LUA`

**Invariant**: Atomic DLQ replay prevents duplicate messages on crash-restart.

**What `TestReplayFromDlq` proves**: After calling `replay_from_dlq`, the DLQ entry is gone and the target stream has 1 message.

**What is not tested**: The atomic Lua script (`_REPLAY_LUA`) does XADD then XDEL in a single eval. If the entire eval succeeds, both operations complete. If it fails, neither completes (Lua scripts are atomic). But no test verifies the failure case: what happens if `replay_from_dlq` is called twice for the same DLQ entry (simulating a crash-restart where the caller retries)? The second call would attempt XDEL on an already-deleted entry, which is a no-op in Redis. But XADD would add a duplicate message.

The Lua script `_REPLAY_LUA` (streams.py lines 262-282):
```lua
redis.call("XADD", stream, "*", unpack(fields))
redis.call("XDEL", dlq, entry_id)
```

There is no existence check before XADD. Compare to `_DISPATCH_LUA` (delay_scheduler/main.py lines 89-118) which has:
```lua
local exists = redis.call("ZSCORE", zkey, member)
if not exists then return 0 end
```

**Risk**: Low in practice. The recovery service ACKs the DLQ entry after replay, so the consumer group prevents redelivery. But the Lua script itself is not idempotent, unlike `_DISPATCH_LUA`.

**Recommendation**: Add a guard in `_REPLAY_LUA` that checks whether the DLQ entry exists before executing XADD, or document that crash safety relies on the consumer group ACK rather than the Lua script.

---

### Finding 9: No test for `_handle_with_retry` behavior when nack_to_dlq itself fails persistently

**Invariant**: Every published message is eventually processed or DLQ'd; no silent drops (INV-1).

In `_handle_with_retry` (service.py lines 55-97), when the handler crashes `max_retries` times, `nack_to_dlq` is called. If `nack_to_dlq` raises a `RedisError`, the exception propagates to `_dispatch_batch`, which catches it and sleeps 1s. On the next consume cycle, the message is redelivered from PEL. `_incr_retry` is called again, incrementing the counter further. Since `count >= max_retries` is already true, `nack_to_dlq` is called again. If nack keeps failing, the retry counter grows unboundedly and the message never reaches DLQ or archive.

`test_redis_error_in_dispatch_loop_does_not_crash` tests that a RedisError in `_handle_with_retry` does not crash `run_consumer`, but it does not test the eventual fate of the message.

**Risk**: Low-to-medium. In practice, transient Redis errors resolve, and the nack eventually succeeds. But a persistent nack failure (e.g., DLQ stream maxlen rejecting writes) would cause an infinite retry loop.

---

### Finding 10: `_requeue_delayed` uses MULTI/EXEC across different key hash slots

**Source**: `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-recovery/src/lol_recovery/main.py`, line 94.

```python
async with r.pipeline(transaction=True) as pipe:
    await pipe.zadd(_DELAYED_KEY, {member: ready_ms})
    await pipe.xack(_IN_STREAM, _GROUP, msg_id)
    await pipe.execute()
```

`transaction=True` wraps commands in MULTI/EXEC, which provides atomicity on single-node Redis. In a Redis Cluster, MULTI/EXEC requires all keys in the same hash slot. `_DELAYED_KEY` ("delayed:messages") and `_IN_STREAM` ("stream:dlq") map to different slots. This code would fail in a cluster deployment.

No test verifies that this pipeline actually executes atomically (recovery unit tests mock Redis). No test fails if `transaction=True` is changed to `transaction=False`.

**Risk**: Low for current deployment (single Redis node). Would become a blocking issue on Redis Cluster migration.

---

### Finding 11: consume_typed three-phase ordering is untested

**What is untested**: The `consume_typed` function (streams.py lines 127-188) has three phases: (1) drain own PEL, (2) XAUTOCLAIM idle messages, (3) block-wait for new messages. Phase 2 only runs if phase 1 returns empty. Phase 3 only runs if phase 2 returns empty. No test exercises the transition between phases -- specifically, a scenario where a consumer has messages in its own PEL AND there are idle messages from dead workers waiting for XAUTOCLAIM. The test verifies that XAUTOCLAIM works when the PEL is empty, but not that it is correctly skipped when the PEL is non-empty.

**Risk**: Low. The design is correct (own PEL takes priority). But a refactor that changes the phase ordering would not be caught by any existing test.

---

### Summary of findings by severity

| # | Severity | Finding |
|---|----------|---------|
| 4.3 | **High** | No test for persistent nack_to_dlq failure (message stuck in PEL with unbounded retry counter) |
| 3 | **Medium** | Parser re-parse does not verify duplicate analyze stream messages; relies on untested cross-service cursor dedup |
| 2 | **Medium** | No concurrent mutual exclusion test for analyzer lock; IT-06 tests cursor, not lock isolation |
| 6 | **Medium** | `has_priority_players` orphan cleanup is probabilistic with O(n) worst case, untested with multiple orphans |
| 8 | **Medium** | `_REPLAY_LUA` lacks idempotency guard (XADD runs even if DLQ entry was already deleted) |
| 5 | **Low** | Lua cursor SET does not enforce monotonicity; relies on caller providing ascending order |
| 7 | **Low** | Unit tests for system:halted do not verify PEL preservation; integration test IT-05 covers this |
| 1 | **Low** | Rate limiter atomicity only tested at integration tier, not in unit tests |
| 9 | **Low** | Persistent nack_to_dlq failure causes unbounded retry counter growth |
| 11 | **Low** | Three-phase consume ordering (PEL -> XAUTOCLAIM -> new) untested as a sequence |
| 10 | **Low** | `_requeue_delayed` MULTI/EXEC crosses hash slots; incompatible with Redis Cluster |

### Key files referenced

- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/rate_limiter.py` -- Lua script (lines 27-73)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/streams.py` -- replay Lua (lines 262-282), consume_typed (lines 127-188)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/service.py` -- _handle_with_retry (lines 55-97)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/priority.py` -- has_priority_players (lines 73-93)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-analyzer/src/lol_analyzer/main.py` -- _PROCESS_MATCH_LUA (lines 49-78), _analyze_player (lines 270-364)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-parser/src/lol_parser/main.py` -- _parse_match (lines 383-502)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-recovery/src/lol_recovery/main.py` -- _requeue_delayed (lines 71-97)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py` -- _DISPATCH_LUA (lines 89-118)
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/tests/unit/test_streams.py` -- all stream operation tests
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/tests/unit/test_rate_limiter.py` -- rate limiter unit tests
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-analyzer/tests/unit/test_main.py` -- analyzer lock, cursor, stats tests
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-parser/tests/unit/test_main.py` -- parser idempotency, ban/matchup tests
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-delay-scheduler/tests/unit/test_main.py` -- dispatch, circuit breaker, duplicate guard tests
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/tests/unit/test_service.py` -- retry counter, priority ordering, consumer loop tests
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/tests/integration/test_it06_concurrent.py` -- concurrent parser/analyzer test
- `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/tests/integration/test_it12_concurrent_rate_limit.py` -- concurrent rate limiter test
