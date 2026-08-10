#!/usr/bin/env python3
# Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING.
"""regmon-analyze.py — local AI analysis of a live peripheral register.

Reads a register's current value via the swdd daemon, gathers the SVD bitfield
structure, the reference-manual prose per bitfield, and the matching knowledge
base section, then asks a LOCAL model what the peripheral is configured to do.

  regmon-analyze.py TIM2_CR1                # read live value, analyse
  regmon-analyze.py TIM2_CR1 0x0001         # analyse a given value
  regmon-analyze.py --json TIM2_CR1         # emit the payload only (debug)

The model runs on the local Ollama gateway (default 127.0.0.1:11434).  A
fallback model list is used so a missing model never leaves Terry without an
answer.  DeepSeek is NOT required — this works fully offline.

Exit code 0 = verdict produced; 1 = local AI unavailable.
"""
import os
import sys
import json
import socket
import sqlite3
import subprocess
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, ".."))
SVD_DB = os.path.join(_REPO, "databases", "STM32F051.db")
RM_DB = os.path.join(_REPO, "databases", "stm32f0xx-rm.db")
KB_COOKBOOK = os.path.expanduser(
    "~/fossil/swdai/knowledge/pdfk/TIMERS/general-purpose_timer_cookbook.db")
SWDD_SOCK = "/tmp/swdd-cmd.sock"

# Chip-aware SVD database: identify the connected chip from its IDCODE and use
# the matching database (the IDCODE covers the family class, not the exact part).
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "chip_detect",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "chip-detect.py"))
    _chip = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_chip)
    _db = _chip.chip_db_path()
    if _db:
        SVD_DB = _db
except Exception:
    pass

# Primary: the fossilcrew gateway's Coder agent (Qwen3.6-27B-A3B-Coder) —
# already warm in GPU, OpenAI-compatible at /v1/chat/completions.  One model
# to maintain, no second 20 GB copy.  Fallback: the local Ollama gateway.
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:9001")
GATEWAY_MODEL = os.environ.get("GATEWAY_MODEL", "koda")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("AI_MODEL", "qwen2.5-coder:32b")
OLLAMA_FALLBACKS = ["qwen2.5-coder:32b", "qwen2.5:14b",
                    "Qwen3.6-35B-A3B-UD-Q4_K_S:latest", "phi3"]


def read_mem_word(addr):
    """Read a 32-bit word via the swdd daemon."""
    if isinstance(addr, str):
        addr = int(addr.replace("$", "0x"), 16)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(SWDD_SOCK)
        s.sendall((f"mem {addr:x} 4\n").encode())
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
    except Exception:
        return None
    for line in data.decode(errors="replace").split("\n"):
        line = line.strip()
        if not line or line.startswith("MEM") or line == ".":
            continue
        if ":" not in line:
            continue
        try:
            parts = line.split(":")[1].strip().split()
            val = 0
            for i, p in enumerate(parts):
                val |= int(p, 16) << (8 * i)
            return val
        except (ValueError, IndexError):
            continue
    return None


def reg_address(peripheral, register):
    """Look up a register's address from the SVD database."""
    if not os.path.isfile(SVD_DB):
        return None
    try:
        conn = sqlite3.connect(SVD_DB)
        row = conn.execute(
            "SELECT r.address FROM register r "
            "JOIN peripheral p ON r.peripheral_name = p.name "
            "WHERE p.name = ? AND r.name = ?",
            (peripheral.upper(), register.upper())).fetchone()
        conn.close()
        if row and row[0]:
            a = row[0].strip()
            if a.startswith("0x") or a.startswith("$"):
                return int(a.replace("$", "0x"), 16)
            return int(a, 16)
    except Exception:
        pass
    return None


def rm_prose(register_name, field_name):
    """RM prose for a bitfield (same lookup as regmon-tui, incl. generic-y)."""
    if not os.path.isfile(RM_DB):
        return None
    try:
        conn = sqlite3.connect(RM_DB)
        row = conn.execute(
            "SELECT description FROM bitfields WHERE register_name = ? AND name = ?",
            (register_name, field_name)).fetchone()
        if not row or not (row[0] and row[0].strip()):
            generic = ''.join(ch for ch in field_name if not ch.isdigit())
            for cand in ([generic, generic + 'y'] if generic else []):
                if cand and cand != field_name:
                    row = conn.execute(
                        "SELECT description FROM bitfields "
                        "WHERE register_name = ? AND name = ?",
                        (register_name, cand)).fetchone()
                    if row and row[0] and row[0].strip():
                        break
        conn.close()
        if row and row[0] and row[0].strip():
            return row[0].strip()
    except Exception:
        pass
    return None


