# regmon-re

**A standalone register monitor (reverse-engineering console) for ARM
microcontrollers, with an AI assistant via opencode.**

Keyboard-operated. Talks to a chip over a cheap SWD debug probe and reads the
silicon directly — no vendor tooling, no lock-in. The AI comes from **opencode**
(any model you configure: local or cloud).

## How it works

```
SWD probe → swdd daemon → regmon-console (Tkinter GUI) → opencode run (AI)
```

The console auto-detects the connected chip from its IDCODE, loads the matching
SVD register database, and lets you:
- browse peripherals/registers/bitfields live
- **Analyse Reg** — watch a register across ~10s, AI verdict on what it's doing
- **Analyse Prog** — fingerprint the whole chip, AI verdict on the program
- **Strings** — scan flash for readable text (firmware secrets)
- **Save Flash** — dump the whole flash image (clone / offline analysis)
- **Ask AI** — any register-level question answered via opencode

## Requirements

- **opencode** — https://opencode.ai (the AI agent)
- **python3 + tkinter** (Arch: `tk`, Debian: `python3-tk`)
- **swdd** — bundled in `swdcom/` (talks to the ST-Link probe)
- an **SWD debug probe** (ST-Link etc.) + a target STM32 board

## Quick start

```
./setup.sh                 # checks deps, links regmon-console + swdd
swdd &                     # start the SWD daemon (serves /tmp/swdd-cmd.sock)
regmon-console             # launch the console
```

The console shows "No target" until a probe + chip are connected.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `RE_RUN_AGENT` | `build` | opencode agent (`build` = act, `plan` = think) |
| `RE_OPENCODE` | `opencode` | opencode executable |
| `RE_AI_TIMEOUT` | `120` | seconds to wait for the AI |
| `RE_UPLOAD_REPO` | *(empty)* | optional git repo for snapshot uploads |

## Databases

Four SVD databases ship with the repo (`databases/`): STM32F051, STM32F103,
STM32F407, STM32L0xx. The chip-detector picks the right one automatically.
Custom databases (curated, errata-baked knowledge for other families) are paid
add-ons — the software stays free.

## License

GPL (see COPYING). Software is free; custom knowledge databases are paid
add-ons.
