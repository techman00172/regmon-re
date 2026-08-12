# Agents

## Change Rule
Do not change anything or restart system tools without permission.

## Markdown
All text documents created by the AI agent must be in Markdown as used by the Fossil DCVS Wiki unless otherwise specified

## Tables
Do not use SciMark wiki tables anywhere
All fossil chat communication and tables must be in Fossil Wiki Markdown

## Version Control
Use Fossil DCVS instead of GIT on this PC

## Wgetpaste
use 'wgetpaste <filename>' to paste text files

## Sign-off
Always conclude your response with '✅ finished' when a task is complete

## Completion over speed
Terry always prefers accurate completion over speed. Speed is of minor importance
to him — he is usually busy with other tasks after setting the AI a task to do.
Take the time needed to get the result right; do not cut corners or rush to finish
quickly at the expense of correctness.

## Code changes
After AI has made code changes, advise the user that the changes are waiting for
his review and commit. The AI does not commit; the user commits.

## Regmon-RE

Regmon-RE is a **standalone register monitor** for ARM microcontrollers
(STM32 etc.), keyboard-operated, with AI analysis via **opencode** (the user's
configured AI — e.g. DeepSeek). It reads the chip over an SWD probe through the
bundled `swdd` daemon.

Key facts an agent should know:

- **AI:** `opencode run --agent build "<prompt>"` — env `RE_RUN_AGENT` (default
  `build`), `RE_OPENCODE` (default `opencode`).
- **swdd:** `swdcom/swdd`, serves Unix socket `/tmp/swdd-cmd.sock`.
- **Databases:** `databases/*.db` (F051/F103/F407/L0xx + `stm32f0xx-rm.db`);
  the console auto-picks by chip IDCODE.
- **Version:** v4.1.0 (standalone tree, distinct from the swdai 2.x console).
- **RELEASE GATE (mandatory):** before ANY release (version bump, port, or
  feature), run `bash test/test-harness.sh --build` and confirm
  `=== RESULT: N passed, 0 failed ===`. The harness builds a fresh Podman
  container, verifies the repo contents, databases (+ alternate-function
  tables), Python compile, console launch, and setup.sh — all in isolation.
  Do NOT cut corners: a release that has not passed the harness is not
  released. (Rule added 2026-08-13 after the v4.1.0 port skipped the gate.)
- **No audio/voice/TTS** — keyboard only.
- **Docs for the user/AI:** `doc/requirements.md`, `doc/operation.md`,
  `doc/skills/regmon-re/SKILL.md`, `README.md`.
- **Install:** the five-step AI-driven flow — see `README.md` (user just
  downloads the `.fossil` and says "read the install and do it").