def kb_prose(register_name):
    """Search the timer cookbook for prose mentioning the register or its bits.
    Returns a bounded excerpt to keep the prompt small."""
    if not os.path.isfile(KB_COOKBOOK):
        return ""
    out = []
    try:
        conn = sqlite3.connect(KB_COOKBOOK)
        for r in conn.execute(
                "SELECT section, text FROM paragraphs "
                "WHERE text LIKE ? LIMIT 6",
                ("%" + register_name.split("_")[-1] + "%",)):
            out.append((r[0], r[1].strip()))
        conn.close()
    except Exception:
        return ""
    # Also try the register name itself and generic CR1 words.
    key = register_name.split("_")[-1].upper()
    hits = [t for _, t in out if key in t.upper()]
    if not hits:
        return ""
    text = "\n".join(hits)
    return text[:1500]


def build_payload(peripheral, register, value, samples=None):
    """Assemble the analysis prompt from live value + SVD + RM + KB.
    samples: optional list of (interval_index, value) captured over time.
    When present, the payload shows the whole time-series and asks the AI to
    interpret what the CHANGES mean (a peripheral that toggles is in use)."""
    reg_name = f"{peripheral.upper()}_{register.upper()}"
    lines = [
        f"Peripheral: {peripheral.upper()}  Register: {register.upper()}  "
        f"Live value: 0x{value:08x} ({value})",
        "",
        "Bitfields (name, bits, current decoded value, RM meaning):",
    ]
    try:
        conn = sqlite3.connect(SVD_DB)
        rows = conn.execute(
            "SELECT name, bitWidth, bitOffset, description FROM field "
            "WHERE peripheral_name = ? AND register_name = ? ORDER BY bitOffset",
            (peripheral.upper(), register.upper())).fetchall()
        conn.close()
    except Exception:
        rows = []
    seen = set()
    fields = {}   # name -> (width, offset) for the time-series change detection
    for fname, width, offset, desc in rows:
        if fname in seen:
            continue  # SVD carries duplicate field rows; keep the first.
        seen.add(fname)
        fields[fname] = (width, offset)
        mask = (1 << width) - 1
        fval = (value >> offset) & mask
        meaning = ""
        prose = rm_prose(reg_name, fname) or (desc or "")
        # Extract the 'XX: meaning' line matching the current value, if any.
        for line in prose.split("\n"):
            line = line.strip()
            import re
            m = re.match(r"^([0-9A-Fa-f]{1,2}|0b[01]+|0x[0-9A-Fa-f]+):\s*(.+)$", line)
            if m:
                k = m.group(1).lower()
                try:
                    kval = int(k, 2) if k.startswith("0b") else \
                        int(k, 16) if k.startswith("0x") else \
                        int(k, 2) if set(k) <= {"0", "1"} else int(k)
                except ValueError:
                    continue
                if kval == fval:
                    meaning = m.group(2).strip()
                    break
        bits = f"{offset}" if width == 1 else f"[{offset+width-1}:{offset}]"
        if not meaning:
            meaning = prose.split("\n")[0][:100]
        lines.append(f"  {fname:<16} {bits:>9} = {fval:<6} {meaning[:120]}")
    kbtxt = kb_prose(reg_name)
    if kbtxt:
        lines.append("")
        lines.append("Knowledge base (timer cookbook, relevant section):")
        lines.append(kbtxt[:1500])
    lines.append("")
    if samples:
        # Time-series mode: show every captured sample and which bits moved.
        lines.append("This register was SAMPLED over time (one capture per cycle,"
                     " ~0.5s apart). All samples:")
        prev = None
        for idx, sval in samples:
            changed = ""
            if prev is not None and sval != prev:
                # which bits changed between this sample and the previous
                diff = sval ^ prev
                names = []
                for fname, (fwidth, foffset) in sorted(
                        fields.items(), key=lambda kv: kv[1][1]):
                    if diff & (((1 << fwidth) - 1) << foffset):
                        names.append(fname)
                changed = f"   <-- CHANGED from prev (bits: {', '.join(names) or '?'})"
            lines.append(f"  sample {idx+1}: 0x{sval:08x} ({sval}){changed}")
            prev = sval
        lines.append("")
        lines.append("The value CHANGES between samples. In your opinion, what is "
                     "this peripheral doing over time? Say whether it is active/"
                     "in use (e.g. a comparator being periodically enabled for a "
                     "measurement), which bits toggle, and why. Start 'In my opinion "
                     "this peripheral is ...'. 2-4 lines.")
    else:
        lines.append("In your opinion, what is this peripheral configured to do? "
                     "Answer in ONE line starting 'In my opinion this is a ...', "
                     "then 1-2 lines of the key reasoning naming which bits decide it.")
    return "\n".join(lines)


