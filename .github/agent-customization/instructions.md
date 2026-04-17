# Agent customization — instructions.md

Purpose
-------
This file defines repository-specific instructions for AI agents working in this codebase. It captures hard rules, preferred patterns, and a required commit-summary template that agents must emit whenever they alter files.

Scope
-----
Applies to any AI agent or automation that makes edits in this repository. Use these rules for changes to code, tests, docs, and configuration files unless the user explicitly narrows the scope.

Principles
----------
- Be minimal and surgical: prefer the smallest safe change that satisfies the request.
- Preserve CLI contracts and public APIs; avoid breaking downstream consumers.
- Prefer actionable, verifiable changes: include tests or a reproducible CLI example when adding behavior.

Hard requirements (must follow)
------------------------------
1. Manage todos: For multi-step tasks, always call `manage_todo_list` to create and track a concise plan before making edits.
2. Preamble: Before any tool call that reads/writes files or runs commands, send a 1–2 sentence preamble describing what you will do.
3. Editing files: Use `apply_patch` for modifying existing files (add/update/delete). Use `create_file` only for new files if preferred; do not use unknown/typoed commands like `applypatch`.
4. Commit summary (required): Every time an agent alters files, produce a plain-text, uncompiled Markdown summary suitable for pasting into the GitHub Source Control commit message box. See the template below.
5. Preserve project contracts: Keep CLI behavior and CSV/KML output formats unchanged for `sailpath.py` and `geometry_v0.py`. Do not change default units (miles unless explicitly noted) without explicit user approval.
6. No license headers: Do not add copyright/license headers unless requested.

Commit-summary template (agent MUST emit this after any edit)
------------------------------------------------------------
First line: short title (one line)

One blank line, then a short bullet list describing changes:

- Files changed:
  - path/to/file.ext — added/updated/removed — one-line description
- Rationale: one-line explanation
- Verification: commands/tests to run (brief)

Example (pasteable into commit box):

Add .github/agent-customization/instructions.md — add agent rules

- Files changed:
  - .github/agent-customization/instructions.md — added: repository agent guidelines and commit-summary template
- Rationale: Provide consistent rules for AI agent edits and require commit summaries
- Verification: none (documentation only)

Repository-specific conventions (extracted)
-----------------------------------------
- CLI-first: preserve `argparse` flags and CSV/KML output contracts.
- `sailpath.py` CSV header contract: LOC0,LAT0,LON0,LOC1,LAT1,LON1,STEP_DIST
- Keep azimuth naming (12-bin) and color/bin names stable.
- `sailpath.py` uses meters for `--step-dist`; `geometry_v0.py` default units are miles.

Examples: prompts to test the rule
---------------------------------
- "Refactor duplicate code in `sailpath.py` and include the commit-summary produced by the agent."
- "Add a small unit test for `geometry_v0.py` and produce the commit-summary for the change."

Clarifying questions to ask the repo owner (if not already decided)
------------------------------------------------------------------
- Should the commit-summary include full diffs or only file paths + one-line descriptions? (recommended: file paths + short descriptions)
- Should this policy apply to trivial edits (typo fixes, README copy) or only to code changes? (recommended: all file changes)

How to update this file
-----------------------
Edit this file via the normal process. When updating instructions, include a short changelog entry at the top and produce a commit-summary describing the policy change.

If you want, I can also propose a small CI or pre-commit hook that enforces presence of a commit-summary for PRs and commits.
