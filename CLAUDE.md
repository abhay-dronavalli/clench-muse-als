@AGENTS.md

# Claude Code specifics

Everything in AGENTS.md applies. These notes only add Claude Code behavior.

- `.claude/settings.json` turns off automatic commit attribution. Keep it that way.
- Start parallel sessions in their own worktree: `claude --worktree`.
- Use subagents for bounded jobs with noisy output: running the test suite, auditing a diff,
  researching a library. Summarize their results; do not paste raw logs back.
- If the Codex plugin is installed, run a Codex adversarial review of your diff before writing the
  CHUNK REPORT, verify each finding, and list confirmed and rejected findings in the report.
- Setup and service details for humans live in `docs/RUNBOOK.md`.
