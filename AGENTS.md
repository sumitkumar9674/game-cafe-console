# Game Cafe Console — Codex Instructions

## Project Purpose

Game Cafe Console is a local Windows gaming café management system.

The long-term system will allow an admin PC to manage gaming PCs over the local network, including user sessions, timed access, PC locking, session renewal, and Windows logout.

The core system should work locally on the café LAN without requiring cloud services.

---

## Development Approach

This project is also a learning project.

Prefer code that is:

- Simple
- Readable
- Explicit
- Easy to explain
- Easy to test

Avoid clever or unnecessarily complex solutions.

Do not overengineer features based on future requirements.

Build and validate one capability at a time.

---

## Scope Control

Only implement what the current task explicitly requests.

Do not proactively build future features.

Do not refactor unrelated code unless it is necessary for the requested change.

Do not modify unrelated files.

If a larger architectural change appears necessary, explain it before implementing it.

---

## Python First

Use Python as the primary language for this project.

Prefer Python's standard library when it is suitable.

Do not introduce another language, framework, or major dependency unless there is a clear technical reason.

If an external dependency is required, explain:

1. Why it is needed.
2. What problem it solves.
3. Why the standard library is insufficient.

Do not silently install dependencies.

---

## Local-First Architecture

Core café functionality must not depend on an internet connection or cloud service.

The intended architecture is based on communication between machines on the local network.

Cloud services should not be introduced for core functionality unless explicitly requested.

---

## Experiments

Experimental code must remain separate from production code.

Use the `experiments/` directory for feasibility tests and Windows behavior experiments.

Do not move experimental behavior into the real agent until the experiment has been validated and we explicitly decide to integrate it.

Experiments should test one important behavior at a time whenever practical.

---

## Windows Safety

This project will eventually interact with Windows at a low level.

Never make persistent or security-sensitive Windows changes unless the current task explicitly requests them.

This includes, but is not limited to:

- Windows Registry changes
- Group Policy changes
- User account changes
- File permission changes
- Firewall changes
- Startup configuration
- Windows services
- Task Manager restrictions
- Shell replacement
- Keyboard or system shortcut restrictions
- Login/logout configuration
- BIOS/UEFI-related changes

Do not perform these changes simply because they may help achieve a future project goal.

---

## Experimental Recovery

During development and feasibility testing, prioritize the ability to recover control of the development PC.

If an experiment can restrict normal PC interaction, provide a clear development-only unlock, stop, or recovery mechanism unless the current task explicitly defines another safety mechanism.

Production restrictions will be designed separately after feasibility testing.

---

## Security Model

Do not describe application-level behavior as "unbreakable" or "impossible to bypass."

Clearly distinguish between:

- UI-level restrictions
- Process-level controls
- Windows service privileges
- Windows account permissions
- Operating-system security policies

When a requirement cannot be reliably enforced at the current privilege level, explain the limitation instead of hiding it with a fragile workaround.

---

## Code Organization

Keep responsibilities separated.

Current project areas may include:

- `agent/` — real gaming-PC agent code
- `experiments/` — isolated feasibility experiments

Additional architecture should be introduced only when required.

Use descriptive function, class, variable, and file names.

Avoid large files containing unrelated responsibilities.

---

## Comments and Explanations

Use comments for important reasoning, Windows-specific behavior, and non-obvious implementation details.

Do not comment every obvious Python statement.

When completing a task, briefly explain:

- What changed
- Why it was implemented that way
- Any important limitation discovered
- How to run or test it

---

## Git

Do not create commits, push branches, merge branches, or modify Git history unless explicitly requested.

Do not commit secrets, credentials, generated build output, local databases, or virtual environments.

Keep changes focused so they can be committed as small logical units.

---

## Secrets and Configuration

Never hardcode passwords, tokens, credentials, or other secrets into source code.

Do not expose secrets in logs or example output.

Use appropriate local configuration or environment variables when secrets eventually become necessary.

---

## Decision Priority

When multiple implementations are possible, prefer this order:

1. Safe to test
2. Correct
3. Simple to understand
4. Reliable
5. Easy to maintain
6. Optimized

Do not sacrifice correctness or reliability for premature optimization.
