# Regmon-RE — version 4.1.0

_Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING._

**A register monitor for ARM microcontrollers, with an AI assistant.**

Regmon-RE talks to a chip over a cheap SWD debug probe and reads the silicon
directly — live registers, bitfields, flash contents — and it has an AI that
can analyse what the chip is doing. No vendor tooling. No lock-in.

This download contains:
- `README.md` — this file
- `regmon-re.fossil` — the repository (Fossil DVCS, the whole project)

## The five-step install — the easiest you'll ever see

You need opencode and an AI account. If you're reading this you almost certainly
already have the AI part. Regmon-RE's AI does the install for you.

1. **Install opencode** — https://opencode.ai (free, one command)
2. **Get an AI account** — DeepSeek works great and is cheap; or use a free
   model on OpenRouter's free tier (e.g. `openai/gpt-oss-20b:free`). A few
   dollars of OpenRouter credit is plenty.
3. **Point opencode at the AI** — `opencode` with your API key (DeepSeek or
   OpenRouter). Two minutes.
4. **Download Regmon-RE** — just the `regmon-re.fossil` file. Nothing else.
5. **Say the magic words** — tell opencode: *"read the install and do it"*
   (point it at the file you downloaded). That's it.

opencode will:
- create a directory for the repo, open the Fossil repo, and check everything out
- check your system has python3 + tkinter + a working audio-free setup
- build or link `swdd` (the SWD debug daemon, bundled in the repo)
- verify the SVD databases are present
- launch Regmon-RE for you

> **You don't need to know anything about Fossil.** opencode does the Fossil
> part. You just download one file and tell it to go.
>
> **Note:** during setup opencode will ask permission to run commands (setup.sh,
> python compile checks, launching the console). Approve them as it goes —
> that's the AI doing the install for you.

## What you need on the bench

- a **Linux** PC (any recent distro)
- an **SWD debug probe** — ST-Link or clone (a few dollars)
- a target **STM32 board** (or other ARM Cortex-M supported chip)
- your chip wired to the probe (SWDIO, SWCLK, GND, 3V3)

## Using it

- plug in the probe + board
- the console auto-detects the chip (reads the silicon's IDCODE)
- browse registers, **Analyse Reg**, **Analyse Prog**, **Strings**, **Save Flash**
- **Ask AI** — any register-level question, answered by your AI

## What's in the repo

```
databases/        SVD register databases (F051, F103, F407, L0xx + RM ref)
scripts/          the console + chip detection
swdcom/           swdd daemon + the AI analysis tools
setup.sh          dependency check + linking
```

## License

MIT (see COPYING in the repo). Free software. Custom curated databases for
other chip families are available as paid add-ons — the software stays free.

## Tested

Validated end-to-end in an isolated container (`test/test-harness.sh`, Podman):
dependencies, databases, python compile, console launch (headless), setup.sh,
and the AI via OpenRouter's free tier — all 24 checks pass. Wipe-and-repeat:
each run builds a fresh container and discards it.
