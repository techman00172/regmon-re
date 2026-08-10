# regmon-re — standalone voice-to-AI (ZorgSpeech portability pass)

_2026-08-10. Terry's idea: spin ZorgSpeech off from the FossilCrew/FossilCon
stack into a standalone tool anyone can run against ANY AI — local or cloud.
Design decision (2026-08-10, Terry): the AI agent comes from **opencode** —
everyone can install opencode, so Regmon RE will use opencode's agent._

## What this is

ZorgSpeech today is a voice-to-AI TUI: press a key → record voice → whisper
transcribes → (optional) an interpreter restructures it → the text is sent to
the AI. It currently only talks to opencode via `tmux send-keys` in the
FossilCon zorgstudio layout, and its interpreter only knows the fossilcrew
gateway (koda).

The portability pass makes it **standalone**: anyone with opencode installed
can run it against whichever AI opencode is configured for — local model or
cloud — with no fossil crew, no gateway, no tmux/zorgstudio coupling.

## The core design: opencode is the agent

opencode is free, installable everywhere, and has a non-interactive mode:

```
opencode run "text"
```

So the send step is simply:

```
record → whisper → opencode run "$transcript"
```

opencode handles "which AI" (local model, DeepSeek, whatever the user
configured). Regmon RE does not need to know. This replaces the whole tmux
send-keys mechanism and removes any need for us to wire API keys.

### Decisions

1. **Agent choice** — opencode has agents (e.g. `build`, `plan`). Configurable
   with a sensible default:
   - `RE_RUN_AGENT` env var, default `build` (dictation → act).
   - `plan` for "think about this first".
   - `opencode run --agent NAME "text"` if supported, else the default agent.
2. **Non-interactive** — standalone uses `opencode run` (returns a response),
   NOT the interactive TUI/tmux path. The FossilCon tmux version stays in the
   `zorgspeech` repo unchanged.
3. **Interpreter step** — optional. For v1, drop `zs-interpret` (koda-specific);
   opencode itself restructures well. Keep a hook (`RE_INTERPRET_CMD`) for
   anyone who wants their own pre-processing.
4. **Audio device** — `RE_AUDIO_DEV` (default `hw:0` auto-probe fallback), not
   hardcoded to `hw:StudioTM`.

## Portability changes from current zorgspeech

| Concern | Current | Standalone (regmon-re) |
|---|---|---|
| Send | `tmux send-keys -t :0.0` | `opencode run` (+ optional `--agent`) |
| Interpreter | koda gateway (`zs-interpret`) | dropped / `RE_INTERPRET_CMD` hook |
| Audio | `hw:StudioTM` | `RE_AUDIO_DEV` (auto-probe) |
| Install | `setup.sh` | `setup.sh` (whisper + whisper-cli + wav2md + opencode check) |
| Databases | Fossil SVD DBs (Regmon-specific) | optional plain knowledge files |

## Install / requirements

- **opencode** (user installs; Regmon RE checks for it and says so if missing)
- **whisper** transcription (bundled `whisper-cli` + model download in setup.sh)
- **arecord** / ALSA or PulseAudio for recording
- Everything else is optional.

## Databases (the monetisation idea)

Standing separately: ship with a few **plain knowledge files** (text/CSV/Forth)
any AI can use as context. A paid custom database = curated, worked,
errata-baked knowledge — labour, not just data. Software stays free (GPL);
databases are the paid add-ons, released to everyone once funded.

## Build order

1. Port `zorgspeech` → `regmon-re` `scripts/zorgspeech` with the opencode
   send target + `RE_*` env vars.
2. `setup.sh` decoupled (check opencode, install whisper, probe audio).
3. Test end-to-end: record → whisper → `opencode run`.
4. README + env var docs.
5. `fossil git export` → GitHub when clean.

## Notes

- The FossilCon ZorgSpeech stays in its own `zorgspeech` repo; this is the
  **standalone product** version.
- GPL, simple, honest — "undersell and overdeliver".
