# Claude Code Workflow Setup

This repo uses shared Vrooem hooks from `C:\laragon\www\CarRental\.claude\hooks`.

- `SessionStart`: load concise Vrooem context.
- `UserPromptSubmit`: route task to skills/MCPs.
- `PreToolUse`: block destructive commands and secret edits.
- `PostToolUse`: track changed files.
- `Stop`: remind verification after edits.

Read `AGENTS.md` in this repo and `C:\laragon\www\CarRental\CLAUDE.md` for the full workflow.
