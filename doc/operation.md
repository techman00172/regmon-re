# Regmon-RE — Operation Guide

_Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING._

_How to operate Regmon-RE: launch, read registers, analyse, and use the AI._

## Launch

```
swdd &              # start the SWD daemon (serves /tmp/swdd-cmd.sock)
regmon-console      # launch the console
```

The console starts on the display. It auto-detects the connected chip from its
IDCODE and loads the matching SVD database. Until a probe + chip are connected
it shows "No target" — that is correct, not an error.

## The console layout

- **Top row:** search box, `Detect Chip`, `Snapshot`, `Upload`.
- **Chip header:** yellow chip class + cyan `DEV_ID 0xNNN · Flash: N KB ·
  DB: <name>`.
- **Peripheral tree:** click a peripheral to expand its registers; click a
  register to see its bitfields decoded in the panel.
- **AI row:** `Analyse Reg`, `Analyse Prog`, `Strings`, `Save Flash`, `Clear`,
  `Copy`, and the `Ask AI` input.
- **Bottom bar:** live status, version (`v4.0.0`), and `SWDD ON` / `SWDD OFF`.

## Reading registers

1. Type in the search box to jump to a peripheral/register.
2. Click a register in the tree.
3. Its current value and every bitfield (name, bit range, meaning) show in the
   bitfield panel. Values refresh live as the chip runs.

## The AI tools

| Button | What it does |
|---|---|
| **Ask AI** | Type any register-level question. Sent via `opencode run` to your AI, answered in the analysis box. |
| **Analyse Reg** | Samples one register ~20 times over ~10s, then asks the AI what the register is doing (static? toggling? why?). |
| **Analyse Prog** | Fingerprints the whole chip — reads which peripherals are clock-enabled, samples their dynamic registers, and asks the AI what the program is doing. |
| **Strings** | Scans flash for readable English text (firmware messages, Forth words, secrets). Shows live % progress. |
| **Save Flash** | Dumps the entire flash image to `flash-dumps/` (offline analysis with Ghidra/objdump, or clone an identical chip). |
| **Clear** | Clears the AI analysis box. |
| **Copy** | Copies the analysis/selection to the clipboard. |

## SWD control

- **SWDD ON / SWDD OFF** start/stop the swdd daemon from the console. The solid
  block sits on the button matching the real state.
- Use it to stop swdd, flash a new binary with `st-flash`, then start swdd again
  — all from the console, no terminal needed.
- Why: the ST-Link is single-owner. swdd holds it to read live; st-flash needs
  it to program. The OFF/ON dance hands the probe over and back. Full detail in
  `doc/programming-the-chip.md` (including how the synchronized ring buffer
  works and why Forth works off the shelf).

## Typical workflows

- **"Is this peripheral alive?"** — click its register, run Analyse Reg.
- **"What is this firmware doing?"** — run Analyse Prog, read the verdict.
- **"What's stored in this chip?"** — run Strings (text) or Save Flash (full
  image).
- **"Why is my UART not transmitting?"** — Ask AI with the USART status
  register selected; the AI knows the register context.

## Asking opencode for help

Any user (hobbyist or pro) can just ask opencode:

- "How do I read a register?"
- "Explain what Analyse Prog does."
- "The console shows No target — what's wrong?"
- "Walk me through Save Flash and cloning a chip."

opencode reads this repo and can teach, diagnose, and guide — no manual needed.
