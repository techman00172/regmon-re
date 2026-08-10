# Regmon-RE — Requirements & Support

_Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING._

_What you need to run Regmon-RE, and the support model._

## System requirements

- **Linux** or **FreeBSD** (any recent distro). Regmon-RE is keyboard-operated
  with no audio/voice — nothing to configure there.
- **python3** with **tkinter** — the console is a Tkinter GUI.
  - Arch: `sudo pacman -S tk`
  - Debian/Ubuntu: `sudo apt install python3-tk`
  - FreeBSD: `sudo pkg install python3 py311-tkinter`
- **opencode** — the AI agent (https://opencode.ai). This is the brain.
- An **AI account** — DeepSeek works great and is cheap; a free DeepSeek Flash
  account via OpenRouter also works.
- **Fossil** (optional) — only if you want to browse the repo the manual way.
  opencode can do all Fossil operations for you.
- **stlink** (the ST-Link host tools) — needed to **flash/program** the chip
  (`st-flash`). Regmon-RE itself talks to the probe through its own bundled
  `swdd`, but when you want to write new firmware you use st-flash.
  - Arch: `sudo pacman -S stlink`
  - Debian/Ubuntu: `sudo apt install stlink-tools`
  - FreeBSD: `sudo pkg install stlink`
  - (Install via opencode if you're not sure — it knows how.)

## Hardware (the bench)

- an **SWD debug probe** — ST-Link or clone (a few dollars; clones work)
- a target **STM32 board** (or other supported ARM Cortex-M)
- wiring: SWDIO, SWCLK, GND, 3V3 from the probe to the chip's SWD header

## Bundled with the repo

Everything needed to talk to the chip ships in the repo:

| Path | What it is |
|---|---|
| `swdcom/swdd` | the SWD debug daemon (reads the chip via the probe) |
| `databases/STM32F051.db` | SVD register database (F0) |
| `databases/STM32F103.db` | SVD register database (F1) |
| `databases/STM32F407.db` | SVD register database (F4) |
| `databases/STM32L0xx.db` | SVD register database (L0) |
| `databases/stm32f0xx-rm.db` | reference manual extracts |
| `setup.sh` | dependency check + linking of the console and swdd |

No vendor tooling, no installation of SDKs, no licenses.

## How it runs (the architecture)

```
SWD probe → swdd daemon (Unix socket /tmp/swdd-cmd.sock)
         → regmon-console (Tkinter GUI)
         → AI analysis via `opencode run` (your configured AI)
```

The console reads registers live over the socket, decodes them against the SVD
database, and hands questions/analysis to opencode for the AI verdict.

## Support model

There is no support team. There doesn't need to be — the user has opencode and
an AI. To get help:

- **"How do I use Regmon?"** → ask opencode. It reads this repo's docs and can
  teach you, run a course, or answer any question.
- **"Something's not working"** → describe the symptom to opencode; it can read
  the code, check the socket, inspect the databases, and diagnose.
- **"Set up a new chip"** → opencode can guide the SVD database addition.

The AI is the manual, the tutor, and the support desk — for peanuts.