def _opencode_chat(prompt):
    """Standalone AI via `opencode run` — works against ANY AI the user
    configured (local or cloud).  Returns (text, "opencode") or (None, None)."""
    import shutil
    exe = os.environ.get("RE_OPENCODE", "opencode")
    agent = os.environ.get("RE_RUN_AGENT", "build")
    if not shutil.which(exe):
        return None, None
    try:
        proc = subprocess.run(
            [exe, "run", "--agent", agent, prompt],
            capture_output=True, text=True, timeout=int(os.environ.get("RE_AI_TIMEOUT", "120")))
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip(), "opencode"
    except Exception:
        pass
    return None, None


def _gateway_chat(prompt):
    """Call the fossilcrew gateway's Coder agent (OpenAI-compatible API).
    Returns (text, "koda") or (None, None)."""
    body = json.dumps({
        "model": GATEWAY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.2,
    }).encode()
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        text = (data.get("choices") or [{}])[0].get("message", {}).get(
            "content", "").strip()
        if text:
            return text, GATEWAY_MODEL
    except Exception:
        pass
    return None, None


def ollama_chat(prompt):
    """Call the local Ollama gateway. Tries the configured model then fallbacks."""
    tried = []
    models = [OLLAMA_MODEL] + [m for m in OLLAMA_FALLBACKS if m != OLLAMA_MODEL]
    for model in models:
        if model in tried:
            continue
        tried.append(model)
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.2},
        }).encode()
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            text = (data.get("message") or {}).get("content", "").strip()
            if text:
                return text, model
        except Exception:
            continue
    return None, None


def analyze(prompt):
    """Run the prompt. Primary (standalone): `opencode run` — any AI the user
    configured. Fallbacks: the fossilcrew gateway, then local Ollama."""
    text, model = _opencode_chat(prompt)
    if text:
        return text, model
    text, model = _gateway_chat(prompt)
    if text:
        return text, model
    text, model = ollama_chat(prompt)
    if text:
        return text, model
    return None, None


def main():
    args = sys.argv[1:]
    debug_json = False
    if "--json" in args:
        debug_json = True
        args.remove("--json")
    n_samples = 0
    if "--samples" in args:
        i = args.index("--samples")
        try:
            n_samples = int(args[i + 1])
        except (IndexError, ValueError):
            n_samples = 0
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2
    name = args[0].upper().replace(" ", "_")
    if "_" not in name:
        print(f"Expected PERI_REG, got: {name}", file=sys.stderr)
        return 2
    peripheral, register = name.split("_", 1)
    value = None
    if len(args) > 1:
        try:
            value = int(args[1], 0)
        except ValueError:
            value = None
    addr = reg_address(peripheral, register)
    if addr is None:
        print(f"Register not found in SVD: {name}", file=sys.stderr)
        return 2

    samples = None
    if n_samples > 1:
        # Capture the register N times (~0.5s apart) to catch a periodic
        # peripheral toggling (e.g. a comparator enabled only for a window).
        import time as _time
        samples = []
        for i in range(n_samples):
            v = read_mem_word(addr)
            if v is None:
                print("Could not read live value (swdd down?)", file=sys.stderr)
                return 1
            samples.append((i, v))
            _time.sleep(0.5)
        value = samples[-1][1]
    else:
        if value is None:
            value = read_mem_word(addr)
            if value is None:
                print("Could not read live value (swdd daemon down?)", file=sys.stderr)
                return 1

    payload = build_payload(peripheral, register, value, samples=samples)
    if debug_json:
        print(payload)
        return 0
    verdict, model = analyze(payload)
    if not verdict:
        print("(local AI unavailable — Ollama not responding)", file=sys.stderr)
        return 1
    print(f"{verdict}\n[model: {model}]")


if __name__ == "__main__":
    sys.exit(main())
