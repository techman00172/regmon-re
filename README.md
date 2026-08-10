# Regmon-RE — version 4.0.0

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
2. **Get an AI account** — DeepSeek works great (cheap), or use a free
   DeepSeek Flash account via OpenRouter if you don't have credits yet
3. **Point opencode at DeepSeek** — `opencode` with your DeepSeek API key
   (or OpenRouter). Two minutes.
4. **Open the repo** — `fossil open regmon-re.fossil` (or just tell opencode
   where `regmon-re.fossil` is)
5. **Tell opencode: "get Regmon RE working for me"** — and that's it.

opencode will:
- check your system has python3 + tkinter + a working audio-free setup
- build or link `swdd` (the SWD debug daemon, bundled in the repo)
- verify the SVD databases are present
- launch Regmon-RE for you

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

GPL (see COPYING in the repo). Free software. Custom curated databases for
other chip families are available as paid add-ons — the software stays free.
