# Software Engineering (Process & Principles)

This document captures the **software engineering process, design principles, and development workflow** used by the Japanese Lesson Generator project.

It is intended as a companion to the living progress report (progress_report.md), focusing on how we work (not what we built).

---

## Design Principles

The project is guided by a small set of core engineering principles, starting with the most fundamentals:

- **High cohesion** — each module has one responsibility.
- **Low coupling** — modules communicate via well-defined interfaces; no circular dependencies.
- **Composition over inheritance** — prefer small, reusable components.
- **DRY** — avoid duplication; generalize patterns into cohesive modules.
- **KISS** — keep solutions simple; use the right level of complexity to keep overall system understandable and maintainable.
- **YAGNI** — plan with full architecture in mind, but implement only what is needed for the next incremental delivery.
- **Spike-before-scale** — validate ideas with lightweight spikes before investing in full implementations.
- **Performance focus** — LLM calls, video rendering, and TTS are slow; optimize for predictability and repeatability.

---

## Test Development & TDD Rules
- **Workflow**: Follow a strict Red-Green-Refactor cycle.
    1. **Red**: Write a failing test in `tests/` that defines the new CLI command's behavior.
    2. **Green**: Implement the minimal code in the main CLI tool to make the test pass.
    3. **Refactor**: Clean up the implementation while ensuring the test stays green.
- **CLI-First Testing**: Tests must invoke the application via its CLI interface using `subprocess` or `pytest` CLI runners to ensure end-to-end reliability.
- **Mocking**: Use `unittest.mock` for external dependencies (APIs, Databases). Do not run real side-effects during unit tests.
- **Execution (pwsh)**: Always run tests using the project's test command through the active terminal:
  ```powershell
  python main.py test  # Or directly: pytest --verbose
  ```
- **Coverage**: Aim for "Happy Path" verification first, then add edge cases for empty inputs or invalid CLI arguments.

--

## Development Cycle

The project follows an iterative, research-driven cycle with heavy documentation and validation.

### 1) Research & Design
- Define problem and requirements.
- Research technology options.
- Document decisions in docs/.
- Design architecture and data structures.

### 2) Spike Implementation
- Create minimal proof-of-concept scripts (spike/).
- Validate technology choices.
- Document findings and key learnings.

### 3) Core Development
- Implement production-ready features.
- Keep dependencies minimal (stdlib-first).
- Write pure functions with clear interfaces.

### 4) Testing & Validation
- Run unit tests and targeted integration scenarios.
- Verify output formats and edge cases.
- Document results.

### 5) Documentation & Planning
- Update progress_report.md regularly.
- Capture completed work in docs/development_history.md.
- Keep README and docs up to date.
- Maintain a clear backlog of next steps, technical debt, and future ideas.

### 6) Repository Management
- Organize code into clear modules and packages.
- Keep generated artifacts gitignored.
- Ensure reproducibility with fixed seeds and documented dependencies.
