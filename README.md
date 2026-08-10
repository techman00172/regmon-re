# regmon-re

**Standalone voice-to-AI.** Press a key, speak, and your words go to an AI —
whichever AI your **opencode** is configured for (local model or cloud).

This is the standalone portability pass of ZorgSpeech, decoupled from the
FossilCon crew: no fossil server, no gateway, no tmux/zorgstudio coupling.

## How it works

```
press i/d → record voice → whisper transcribes → opencode run "text"
```

The AI agent comes from **opencode** — free, installable everywhere. Regmon RE
doesn't need to know which AI you use; opencode handles that.

## Requirements

- **opencode** — https://opencode.ai (the AI agent)
- **whisper** transcription: run `./setup.sh` (bundles `whisper-cli` + `wav2md`,
  downloads the ~490MB model)
- **arecord** (ALSA) for recording

## Usage

```
./scripts/zorgspeech            # or install: ./setup.sh
```

Keys:
- **i** — record (instruct mode)
- **d** — record (direct mode)
- **i/d again** — stop, transcribe, send to opencode
- after "Done": **d** clears back to idle

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `RE_RUN_AGENT` | `build` | opencode agent (`build` = act, `plan` = think) |
| `RE_OPENCODE` | `opencode` | opencode executable |
| `RE_AUDIO_DEV` | auto-probe | ALSA capture device (e.g. `hw:0,0`) |
| `RE_AUDIO_FMT/RATE/CH` | S16_LE / 16000 / 1 | recording format |
| `RE_INTERPRET_CMD` | *(empty)* | optional pre-processing command; transcript is piped through it before being sent |
| `WAV2MD` | `wav2md` | transcription wrapper |

## License

GPL (see COPYING). Software is free; custom knowledge databases are paid
add-ons (see `doc/portability-design.md`).
