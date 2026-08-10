---
name: regmon-re
description: Use when helping a user install, operate, or get support for Regmon-RE — the standalone register monitor for ARM microcontrollers (STM32 etc.) with AI analysis via opencode. Covers the five-step install, launching, the console's AI tools (Analyse Reg / Analyse Prog / Strings / Save Flash / Ask AI), SWD control, and troubleshooting.
---

# Regmon-RE skill

Regmon-RE is a keyboard-operated register monitor for ARM microcontrollers. It
talks to a chip over an SWD debug probe (via the bundled `swdd` daemon), reads
live registers against SVD databases, and provides AI analysis through
`opencode run` (the user's configured AI — e.g. DeepSeek).

## Key facts

- **AI agent:** opencode (`opencode run --agent build "..."`). Default agent
  `build`; override with `RE_RUN_AGENT`.
- **SWD daemon:** `swdcom/swdd` — serves a Unix socket at `/tmp/swdd-cmd.sock`.
- **Databases:** `databases/*.db` — SVD register databases (F051/F103/F407/L0xx)
  + `stm32f0xx-rm.db` reference extracts. The console picks the right one from
  the chip's IDCODE.
- **Version:** v4.0.0 (standalone release; a distinct tree from the swdai
  2.x console — FossilCon-only pieces removed, AI via opencode).
- **No audio/voice/TTS** — keyboard-operated only.

## Install (the five steps)

1. Install opencode (https://opencode.ai).
2. Get an AI account — DeepSeek, or free DeepSeek Flash via OpenRouter.
3. Point opencode at the AI (API key, two minutes).
4. Download Regmon-RE — the `regmon-re.fossil` file.
5. Say: **"read the install and do it"** — opencode creates a directory, opens
   the Fossil repo, checks python3+tkinter, links `swdd`, verifies the
   databases, and launches the console.

Approve opencode's permission prompts as it runs setup commands.

## Launch

```
swdd &            # start the SWD daemon
regmon-console    # launch the console
```

The console auto-detects the chip (IDCODE) and loads the right SVD database.
"No target" = no probe/chip connected — not an error.

## Console features

- **Detect Chip** — re-read the IDCODE, reload the correct database.
- **Ask AI** — register-level questions, answered via opencode.
- **Analyse Reg** — sample one register ~20× over ~10s, AI verdict on what it
  does.
- **Analyse Prog** — fingerprint the whole chip (RCC clock gates + dynamic
  registers), AI verdict on the program.
- **Strings** — scan flash for readable text (live % progress).
- **Save Flash** — dump the full flash image to `flash-dumps/` (offline
  analysis / chip cloning).
- **SWDD ON/OFF** — stop/start swdd from the console (flash workflow).
- **Snapshot / Upload** — capture the console window; optional git upload.

## Troubleshooting

- **"No target"** — probe not connected, or swdd not running (`swdd &`).
- **swdd won't start** — probe not detected on USB, or another process holds the
  ST-Link claim.
- **AI says "opencode not found"** — opencode not installed/on PATH.
- **Wrong database** — re-run Detect Chip; confirm the chip is wired.

## Reference

- `doc/requirements.md` — system + hardware requirements, support model.
- `doc/operation.md` — full operation guide.
- `doc/portability-design.md` — design notes.
- `README.md` — the five-step install + repo contents.
