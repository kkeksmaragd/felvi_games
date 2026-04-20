# Software Engineering (Process & Principles)

This document captures the **software engineering process, design principles, and development workflow** used by the Japanese Lesson Generator project.

It is intended as a companion to the living progress report (progress_report.md), focusing on how we work (not what we built).

---

## Design Principles

The project is guided by a small set of core engineering principles:

- **High cohesion** — each module has one responsibility.
- **Low coupling** — modules communicate via well-defined interfaces; no circular dependencies.
- **Composition over inheritance** — prefer small, reusable components.
- **YAGNI** — implement only what is needed for the next incremental delivery.
- **KISS** — keep solutions simple and readable.
- **DRY** — avoid duplication; generalize patterns into cohesive modules.
- **Spike-before-scale** — validate ideas with lightweight spikes before investing in full implementations.
- **Performance focus** — LLM calls, video rendering, and TTS are slow; optimize for predictability and repeatability.

---

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

### 6) Repository Management
- Organize code into clear modules and packages.
- Keep generated artifacts gitignored.
- Ensure reproducibility with fixed seeds and documented dependencies.
