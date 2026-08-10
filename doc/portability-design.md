# regmon-re — standalone register monitor + AI

_Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING._

_2026-08-10. Terry's vision: the keyboard-operated register monitor
(reverse-engineering console) as a standalone product anyone can run, with the
AI provided by **opencode** — free, installable everywhere. No fossil crew, no
gateway, no voice/TTS (that's ZorgSpeech, a separate tool)._

## What this is

A Tkinter GUI register monitor. Press keys / click, talk to a chip over an SWD
debug probe via the bundled `swdd` daemon, read live registers against SVD
databases, and get AI analysis — with opencode as the agent.

## The AI design (Terry's decision)

The AI agent comes from **opencode**. Regmon RE shells out to:

```
opencode run --agent build "<prompt>"
```

opencode handles "which AI" (local model or cloud — e.g. DeepSeek). Regmon RE
doesn't need to know. Fallback: the fossilcrew gateway (koda) when present on a
FossilCon box.

## Components

| Piece | Source | Role |
|---|---|---|
| `scripts/regmon-console.py` | ported from swdai | the GUI + logic |
| `scripts/chip-detect.py` | ported from swdai | IDCODE → chip family → SVD DB |
| `scripts/chip-assist.py` | ported from swdai | auto-identify unknown chips |
| `swdcom/swdd` | bundled binary | SWD debug daemon (cmd socket) |
| `swdcom/regmon-analyze.py` | ported, AI→opencode | Analyse Reg |
| `swdcom/regmon-program-analyze.py` | ported, AI→opencode | Analyse Prog |
| `databases/*.db` | 4 shipped | SVD register databases |

## Changes made for standalone

- AI backend: fossilcrew gateway → `opencode run` (gateway kept as fallback).
- DB paths: `~/fossil/swdai/...` → repo-local `databases/`.
- Snapshot save: `~/fossil/schematics` → repo `pics/`; upload/chat optional
  via `RE_UPLOAD_REPO`/`RE_CHAT_SEND_URL` (empty = disabled).
- Koda GPU-mode button: hidden when `koda-mode.sh` absent.
- Flash dumps: repo-local `flash-dumps/`.
- Added missing `subprocess` import to both analyzers.

## Build order (status)

1. ✅ Port console + helpers + swdd + 4 databases.
2. ✅ AI backend → opencode (tested: both analyzers + console ask).
3. ✅ setup.sh (opencode + tkinter + probe check, links binaries).
4. ✅ README.
5. ⏳ End-to-end on real hardware (Terry's bench).
6. ⏳ `fossil git export` → GitHub when clean.

## Monetisation (standing idea)

Software free (MIT); paid add-ons are **custom curated databases** for
chip families not shipped (labour = errata-baked, tested knowledge). Once
funded, released to everyone.
