#!/usr/bin/env python3
# Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING.
"""Regmon-RE — clickable control for the Regmon display on the second monitor.

Lives on screen 0 (where the WM works). Lists every STM32 register from the SVD
database with its LIVE value, read via the swdd daemon. Clicking a register
sends SELECT to the Regmon control socket, which jumps the second-monitor
display to that register and expands its bitfields.

Values are polled at ~2 Hz; a register whose value changed is highlighted
yellow briefly (matching the Regmon display's change indication).

Usage:
  regmon-console.py                 # launch on current display
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
SVD_DB = os.path.join(_REPO_ROOT, "databases", "STM32F051.db")
SWDD_SOCK = "/tmp/swdd-cmd.sock"

# Chip detection: identify the connected STM32 from its IDCODE and load the
# matching SVD database.  The IDCODE covers a FAMILY CLASS (0x440 = F0/F051,
# 0x410 = F1/F103), not the exact part — the decoded name is the best available
# guess and is shown at the top of the window so Terry always sees which chip
# Regmon thinks it is looking at.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
_CHIP_INFO = None

try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "chip_detect",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "chip-detect.py"))
    _chip = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_chip)
except Exception:
    _chip = None
else:
    _CHIP_INFO = _chip.detect_chip()
    _db = _chip.chip_db_path(_CHIP_INFO)
    if _db:
        SVD_DB = _db

# Local AI gateway (fossilcrew, warm koda on GPU). Gateway-first per AGENTS.md.
GATEWAY_URL = "http://127.0.0.1:9001/v1/chat/completions"
GATEWAY_MODEL = "koda"

# Hard spec facts keyed by family class (from the official datasheets).  These
# pin the chip down so the AI can never hallucinate a clock ceiling or a
# flash/RAM size.  Max SYSCLK is the headline; others added as needed.
CHIP_SPEC_FACTS = {
    "STM32F0": "max SYSCLK 48 MHz (datasheet limit)",
    "STM32F1": "max SYSCLK 72 MHz (datasheet limit)",
    "STM32F2": "max SYSCLK 120 MHz (datasheet limit)",
    "STM32F3": "max SYSCLK 72 MHz (datasheet limit)",
    "STM32F4": "max SYSCLK 168 MHz @ 3.3V (2 flash wait states); 150 MHz at low voltage. NOT 480 MHz — 480 MHz is the H7 family.",
    "STM32L0": "max SYSCLK 32 MHz (datasheet limit)",
    "STM32L1": "max SYSCLK 32 MHz (datasheet limit)",
    "STM32L4": "max SYSCLK 80 MHz (datasheet limit)",
    "STM32L5": "max SYSCLK 110 MHz (datasheet limit)",
    "STM32F7": "max SYSCLK 216 MHz (datasheet limit)",
    "STM32G0": "max SYSCLK 64 MHz (datasheet limit)",
    "STM32G4": "max SYSCLK 170 MHz (datasheet limit)",
    "STM32H7": "max SYSCLK 480 MHz (datasheet limit)",
}

# Clock/UART/GPIO recipe facts keyed by family class.  The standalone release
# asks opencode (not the local gateway), so it cannot run the compute tools —
# the same PLL/baud/GPIO math is baked into the prompt instead, so opencode
# answers from real formulas rather than guesswork.
CHIP_RECIPES = {
    "STM32F0": ("PLL (F0, no PLLN): SYSCLK = PLL_IN * PLLMUL / PLLDIV, PLL_IN = "
                "HSI8/2 = 4 MHz or HSE; max 48 MHz. UART: BRR = fCK/(16*baud), "
                "USART1 on APB2, USART2 on APB1."),
    "STM32F1": ("PLL (F1, no PLLN): SYSCLK = PLL_IN * PLLMUL, PLL_IN = HSI8/2 or "
                "HSE, PLLMUL 2..16; max 72 MHz. UART: BRR = fCK/(16*baud), "
                "USART1 on APB2, USART2/3 on APB1. GPIO CRL/CRH: each pin is "
                "[CNF][MODE]; CNF depends on MODE — input: 0=analog,1=floating,"
                "2=pullup; output: 0=pushpull,1=opendrain,2=AF pushpull,"
                "3=AF opendrain; MODE 0=input,1=out10MHz,2=out2MHz,3=out50MHz."),
    "STM32F2": ("PLL: SYSCLK = (PLL_IN/PLLM)*PLLN/PLLP; max 120 MHz. UART: "
                "BRR = fCK/(16*baud), USART1 on APB2, USART2/3 on APB1."),
    "STM32F3": ("PLL (F3, no PLLN): SYSCLK = PLL_IN * PLLMUL; max 72 MHz. "
                "UART: BRR = fCK/(16*baud)."),
    "STM32F4": ("PLL (F4): SYSCLK = (PLL_IN/PLLM)*PLLN/PLLP; PLLM bits5:0, "
                "PLLN bits14:6, PLLP bits17:16 (0=2,1=4,2=6,3=8); VCO in 1-2 MHz, "
                "VCO out 100-432 MHz; max 168 MHz. Wait states (2.7-3.6V): 0<=30, "
                "1<=60, 2<=90, 3<=120, 4<=150, 5<=168 MHz. 100MHz from 8MHz HSE = "
                "PLLM=8,PLLN=200,PLLP=0; 20MHz = PLLM=8,PLLN=120,PLLP=0b10. UART: "
                "BRR=fCK/(16*baud), USART1/6 on APB2, USART2/3/4/5 on APB1."),
    "STM32L0": ("PLL (L0): SYSCLK = PLL_IN * PLLMUL / PLLDIV; max 32 MHz; MSI is "
                "the low-power source. UART: BRR = fCK/(16*baud), USART1 on APB2, "
                "USART2/4/5 on APB1. GPIO uses AFSEL field names."),
    "STM32L1": ("PLL: max 32 MHz. UART: BRR = fCK/(16*baud)."),
    "STM32L4": ("PLL: SYSCLK = (PLL_IN/PLLM)*PLLN/PLLR; max 80 MHz (120 L4R/S); "
                "MSI is the low-power source. UART: BRR = fCK/(16*baud)."),
    "STM32L5": ("PLL: SYSCLK = (PLL_IN/PLLM)*PLLN/PLLR; max 110 MHz. UART: "
                "BRR = fCK/(16*baud)."),
    "STM32F7": ("PLL: SYSCLK = (PLL_IN/PLLM)*PLLN/PLLP; max 216 MHz (overdrive "
                "above ~180). UART: BRR = fCK/(16*baud)."),
    "STM32G0": ("PLL: SYSCLK = (PLL_IN/PLLM)*PLLN/PLLR; max 64 MHz. UART: "
                "BRR = fCK/(16*baud), USART1/2 on APB2, USART3/4 on APB1."),
    "STM32G4": ("PLL: SYSCLK = (PLL_IN/PLLM)*PLLN/PLLR; max 170 MHz. UART: "
                "BRR = fCK/(16*baud)."),
    "STM32H7": ("PLL: SYSCLK = (PLL_IN/PLL1M)*PLL1N/PLL1P; max 480 MHz. UART: "
                "BRR = fCK/(16*baud)."),
}

def _ask_system_prompt():
    """System prompt for Ask AI, including the DETECTED chip identity so the
    agent never has to guess or doubt the MCU (blogher #84: it once doubted the
    chip and invented MCU_CR/MCU_SR, which do not exist on any STM32)."""
    chip = "STM32F051"
    spec = ""
    recipe = ""
    if _CHIP_INFO and _CHIP_INFO.get("dev_id") is not None:
        chip = _CHIP_INFO["name"]
        spec = CHIP_SPEC_FACTS.get(_CHIP_INFO.get("chip", ""), "")
        recipe = CHIP_RECIPES.get(_CHIP_INFO.get("chip", ""), "")
    spec_line = f"\nHARD SPEC FACT: the connected chip is {chip}. {spec}" if spec else ""
    recipe_line = f"\nCLOCK/UART/GPIO RECIPE ({_CHIP_INFO.get('chip','')}): {recipe}" if recipe else ""
    return f"""You are an embedded software engineer helping with a live {chip}
(connected via SWD — Regmon console). Answer the user's register-level question
concisely and precisely.
{spec_line}
{recipe_line}
The connected chip is {chip}. The SVD register addresses and bitfields in the
context belong to THIS chip — do not doubt the chip identity or suggest a
different family unless the SVD data genuinely contradicts it.

Rules:
- Answer directly, no preamble or greetings.
- If asked about a chip limit (clock, flash, RAM), use the HARD SPEC FACT above
  as the ceiling. The datasheet always states these limits — never claim the
  datasheet omits them, and never substitute another family's numbers.
- If asked how to set a clock speed, a UART baud, or a GPIO pin mode, use the
  CLOCK/UART/GPIO RECIPE above — it gives the exact PLL/BRR/CNF-MODE math for
  this chip family. Compute from that recipe; never invent a formula or field
  encoding. Give the register values to write and the sequence (e.g. set flash
  wait states before raising the clock; disable USART before writing BRR).
- If the context includes SVD bitfields for a register, use ONLY those exact
  field names and bit positions. Do NOT invent or substitute other STM32 field
  names (e.g. no MULSEL/DIVSEL/PREDIV unless they are actually listed).
- Do NOT invent registers. STM32F0/F1 have NO MCU_CR/MCU_SR registers; chip
  identity is established by the DBGMCU IDCODE, not by a register name.
- Give the register/bitfield names and their exact SVD values when relevant.
- A selected register may be mentioned as context — relate to it ONLY if the
  question is actually about that register; otherwise answer the question asked
  (this is a free-form ask box, not a register-analysis request).
- Mention what to set (register + value) and why.
- Keep it under ~12 lines.
- If the question is off-topic or unclear, say so in one line."""


def _svd_bitfields(group, reg):
    """Return the REAL SVD bitfields of a register as text, or ''."""
    if not group or not reg:
        return ""
    try:
        conn = sqlite3.connect(SVD_DB)
        rows = conn.execute(
            "SELECT name, bitOffset, bitWidth, description FROM field "
            "WHERE peripheral_name=? AND register_name=? ORDER BY bitOffset",
            (group.upper(), reg.upper())).fetchall()
        conn.close()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = [f"{group.upper()}_{reg.upper()} SVD bitfields:"]
    for name, off, wid, desc in rows:
        lines.append(f"  {name:10s} bits[{off}+{wid}] {desc or ''}".rstrip())
    return "\n".join(lines)


RE_OPENCODE = os.environ.get("RE_OPENCODE", "opencode")
RE_RUN_AGENT = os.environ.get("RE_RUN_AGENT", "build")
RE_AI_TIMEOUT = int(os.environ.get("RE_AI_TIMEOUT", "120"))

def _opencode_ask(question, register_context):
    """Send the question to the AI via `opencode run` (standalone, any AI the
    user configured).  Returns the answer text."""
    prompt = _ask_system_prompt() + \
        f"\n\nSelected register: {register_context}\n\nQuestion: {question}"
    try:
        proc = subprocess.run(
            [RE_OPENCODE, "run", "--agent", RE_RUN_AGENT, prompt],
            capture_output=True, text=True, timeout=RE_AI_TIMEOUT)
        if proc.returncode != 0:
            return f"(opencode failed: {proc.stderr.strip() or proc.stdout.strip()})"
        out = proc.stdout.strip()
        return out or "(no answer)"
    except FileNotFoundError:
        return "(opencode not found — install from https://opencode.ai)"
    except subprocess.TimeoutExpired:
        return "(opencode timed out)"
    except Exception as e:
        return f"(AI unavailable: {e})"


def _gateway_ask(question, register_context):
    """Fallback AI: the local fossilcrew gateway (koda).  Used when opencode is
    not configured but the gateway is present (Terry's FossilCon box)."""
    messages = [
        {"role": "system", "content": _ask_system_prompt()},
        {"role": "user", "content": f"Selected register: {register_context}\n\nQuestion: {question}"},
    ]
    payload = json.dumps({
        "model": GATEWAY_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": 0.3,
        "max_tokens": 512,
    }).encode()
    try:
        req = urllib.request.Request(GATEWAY_URL, data=payload)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip() \
            or "(no answer)"
    except Exception as e:
        return f"(AI unavailable: {e})"


def _ai_ask(question, register_context):
    """Primary AI is opencode run; falls back to the local gateway."""
    try:
        proc = subprocess.run([RE_OPENCODE, "--version"],
                              capture_output=True, timeout=10)
        if proc.returncode == 0:
            return _opencode_ask(question, register_context)
    except Exception:
        pass
    return _gateway_ask(question, register_context)

# Snapshot settings.  Standalone: snapshots save to the repo's own pics/ dir.
SCREENSHOTS_DIR = os.path.join(_REPO_ROOT, "pics")
UPLOAD_REPO = os.environ.get(
    "RE_UPLOAD_REPO",
    os.path.join(os.path.dirname(_REPO_ROOT), "regmon-re-git"))   # regmon-re git mirror
RAW_URL = os.environ.get(
    "RE_RAW_URL",
    "https://raw.githubusercontent.com/techman00172/regmon-re/master/pics")
# Optional fossil chatroom for posting analysis (fossilCon-only; empty = disabled).
CHAT_SEND_URL = os.environ.get("RE_CHAT_SEND_URL", "")
CHAT_REFERER = os.environ.get("RE_CHAT_REFERER", "")

BG = "#0d0d1a"
FG = "#d0d0e0"
ACCENT = "#ff6666"
CHANGE = "#ffcc00"
FONT = ("JetBrains Mono", 14)
FONT_BOLD = ("JetBrains Mono", 14, "bold")
FONT_SMALL = ("JetBrains Mono", 12)

# Semantic version.  v2.0.0 = the Detect Chip era: chip-class detection,
# chip-aware SVD databases, Strings, Analyse Reg and Analyse Prog.  Major bump
# over the unversioned pre-detect console (informally the v1 era).  The Fossil
# tag 'v2.0.0' marks that release; bump MINOR for new features, PATCH for fixes.
# v2.1.0 = the RE (reverse-engineering) era in swdai.
# v4.0.0 = the STANDALONE Regmon-RE release: a distinct tree with the
# FossilCon-only pieces removed (Assist, Koda GPU toggle) and the AI provided
# by opencode (any model the user configures).  A new major because this is a
# different product from the swdai console lineage.
VERSION = "4.1.0"
APP_NAME = "Regmon-RE"

POLL_PERIOD = 0.5  # 2 Hz

# Auto chip-detect: how often to probe for a board swap (seconds).  Only runs
# while swdd is active; skips entirely when swdd is off/disconnected.
AUTO_DETECT_PERIOD = 30


def swdd_cmd(cmd, timeout=2):
    """Send a command to the swdd daemon socket; return raw response or None."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SWDD_SOCK)
        s.sendall((cmd + "\n").encode())
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        return data.decode(errors="replace")
    except Exception:
        return None


def read_mem_word(addr):
    """Read a 32-bit word via swdd. addr may be int or '0x...' string."""
    if isinstance(addr, str):
        addr = int(addr.replace("$", "0x"), 16)
    resp = swdd_cmd(f"mem {addr:x} 4")
    if not resp:
        return None
    for line in resp.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("MEM") or line == ".":
            continue
        if ":" not in line:
            continue
        parts = line.split(":")[1].strip().split()
        val = 0
        for i, p in enumerate(parts):
            val |= int(p, 16) << (8 * i)
        return val
    return None


# Flash-size register addresses, keyed by family class (also in chip-assist).
FLASH_SIZE_ADDRS = {
    "STM32F0": 0x1FFFF7CC, "STM32F1": 0x1FFFF7E0, "STM32F4": 0x1FFF7A22,
    "STM32F7": 0x1FF0F442, "STM32H7": 0x1FF1E880, "STM32L0": 0x1FF8007C,
    "STM32L1": 0x1FF8004C, "STM32L4": 0x1FFF75CC, "STM32G0": 0x1FFF75CC,
    "STM32G4": 0x1FFF75CC,
}


def flash_size_kb():
    """Read the flash size (KB) from the per-family register.  Falls back to
    probing every known address and taking the first sane value."""
    fam = (_CHIP_INFO or {}).get("chip")
    addrs = []
    if fam and fam in FLASH_SIZE_ADDRS:
        addrs.append(FLASH_SIZE_ADDRS[fam])
    addrs += [a for a in set(FLASH_SIZE_ADDRS.values()) if a not in addrs]
    for a in addrs:
        v = read_mem_word(a)
        if v is None:
            continue
        kb = v & 0xFFFF
        if 8 <= kb <= 8192:  # sane flash range (8KB .. 8MB)
            return kb
    return None


def dump_flash_to_file(path, progress=None):
    """Read the chip's entire flash (0x08000000 .. flash-size) into a binary
    file, in 256-byte chunks (swdd's per-read limit).  Returns the number of
    bytes written, or None on failure.  `progress` is called with a percentage
    (0..100) after each chunk, like scan_flash_strings()."""
    kb = flash_size_kb()
    if kb is None:
        return None
    total = kb * 1024
    addr = 0x08000000
    end = 0x08000000 + total
    chunk = 0
    nchunks = (total + 255) // 256
    with open(path, "wb") as f:
        while addr < end:
            n = min(256, end - addr)
            resp = swdd_cmd(f"mem {addr:x} {n}", timeout=8)
            if resp:
                for line in resp.strip().split("\n"):
                    line = line.strip()
                    if not line or line.startswith("MEM") or line == ".":
                        continue
                    if ":" not in line:
                        continue
                    for b in line.split(":")[1].strip().split():
                        try:
                            f.write(bytes([int(b, 16)]))
                        except ValueError:
                            return None
            addr += n
            chunk += 1
            if progress:
                progress(int(100 * chunk / nchunks))
    return total


def scan_flash_strings(progress=None):
    """Scan the chip's flash for printable ASCII runs (like the 'strings' tool)
    and return (list_of_strings, addresses).  Reads flash in 256-byte chunks
    (swdd's per-read limit) starting at 0x08000000 for the full flash size.

    If `progress` is given it is called with a percentage (0..100) after each
    chunk so the caller can show live progress on a long scan (1MB flash takes
    a while at 256 bytes/read)."""
    kb = flash_size_kb()
    if kb is None:
        return [], [], 0, b""
    total = kb * 1024
    blob = bytearray()
    bytes_read = 0
    addr = 0x08000000
    end = 0x08000000 + total
    chunk = 0
    nchunks = (total + 255) // 256
    while addr < end:
        n = min(256, end - addr)
        resp = swdd_cmd(f"mem {addr:x} {n}", timeout=5)
        if resp:
            for line in resp.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("MEM") or line == ".":
                    continue
                if ":" not in line:
                    continue
                for b in line.split(":")[1].strip().split():
                    blob.append(int(b, 16))
                    bytes_read += 1
        addr += n
        chunk += 1
        if progress:
            progress(int(100 * chunk / nchunks))

    # extract runs of printable ASCII (space..~), then keep the "wordy" ones
    runs = []
    cur = []
    cur_addr = None
    for i, b in enumerate(blob):
        if 32 <= b < 127:
            if not cur:
                cur_addr = 0x08000000 + i
            cur.append(chr(b))
        else:
            if len(cur) >= 4:
                runs.append((cur_addr, "".join(cur)))
            cur = []
    if len(cur) >= 4:
        runs.append((cur_addr, "".join(cur)))

    # keep strings that are mostly word-like: letters dominate, with digits
    # allowed (STM32, USART1) but not pure symbol runs.  Reject instruction-
    # byte artifacts (e.g. 'iAbAi', 'vA8pG' from the Mecrisp dictionary): a
    # single token with mixed case (uppercase not leading) is almost certainly
    # code bytes — real Forth dictionary words are lowercase (optionally with
    # leading digits like '2dup' or symbols like '?dup'/'-rot').  Multi-word
    # strings (spaces, e.g. the banner or flash error messages) are kept as-is.
    words = []
    seen = set()
    for a, s in runs:
        letters = sum(1 for c in s if c.isalpha())
        alnum = sum(1 for c in s if c.isalnum())
        if len(s) == 0:
            continue
        ratio = alnum / len(s)
        if ratio < 0.7 or letters < 2:
            continue
        # multi-word messages (contain a space) are kept as-is
        if " " in s:
            if s not in seen:
                seen.add(s)
                words.append((a, s))
            continue
        # single token: strip leading digits/symbols, then require PURE
        # lowercase — real Forth dictionary words are lowercase.  Mixed case or
        # all-caps = byte noise (instruction bytes happen to look like ASCII).
        # Also drop trailing two-char fragments like '8h'/'pG' that are real
        # word + following instruction byte merged (e.g. '2-rot8h').
        core = s.lstrip("0123456789?@+-<>=!")
        if not core.islower():
            continue
        if len(core) >= 2 and core[-2:][0].isdigit() and core[-1].isalpha():
            continue
        if s not in seen:
            seen.add(s)
            words.append((a, s))
    return words, kb, bytes_read, bytes(blob[:512])


def hex_dump(data, base=0x08000000, width=16):
    """Format bytes as a classic xxd-style hex dump (address  hex  ascii)."""
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexs = " ".join("%02x" % b for b in chunk)
        hexs = hexs.ljust(width * 3 - 1)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%08x:  %s  %s" % (base + off, hexs, asc))
    return "\n".join(lines)


def load_registers():
    """Load {peripheral: [ (regname, address), ... ]} from the SVD database."""
    regs = {}
    conn = sqlite3.connect(SVD_DB)
    rows = conn.execute(
        "SELECT p.name, r.name, r.address FROM peripheral p "
        "JOIN register r ON p.name = r.peripheral_name "
        "ORDER BY p.name, r.name"
    ).fetchall()
    conn.close()
    for peri, reg, addr in rows:
        if addr:
            regs.setdefault(peri, []).append((reg, addr))
    return regs


def svd_counts(db_path):
    """Return (peripherals, registers, bitfields) counted from an SVD database,
    or None on failure."""
    try:
        conn = sqlite3.connect(db_path)
        p = conn.execute("SELECT count(*) FROM peripheral").fetchone()[0]
        r = conn.execute("SELECT count(*) FROM register").fetchone()[0]
        f = conn.execute("SELECT count(*) FROM field").fetchone()[0]
        conn.close()
        return (p, r, f)
    except Exception:
        return None


CPU_REGS = [f"R{i}" for i in range(16)]


def read_cpu_reg(reg_num):
    """Read CPU register N via swdd. Returns int value or None."""
    resp = swdd_cmd(f"reg {reg_num}")
    if not resp:
        return None
    for line in resp.strip().split("\n"):
        if "= 0x" in line:
            try:
                return int(line.split("= 0x")[1].split()[0], 16)
            except (IndexError, ValueError):
                return None
    return None


# --------------------------------------------------------------------------- #
# Bitfield decoding for the console panel (single-monitor users get the full
# bitfield view here, not just on monitor 1).  Mirrors regmon-tui helpers.
# --------------------------------------------------------------------------- #
RM_DB = os.path.join(_REPO_ROOT, "databases", "stm32f0xx-rm.db")


def get_rm_prose(register_name, field_name):
    """RM prose for a bitfield, incl. the generic-y fallback (MODER0->MODERy)."""
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


def get_bitfields(peripheral, register):
    """Return {name: {width, offset, desc, rm_prose}} from the SVD + RM DBs."""
    if not os.path.isfile(SVD_DB):
        return {}
    fields = {}
    try:
        conn = sqlite3.connect(SVD_DB)
        rows = conn.execute(
            "SELECT name, bitWidth, bitOffset, description FROM field "
            "WHERE peripheral_name = ? AND register_name = ? ORDER BY bitOffset",
            (peripheral.upper(), register.upper())).fetchall()
        conn.close()
    except Exception:
        return {}
    reg_name = f"{peripheral.upper()}_{register.upper()}"
    seen = set()
    for fname, width, offset, desc in rows:
        if fname in seen:
            continue
        seen.add(fname)
        fields[fname] = {"width": width, "offset": offset, "desc": desc or ""}
        prose = get_rm_prose(reg_name, fname)
        if prose:
            fields[fname]["rm_prose"] = prose
    return fields


def get_af_function(peripheral, field_name, af_value):
    """Translate an AFRH/AFRL/AFSEL field value ('AF1') into the real function
    name from the datasheet's alternate-function mapping table in the SVD
    database (one database, one search).  Field 'AFRH9' -> pin 9; 4-bit value
    = AF#.  'AFSEL9' -> pin 9 (STM32L0 uses AFSEL field names).  Returns e.g.
    'AF1 = USART1_TX', 'AF1 (reserved)', or None when N/A."""
    import re
    m = re.match(r"^(?:AFR[LH]|AFSEL)([0-9]+)$", field_name)
    if not m:
        return None
    pin = int(m.group(1))
    if not os.path.isfile(SVD_DB):
        return None
    try:
        conn = sqlite3.connect(SVD_DB)
        row = conn.execute(
            "SELECT function FROM alternate_function "
            "WHERE port = ? AND pin = ? AND af = ?",
            (peripheral[-1], pin, af_value),
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    fn = (row[0] or "").strip()
    if fn:
        return f"AF{af_value} = {fn}"
    return f"AF{af_value} (reserved)"


def _pin_has_af(peripheral, field_name):
    """True if the pin named by an AFR[LH]n / AFSELn field has any alternate
    function mapping in the SVD database (i.e. the AF register applies to it)."""
    import re
    m = re.match(r"^(?:AFR[LH]|AFSEL)([0-9]+)$", field_name)
    if not m:
        return False
    pin = int(m.group(1))
    if not os.path.isfile(SVD_DB):
        return False
    try:
        conn = sqlite3.connect(SVD_DB)
        row = conn.execute(
            "SELECT 1 FROM alternate_function WHERE port = ? AND pin = ? LIMIT 1",
            (peripheral[-1], pin)).fetchone()
        conn.close()
    except Exception:
        return False
    return row is not None


def _f1_gpio_cnf_meaning(field_name, cnf_value, reg_value, info):
    """Decode an STM32F1 GPIO CNF field using the sibling MODE field.

    CRL/CRH layout: each pin is [CNFx][MODEx] — CNF at the higher 2 bits,
    MODE at the lower 2 bits of the same 4-bit group.  CNF's meaning depends
    on whether the pin is configured as input (MODE=00) or output (MODE!=00).
    """
    mode_offset = info.get("offset", 0) - 2
    if mode_offset < 0:
        return ""
    mode_val = (reg_value >> mode_offset) & 0b11
    input_cnf = {0: "Analog input", 1: "Floating input (reset)",
                 2: "Pull-up/pull-down input", 3: "Reserved"}
    output_cnf = {0: "GP output push-pull", 1: "GP output open-drain",
                  2: "AF output push-pull", 3: "AF output open-drain"}
    table = input_cnf if mode_val == 0 else output_cnf
    base = table.get(cnf_value, "")
    mode_txt = "input" if mode_val == 0 else "output"
    return f"{base} (MODE={mode_val} = {mode_txt})" if base else ""


def format_bitfield_line(value, name, info, max_name, peri=None, reg=None):
    """One line of decoded bitfield text, e.g. 'MODER0  [1:0] = 0x00  Input mode'."""
    width = info["width"]
    offset = info["offset"]
    fval = (value >> offset) & ((1 << width) - 1)
    if width == 1:
        bits = f"{offset}"
        val_str = f"{fval}"
    elif width <= 8:
        bits = f"[{offset+width-1}:{offset}]"
        val_str = f"0x{fval:02x}"
    elif width <= 16:
        bits = f"[{offset+width-1}:{offset}]"
        val_str = f"0x{fval:04x}"
    else:
        bits = f"[{offset+width-1}:{offset}]"
        val_str = f"0x{fval:08x}"
    # RM meaning: the 'XX: meaning' line matching the current value, if any.
    meaning = ""
    prose = info.get("rm_prose") or info.get("desc") or ""
    for line in prose.split("\n"):
        line = line.strip()
        import re
        m = re.match(r"^([0-9A-Fa-f]{1,8}|0b[01]+|0x[0-9A-Fa-f]+):\s*(.+)$", line)
        if m:
            k = m.group(1).lower()
            try:
                kval = int(k, 2) if k.startswith("0b") else \
                    int(k, 16) if k.startswith("0x") else \
                    int(k, 2) if set(k) <= {"0", "1"} else int(k, 16)
            except ValueError:
                continue
            if kval == fval:
                meaning = m.group(2).strip()
                break
    # AFRH/AFRL/AFSEL: the RM prose only says 'AFn'; enrich with the real
    # function name from the datasheet AF mapping table (same DB Regmon uses).
    # An unmapped AF value on a pin that HAS alternate functions means reserved.
    if reg in ("AFRH", "AFRL") and peri:
        af_fn = get_af_function(peri, name, fval)
        if af_fn:
            meaning = af_fn
        else:
            if _pin_has_af(peri, name):
                meaning = f"AF{fval} (reserved)"
    # F1 GPIO CRL/CRH: CNF's meaning depends on the sibling MODE field — decode
    # them together so the confusing first-gen encoding is legible.
    if reg in ("CRL", "CRH") and name.startswith("CNF"):
        meaning = _f1_gpio_cnf_meaning(name, fval, value, info)
    elif reg in ("CRL", "CRH") and name.startswith("MODE"):
        mode_meaning = {0: "Input mode (reset)", 1: "Output 10 MHz",
                        2: "Output 2 MHz", 3: "Output 50 MHz"}
        meaning = mode_meaning.get(fval, "")
    if not meaning:
        meaning = prose.split("\n")[0][:80]
    return f"{name:<{max_name}}  {bits:>9} = {val_str:<10} {meaning[:60]}"



class RegmonConsole:
    def __init__(self, root):
        self.root = root
        self.root.title("Regmon-RE")
        self.root.geometry("680x970")
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.peripherals = load_registers()

        self.values = {}       # "PERI_REG" -> int value
        self.prev_values = {}
        self.changed = {}      # "PERI_REG" -> highlight cycles remaining
        self.changed_lock = threading.Lock()
        self._stop = False
        self._strings_gen = 0  # invalidates stale Strings scans (board swaps)
        # Auto chip-detect: remembers the last chip class we auto-swapped to,
        # so we only reload the DB/header when the chip actually changes (or
        # disappears) — not on every 30s tick.
        self._auto_last_key = None

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.rebuild_list())

        self._build()
        self._start_poller()
        self._start_auto_detect()

    def _build(self):
        # Chip header: class name (yellow) + DEV_ID + flash size (cyan).  The
        # IDCODE only gives the family class, and the flash-size register the
        # flash size — ST does not divulge RAM per chip, so flash is all we can
        # show.
        chip_hdr = tk.Frame(self.root, bg=BG)
        chip_hdr.pack(fill=tk.X, padx=8, pady=(6, 0))
        self.chip_class_label = tk.Label(chip_hdr, text="", bg=BG, fg="#ffcc00",
                                         font=FONT_BOLD, anchor="w")
        self.chip_class_label.pack(side=tk.LEFT)
        self.chip_label = tk.Label(chip_hdr, text="", bg=BG, fg="#88ccff",
                                   font=FONT_BOLD, anchor="w")
        self.chip_label.pack(side=tk.LEFT, padx=(8, 0))

        # Second line: how many peripherals / registers / bitfields the detected
        # chip's SVD database holds (counted from the DB).  Widget built ONCE here;
        # _set_chip_header() only refreshes its text on re-detect.
        chip_counts = tk.Frame(self.root, bg=BG)
        chip_counts.pack(fill=tk.X, padx=8, pady=(0, 0))
        self.chip_counts_label = tk.Label(chip_counts, text="SVD counts: —", bg=BG,
                                          fg="#77aaaa", font=FONT_SMALL, anchor="w")
        self.chip_counts_label.pack(side=tk.LEFT)

        # Top row: the Register search box (type a bitfield/register name to
        # filter) plus the Analyse Reg / Analyse Prog / Snapshot /
        # Upload buttons on the right.
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(top, text="Search:", bg=BG, fg=FG, font=FONT).pack(side=tk.LEFT)
        entry = tk.Entry(top, textvariable=self.filter_var, bg="#1a1a2e",
                         fg=FG, insertbackground=FG, font=FONT, width=16)
        entry.pack(side=tk.LEFT, padx=6)
        entry.bind("<Return>", lambda e: self.select_current())
        entry.focus_set()
        # Pack side=RIGHT so the LAST-packed sits LEFTMOST: left-to-right the
        # row reads  Detect | Snapshot | Upload  (Terry works left to right).
        detect_btn = tk.Button(top, text="\U0001f50d Detect Chip", bg="#4a3a5a",
                               fg="#ddccff", activebackground="#5a4a6a",
                               activeforeground="#eeccff", font=FONT_SMALL,
                               relief="flat", command=self.detect_chip)
        detect_btn.pack(side=tk.RIGHT, padx=4)
        upload_btn = tk.Button(top, text="\u21e7 Upload", bg="#2d7a7a", fg="#ffffff",
                               activebackground="#3a9a9a", font=FONT_SMALL,
                               relief="flat", command=self.upload_snapshot)
        upload_btn.pack(side=tk.RIGHT, padx=4)
        snap_btn = tk.Button(top, text="\U0001f4f7 Snapshot", bg="#3a3a5a", fg=FG,
                             activebackground="#4a4a6a", font=FONT_SMALL,
                             relief="flat", command=self.snapshot)
        snap_btn.pack(side=tk.RIGHT, padx=4)

        # Register tree: col #0 = name, col "val" = live value.
        # Small fixed height (Terry: single monitor until the video cable
        # arrives) — the AI analysis box below gets the freed space.
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill=tk.X, padx=8, pady=4)
        self.tree = ttk.Treeview(frame, columns=("val",), show="tree",
                                 selectmode="browse", height=6)
        self.tree.heading("#0", text="Register")
        self.tree.heading("val", text="Value")
        self.tree.column("#0", width=290, anchor="w")
        self.tree.column("val", width=110, anchor="e")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#12122a", foreground=FG,
                        fieldbackground="#12122a", font=FONT, rowheight=22)
        style.configure("Treeview.Heading", background="#1a1a2e", foreground=FG,
                        font=FONT_SMALL)
        self.tree.tag_configure("peri", foreground=ACCENT, font=FONT_BOLD)
        self.tree.tag_configure("cpu", foreground="#88bbff", font=FONT_BOLD)
        self.tree.tag_configure("reg", foreground=FG)
        self.tree.tag_configure("reg_chg", foreground=CHANGE)
        self.tree.tag_configure("reg_off", foreground="#555570")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda e: self.select_current())
        self.tree.bind("<Return>", lambda e: self.select_current())
        self.tree.bind("<Button-3>", self._show_copy_menu)
        self.tree.bind("<Control-c>", lambda e: self.copy_selected())
        self.root.bind("<Control-c>", lambda e: self.copy_selected())
        # Fully-collapsed tree = browse mode on Regmon (full register list).
        self.tree.bind("<<TreeviewOpen>>", lambda e: None)
        self.tree.bind("<<TreeviewClose>>", lambda e: self._browse_if_collapsed())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.refresh_bitfields())

        # Bitfield panel: decoded bitfields of the selected register with live
        # values — gives single-monitor users the full bitfield view (the same
        # detail the TUI shows on monitor 1).
        bf_head = tk.Frame(self.root, bg=BG)
        bf_head.pack(fill=tk.X, padx=8, pady=(2, 0))
        self.bf_label = tk.Label(bf_head, text="Bitfields", bg=BG, fg="#88bbff",
                                 font=FONT_BOLD, anchor="w")
        self.bf_label.pack(side=tk.LEFT)
        bf_frame = tk.Frame(self.root, bg=BG)
        bf_frame.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.bf_text = tk.Text(
            bf_frame, height=6, bg="#12122a", fg=FG, font=FONT_SMALL,
            wrap="none", relief="flat", state="disabled", padx=6, pady=4)
        bf_scroll = ttk.Scrollbar(bf_frame, orient="vertical",
                                  command=self.bf_text.yview)
        self.bf_text.configure(yscrollcommand=bf_scroll.set)
        self.bf_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bf_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._bf_group = None
        self._bf_reg = None
        self._bf_fields = {}
        self._bf_last_val = None

        # Ask AI box: type a question (with the selected register as context)
        # and the local gateway answers into the AI Analysis box below.
        ask_head = tk.Frame(self.root, bg=BG)
        ask_head.pack(fill=tk.X, padx=8, pady=(0, 2))
        tk.Label(ask_head, text="Ask AI:", bg=BG, fg="#99cc99",
                 font=FONT_BOLD, anchor="w").pack(side=tk.LEFT)
        self.ask_entry = tk.Entry(
            ask_head, bg="#15151e", fg=FG, insertbackground=FG,
            font=FONT, relief="flat")
        self.ask_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))
        self.ask_entry.bind("<Return>", lambda e: self.ask_ai())
        ask_btn = tk.Button(ask_head, text="Ask", bg="#2a5a2a", fg="#ccffcc",
                            font=FONT_SMALL, relief="flat",
                            activebackground="#3a6a3a", command=self.ask_ai)
        ask_btn.pack(side=tk.RIGHT)

        # AI analysis box: the local model's verdict lands here (monitor 0),
        # keeping the Regmon display (monitor 1) purely the register view.
        ai_head = tk.Frame(self.root, bg=BG)
        ai_head.pack(fill=tk.X, padx=8, pady=(0, 2))
        self.ai_copy_btn = tk.Button(
            ai_head, text="Copy", bg="#3a3a5a", fg=FG, font=FONT_SMALL,
            relief="flat", activebackground="#4a4a6a", command=self.copy_ai_text)
        self.ai_copy_btn.pack(side=tk.RIGHT)
        self.ai_clear_btn = tk.Button(
            ai_head, text="Clear", bg="#5a3a3a", fg="#ff8888", font=FONT_SMALL,
            relief="flat", activebackground="#6a4a4a", command=self.clear_ai_text)
        self.ai_clear_btn.pack(side=tk.RIGHT)
        self.blank_btn = tk.Button(ai_head, text="Blank?", bg="#2a4a4a",
                                   fg="#88ffff", activebackground="#3a5a5a",
                                   activeforeground="#aaffff", font=FONT_SMALL,
                                   relief="flat", command=self.blank_check)
        self.blank_btn.pack(side=tk.RIGHT, padx=(0, 4))
        saveflash_btn = tk.Button(ai_head, text="Save Flash",
                                  bg="#3a3a2a", fg="#ffcc66",
                                  activebackground="#4a4a3a",
                                  activeforeground="#ffdd88", font=FONT_SMALL,
                                  relief="flat", command=self.save_flash)
        saveflash_btn.pack(side=tk.RIGHT, padx=(0, 4))
        strings_btn = tk.Button(ai_head, text="Strings", bg="#2a3a2a",
                                fg="#88ff88", activebackground="#3a5a3a",
                                activeforeground="#aaffaa", font=FONT_SMALL,
                                relief="flat", command=self.scan_strings)
        strings_btn.pack(side=tk.RIGHT, padx=(0, 4))
        analyse_btn = tk.Button(ai_head, text="Analyse Reg", bg="#2a2a4a", fg=ACCENT,
                                activebackground="#3a3a5a", activeforeground="#ff8888",
                                font=FONT_SMALL, relief="flat",
                                command=self.analyse_current)
        analyse_btn.pack(side=tk.RIGHT, padx=(0, 4))
        prog_btn = tk.Button(ai_head, text="Analyse Prog", bg="#3a2a1a",
                             fg="#ffcc88", activebackground="#5a3a2a",
                             activeforeground="#ffdd99", font=FONT_SMALL,
                             relief="flat", command=self.analyse_program)
        prog_btn.pack(side=tk.RIGHT, padx=(0, 4))
        ai_frame = tk.Frame(self.root, bg=BG)
        ai_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        self.ai_text = tk.Text(
            ai_frame, height=16, bg="#1a1a2e", fg=FG, insertbackground=FG,
            font=FONT_SMALL, wrap="word", relief="flat", state="disabled",
            padx=6, pady=4)
        ai_scroll = ttk.Scrollbar(ai_frame, command=self.ai_text.yview)
        self.ai_text.configure(yscrollcommand=ai_scroll.set)
        self.ai_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ai_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Status line
        self.status = tk.Label(self.root, text="", bg=BG, fg="#66ff66",
                               font=FONT_SMALL, anchor="w")
        self.status.pack(fill=tk.X, padx=8, pady=4)
        self.update_status()

        # Last snapshot path — persists until the next snapshot so Terry can
        # come back later and find the file (e.g. for podcasts).
        self.last_snapshot_label = tk.Label(
            self.root, text="Last snapshot: —", bg=BG, fg="#88cc88",
            font=FONT_SMALL, anchor="w")
        self.last_snapshot_label.pack(fill=tk.X, padx=8, pady=(0, 4))

        # Bottom bar: version (left) + SWDD ON/OFF control (right).  Terry stops
        # swdd from the console when he wants to flash a new binary with st-flash
        # (swdd holds the ST-Link claim), then restarts it from here afterwards.
        # The leading square is a LIVE indicator: solid (U+2B1B) marks the state
        # that is currently active, outline (U+2B1C) the inactive one.
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.version_label = tk.Label(
            bottom, text=f"{APP_NAME} v{VERSION}", bg=BG, fg="#77aaaa",
            font=FONT_SMALL, anchor="w")
        self.version_label.pack(side=tk.LEFT)
        self.swdd_off_btn = tk.Button(bottom, text="\u2b1c SWDD OFF", bg="#5a2a2a",
                                      fg="#ff8888", activebackground="#6a3a3a",
                                      activeforeground="#ffaaaa", font=FONT_SMALL,
                                      relief="flat", command=self.swdd_off)
        self.swdd_off_btn.pack(side=tk.RIGHT, padx=2)
        self.swdd_on_btn = tk.Button(bottom, text="\u2b1c SWDD ON", bg="#2a4a2a",
                                     fg="#88ff88", activebackground="#3a5a3a",
                                     activeforeground="#aaffaa", font=FONT_SMALL,
                                     relief="flat", command=self.swdd_on)
        self.swdd_on_btn.pack(side=tk.RIGHT, padx=2)
        self._refresh_swdd_indicators()

        self.rebuild_list()
        self._browse_if_collapsed()  # startup: nothing open → browse all

    def _set_chip_header(self, info):
        """Refresh ONLY the two chip-header labels + the SVD-counts line from
        the detected chip info.  Yellow = family class name, cyan = DEV_ID +
        flash size + the SVD DB in use.  This is a text-only refresh — the
        widgets are created ONCE in _build().  (These were formerly rebuilt on
        every Detect Chip / Assist click, which re-packed a full duplicate of
        the console below the real one.)"""
        dev = (info or {}).get("dev_id")
        name = (info or {}).get("name") if info else None
        if dev is not None and name:
            self.chip_class_label.config(text=name)
            fs = flash_size_kb()
            ftext = ("Flash: %d KB" % fs) if fs else "Flash: ?"
            self.chip_label.config(
                text="DEV_ID 0x%03x  ·  %s  ·  DB: %s"
                     % (dev, ftext, os.path.basename(SVD_DB)))
        else:
            self.chip_class_label.config(text="unknown chip")
            self.chip_label.config(text="")
        counts = svd_counts(SVD_DB)
        counts_line = "SVD counts: —"
        if counts:
            counts_line = ("SVD counts: %d peripherals · %d registers · %d bitfields"
                           % counts)
        self.chip_counts_label.config(text=counts_line)

    def detect_chip(self):
        """Re-detect the connected chip on demand (after swapping boards) and
        reload the matching SVD database, register list and chip header.  The
        console otherwise only detects at startup."""
        global SVD_DB
        if _chip is None:
            self.update_status("chip detection module unavailable")
            return
        try:
            info = _chip.detect_chip()
        except Exception as e:
            self.update_status("chip detection failed: %s" % e)
            return
        _CHIP_INFO.update(info)  # keep the module global in sync (startup uses it)
        newdb = _chip.chip_db_path(info)
        if newdb:
            SVD_DB = newdb
        # Reload the register tree from the new database and refresh the header.
        self.peripherals = load_registers()
        self.values.clear()
        self.prev_values.clear()
        self.rebuild_list()
        self._set_chip_header(info)
        counts = svd_counts(SVD_DB)
        if counts:
            self.chip_counts_label.config(
                text="SVD counts: %d peripherals · %d registers · %d bitfields"
                     % counts)
        else:
            self.chip_counts_label.config(text="SVD counts: —")
        if info.get("dev_id") is not None:
            self.update_status("Chip: %s (DEV_ID 0x%03x) — DB switched to %s"
                               % (info["name"], info["dev_id"],
                                  os.path.basename(SVD_DB)))
        else:
            self.update_status("Chip: unknown chip — DB kept at %s"
                               % os.path.basename(SVD_DB))

    def _start_poller(self):
        def poll():
            while not self._stop:
                # CPU registers first (read via reg N).
                for n in range(16):
                    key = f"CPU_R{n}"
                    v = read_cpu_reg(n)
                    if v is not None:
                        self._track(key, v)
                    time.sleep(0.001)
                # Peripheral registers (read via mem).
                for peri, regs in self.peripherals.items():
                    for reg, addr in regs:
                        key = f"{peri}_{reg}"
                        v = read_mem_word(addr)
                        if v is not None:
                            self._track(key, v)
                        time.sleep(0.002)
                self.root.after(0, self._apply_values)
                self._decay_changes()
                time.sleep(POLL_PERIOD)

        threading.Thread(target=poll, daemon=True).start()

    def _start_auto_detect(self):
        """Background loop: auto-detect the connected chip every 30 seconds,
        but ONLY while swdd is active.  If a different chip appears (board
        swapped), reload the SVD database + header automatically.  If no chip
        is found, show 'No chip attached'.  When swdd is off/disconnected,
        the probe is skipped entirely (no error spam) and detection resumes
        automatically when swdd comes back."""
        def loop():
            while not self._stop:
                state = self._swdd_state()
                if state == "inactive":
                    # swdd deliberately stopped (SWDD OFF).  Track this as the
                    # current state so the header says WHY there is no target
                    # — Terry may come back later and see a stale 'No target'
                    # and not realise swdd is off.  Only act on a change.
                    key = "swdd-off"
                    if key != self._auto_last_key:
                        self._auto_last_key = key
                        self.root.after(0, self._set_no_chip)
                    time.sleep(AUTO_DETECT_PERIOD)
                    continue
                # swdd active OR activating (trying): probe the chip.
                try:
                    info = _chip.detect_chip() if _chip else None
                except Exception:
                    info = None
                dev = (info or {}).get("dev_id")
                key = "chip:%d" % dev if dev is not None else "none"
                if key == self._auto_last_key:
                    time.sleep(AUTO_DETECT_PERIOD)
                    continue
                self._auto_last_key = key
                if dev is None:
                    # No chip (or swdd down but active) — tell Terry plainly.
                    self.root.after(0, self._set_no_chip)
                else:
                    self.root.after(0, self._auto_apply_chip, info)
                time.sleep(AUTO_DETECT_PERIOD)

        threading.Thread(target=loop, daemon=True).start()

    def _set_no_chip(self):
        """Show 'No target' in the header (keep the last DB loaded so the
        register tree still has something sensible, but make the absence
        obvious instead of showing a stale chip).  If SWDD is deliberately
        stopped (the SWDD OFF button) it says so in brackets, so Terry can
        tell 'no debugger' apart from 'debugger off' at a glance."""
        state = self._swdd_state()
        if state == "inactive":
            self.chip_class_label.config(text="No target (SWDD off)")
        else:
            self.chip_class_label.config(text="No target")
        self.chip_label.config(text="", fg="#ff8888")
        if state == "active":
            self.update_status("No target — waiting for a board…")
        elif state == "activating":
            self.update_status("No target — swdd trying to open the debugger (ST-Link connected?)")

    def _auto_apply_chip(self, info):
        """Auto-swap to the newly-detected chip: reload the SVD database,
        register tree and header.  Called from the auto-detect loop when the
        chip identity changes (or a chip is (re)attached)."""
        global SVD_DB
        _CHIP_INFO.update(info)
        newdb = _chip.chip_db_path(info)
        if newdb:
            SVD_DB = newdb
        self.peripherals = load_registers()
        self.values.clear()
        self.prev_values.clear()
        self.rebuild_list()
        self._set_chip_header(info)
        if info.get("dev_id") is not None:
            self.update_status("Auto: %s (DEV_ID 0x%03x) — DB %s"
                               % (info["name"], info["dev_id"],
                                  os.path.basename(SVD_DB)))

    def _track(self, key, v):
        """Record a value; flag a highlight if it changed since last poll."""
        prev = self.prev_values.get(key)
        if prev is not None and prev != v:
            with self.changed_lock:
                self.changed[key] = 2  # highlight ~2 cycles
        self.prev_values[key] = v
        self.values[key] = v

    def _decay_changes(self):
        with self.changed_lock:
            for k in list(self.changed):
                self.changed[k] -= 1
                if self.changed[k] <= 0:
                    del self.changed[k]

    def _apply_values(self):
        """Update the tree's value column in place (no rebuild, no flicker)."""
        for item in self.tree.get_children():
            group = self.tree.item(item, "text")
            if group == "CPU":
                for child in self.tree.get_children(item):
                    reg = self.tree.item(child, "text").strip()
                    self._update_row(child, f"CPU_{reg}")
                continue
            for child in self.tree.get_children(item):
                reg = self.tree.item(child, "text").strip()
                self._update_row(child, f"{group}_{reg}")
        # Refresh the bitfield panel live for the currently selected register.
        if self._bf_group and self._bf_reg:
            key = f"{self._bf_group}_{self._bf_reg}"
            newv = self.values.get(key)
            if newv != self._bf_last_val:
                self._bf_last_val = newv
                self.refresh_bitfields()

    def _update_row(self, item, key):
        v = self.values.get(key)
        if v is None:
            self.tree.set(item, "val", "—")
            self.tree.item(item, tags=("reg_off",))
            return
        self.tree.set(item, "val", f"0x{v:08x}")
        with self.changed_lock:
            if self.changed.get(key):
                self.tree.item(item, tags=("reg_chg",))
            else:
                self.tree.item(item, tags=("reg",))

    def update_status(self, msg=None):
        if msg:
            self.status.config(text=msg, fg="#ffdd66")
        else:
            self.status.config(text="● Regmon-RE — double-click a register to see its bitfields",
                               fg="#66ff66")

    # ---- Live progress (message + NN%) ----------------------------------- #
    def _progress_start(self, message, fg="#ffdd66"):
        """Begin a live-progress status: 'message NN%' updating in place, no
        animation.  Returns a token for _progress_update/_progress_stop.  Only
        one progress line runs at a time — starting a new one cancels the
        previous."""
        token = object()
        self._progress_token = token
        self.status.config(text=message, fg=fg)
        return token

    def _progress_update(self, token, message):
        """Update the progress status text in place (message includes NN%).
        No-op if a different job has since taken over the status line."""
        if getattr(self, "_progress_token", None) is not token:
            return
        self.status.config(text=message, fg="#ffdd66")

    def _progress_stop(self, token, final_message=None):
        """End the live progress; optionally set a final status message."""
        if getattr(self, "_progress_token", None) is token:
            self._progress_token = None
        if final_message:
            self.update_status(final_message)

    # ---- SWDD on/off (for flashing) -------------------------------------- #
    def _swdd_state(self):
        """Return 'active' or 'inactive' (the systemd unit state of swdd)."""
        try:
            p = subprocess.run(["systemctl", "is-active", "swdd.service"],
                               capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
            return (p.stdout or "").strip()
        except Exception:
            return "unknown"

    def _refresh_swdd_indicators(self):
        """Set each SWDD button's leading square to reflect the live state:
        SOLID (U+2B1B) on the button that matches the current swdd state,
        OUTLINE (U+2B1C) on the other.  So when swdd is running the ON button
        shows the solid 'lit' block and OFF shows the outline, and vice versa
        when it is stopped — the indicator is never back-to-front."""
        active = self._swdd_state() == "active"
        on_icon = "\u2b1b" if active else "\u2b1c"
        off_icon = "\u2b1c" if active else "\u2b1b"
        self.swdd_on_btn.config(text=f"{on_icon} SWDD ON")
        self.swdd_off_btn.config(text=f"{off_icon} SWDD OFF")

    def _swdd(self, state):
        """Start or stop the swdd systemd service from the console.  Terry stops
        it before flashing a new binary with st-flash (swdd holds the ST-Link
        claim), then restarts it afterwards — no terminal needed.  Runs in a
        background thread so the GUI stays responsive; the sudo call is
        passwordless for tp."""
        token = self._progress_start("SWDD %s…" % state)
        def work():
            try:
                p = subprocess.run(
                    ["sudo", "systemctl", state, "swdd.service"],
                    capture_output=True, text=True, timeout=30,
                    stdin=subprocess.DEVNULL)
                ok = p.returncode == 0
                msg = "SWDD %s (%s)" % ("ON" if state == "start" else "OFF",
                                        "running" if state == "start" else "stopped")
                if not ok:
                    msg = "SWDD %s failed: %s" % (state, (p.stderr or "").strip()[:120])
                self.root.after(0, lambda: self._progress_stop(token, msg))
                self.root.after(0, self._refresh_swdd_indicators)
                # Refresh the chip header immediately: SWDD off -> 'No target
                # (SWDD off)'; SWDD on -> re-probe (chip or 'No target').  The
                # 30s auto-detect would get there eventually, but Terry wants
                # it instant on the button click.
                self.root.after(0, self._refresh_chip_after_swdd)
            except Exception as e:
                self.root.after(0, lambda: self._progress_stop(
                    token, "SWDD %s error: %s" % (state, e)))
        threading.Thread(target=work, daemon=True).start()

    def _refresh_chip_after_swdd(self):
        """Called right after an SWDD ON/OFF action.  SWDD off -> header shows
        'No target (SWDD off)'.  SWDD on -> re-probe the chip now (a board may
        have been plugged in while swdd was off) and show the chip or 'No
        target'."""
        if self._swdd_state() == "inactive":
            self._set_no_chip()
            return
        # swdd active/activating: probe once
        try:
            info = _chip.detect_chip() if _chip else None
        except Exception:
            info = None
        dev = (info or {}).get("dev_id")
        if dev is None:
            self._set_no_chip()
        else:
            self._auto_last_key = "chip:%d" % dev
            self._auto_apply_chip(info)

    def swdd_on(self):
        self._swdd("start")

    def swdd_off(self):
        self._swdd("stop")


    def rebuild_list(self):
        self.tree.delete(*self.tree.get_children())
        q = self.filter_var.get().strip().lower()
        # CPU register group first.
        if not q or "cpu" in q or any(q in r.lower() for r in CPU_REGS):
            parent = self.tree.insert("", "end", text="CPU", tags=("cpu",))
            for r in CPU_REGS:
                self.tree.insert(parent, "end", text=f"    {r}", values=("—",),
                                 tags=("reg",))
        for peri, regs in self.peripherals.items():
            matched = [(r, a) for r, a in regs
                       if not q or q in r.lower() or q in (peri + r).lower()]
            if not matched:
                continue
            parent = self.tree.insert("", "end", text=peri, tags=("peri",))
            for r, _ in matched:
                self.tree.insert(parent, "end", text=f"    {r}",
                                 values=("—",), tags=("reg",))

    def current_register(self):
        sel = self.tree.selection()
        if not sel:
            return None, None
        item = sel[0]
        parent = self.tree.parent(item)
        if not parent:
            return None, None
        reg = self.tree.item(item, "text").strip()
        group = self.tree.item(parent, "text")
        return group, reg

    def refresh_bitfields(self):
        """Decode the selected register's live value into its bitfields and
        show them in the bitfield panel.  Called on selection and live value
        change."""
        group, reg = self.current_register()
        if not group or group == "CPU":
            self._bf_group = None
            self._bf_reg = None
            self._bf_fields = {}
            self.bf_label.config(text="Bitfields")
            self._bf_write("(select a peripheral register)")
            return
        name = f"{group}_{reg}"
        key = f"{group}_{reg}"
        if (group, reg) != (self._bf_group, self._bf_reg):
            self._bf_group = group
            self._bf_reg = reg
            self._bf_fields = get_bitfields(group, reg)
            self._bf_last_val = None
            self.bf_label.config(text=f"Bitfields: {name}")
        v = self.values.get(key)
        self._bf_write_lines(name, v)

    def _bf_write_lines(self, name, v):
        lines = []
        if not self._bf_fields:
            lines.append(f"{name}: no bitfield data (SVD)")
        elif v is None:
            lines.append(f"{name} = --- (unread)")
        else:
            lines.append(f"{name} = 0x{v:08x}")
            max_name = max((len(n) for n in self._bf_fields), default=8)
            for fname, finfo in sorted(
                    self._bf_fields.items(), key=lambda kv: kv[1]["offset"]):
                lines.append(format_bitfield_line(
                    v, fname, finfo, max_name,
                    getattr(self, "_bf_group", None),
                    getattr(self, "_bf_reg", None)))
        def do():
            self.bf_text.configure(state="normal")
            self.bf_text.delete("1.0", tk.END)
            self.bf_text.insert("1.0", "\n".join(lines))
            self.bf_text.configure(state="disabled")
        self.root.after(0, do)

    def _bf_write(self, text):
        def do():
            self.bf_text.configure(state="normal")
            self.bf_text.delete("1.0", tk.END)
            self.bf_text.insert("1.0", text)
            self.bf_text.configure(state="disabled")
        self.root.after(0, do)

    def _browse_if_collapsed(self):
        """No-op retained for the TreeviewClose binding — the Regmon display
        is retired, so there is nothing to tell it to browse."""

    def select_current(self):
        group, reg = self.current_register()
        if not group:
            # Double-clicked a peripheral GROUP (e.g. GPIOB) — jump to its
            # first register so the click always does something useful.
            sel = self.tree.selection()
            if sel:
                kids = self.tree.get_children(sel[0])
                if kids:
                    item = kids[0]
                    reg = self.tree.item(item, "text").strip()
                    group = self.tree.item(sel[0], "text")
            if not group:
                return
        # Regmon display is retired — just confirm the selection in the console.
        if group == "CPU":
            self.update_status(f"→ CPU {reg}")
        else:
            name = f"{group}_{reg}"
            self.update_status(f"→ {name}")
        self.refresh_bitfields()

    def analyse_current(self):
        """Ask the local AI to interpret the selected register's live value.
        Runs regmon-analyze.py in a background thread and shows the verdict
        in the AI Analysis box at the bottom of this console (monitor 0)."""
        group, reg = self.current_register()
        if not group:
            self.update_status("Select a register first")
            return
        if group == "CPU":
            self.update_status("AI analysis works on peripheral registers, not CPU R-regs")
            return
        name = f"{group}_{reg}"
        key = f"{group}_{reg}"
        v = self.values.get(key)
        varg = f"0x{v:x}" if v is not None else ""
        self.update_status(f"AI analysing {name} (20 samples)…")
        self._ai_write(f"Analysing {name} over 20 samples (~10s)…\n")
        prog = self._progress_start(f"Analysing {name} — 0%")
        started = time.monotonic()

        def progress_tick():
            """Estimate progress from elapsed time (~10s job): the sample loop
            is the bulk of the runtime, and the AI call adds a tail.  Keeps the
            status bar showing the job is alive, like the Strings scanner."""
            if getattr(self, "_progress_token", None) is not prog:
                return
            el = time.monotonic() - started
            pct = min(95, int(el / 10 * 100))
            self._progress_update(prog, f"Analysing {name} — {pct}%")
            self.root.after(500, progress_tick)

        self.root.after(500, progress_tick)

        def work():
            analyzer = os.path.join(_REPO_ROOT, "swdcom", "regmon-analyze.py")
            # Capture ~20 samples at 0.5s apart = a full ~10s cycle, so a
            # periodically-enabled peripheral (e.g. the comparator during its
            # 100ms measurement window) is guaranteed to be seen toggling
            # instead of caught in a single off state.
            cmd = [sys.executable, analyzer, "--samples", "20", name]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=180, stdin=subprocess.DEVNULL)
                text = (p.stdout or "").strip()
                err = (p.stderr or "").strip()
                if not text:
                    text = f"(AI unavailable: {err[:160]})"
            except Exception as e:
                text = f"(AI error: {e})"
            self._ai_write(text + "\n")
            self.root.after(0, lambda: self._progress_stop(
                prog, f"Analysis: {name}"))

        threading.Thread(target=work, daemon=True).start()

    def analyse_program(self):
        """Analyse the WHOLE chip: read RCC, fingerprint every clock-enabled
        peripheral, and ask the local AI what the program is doing. Runs
        regmon-program-analyze.py in a background thread; verdict lands in the
        AI Analysis box."""
        # Match the chip line at the top of the console (Chip: <name> ... Using <db>)
        chip_desc = "Chip: unknown"
        if _CHIP_INFO and _CHIP_INFO.get("dev_id") is not None:
            chip_desc = "Chip: %s (DEV_ID 0x%03x) Using %s.db" % (
                _CHIP_INFO["name"], _CHIP_INFO["dev_id"],
                os.path.splitext(os.path.basename(SVD_DB))[0])
        elif _CHIP_INFO:
            chip_desc = "Chip: %s Using %s.db" % (
                _CHIP_INFO["name"], os.path.splitext(os.path.basename(SVD_DB))[0])
        self.update_status("Analysing %s…" % chip_desc)
        self._ai_write("Analysing %s — RCC-gated fingerprint of all active "
                       "peripherals, then local AI interpretation…\n" % chip_desc)
        prog = self._progress_start(f"Analysing {chip_desc} — 0%")
        started = time.monotonic()

        def progress_tick():
            if getattr(self, "_progress_token", None) is not prog:
                return
            el = time.monotonic() - started
            pct = min(95, int(el / 30 * 100))
            self._progress_update(prog, f"Analysing {chip_desc} — {pct}%")
            self.root.after(500, progress_tick)

        self.root.after(500, progress_tick)

        def work():
            analyzer = os.path.join(_REPO_ROOT, "swdcom", "regmon-program-analyze.py")
            cmd = [sys.executable, analyzer, "--rate", "10", "--samples", "20"]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=240, stdin=subprocess.DEVNULL)
                text = (p.stdout or "").strip()
                err = (p.stderr or "").strip()
                if not text:
                    text = err or "(no output)"
            except Exception as e:
                text = f"(AI error: {e})"
            self._ai_write(text + "\n")
            self.root.after(0, lambda: self._progress_stop(
                prog, "Program analysis complete"))

        threading.Thread(target=work, daemon=True).start()

    def _ai_write(self, text):
        """Append text to the AI Analysis box from any thread."""
        def do():
            self.ai_text.configure(state="normal")
            self.ai_text.insert("end", text)
            self.ai_text.see("end")
            self.ai_text.configure(state="disabled")
            # Force the widget to recompute its layout/paint.  Without this, a
            # Text widget written to while scrolled or right after a large
            # insert can leave a stale paint of the old content overlapping the
            # new one (the 'duplicated content at the bottom' rendering bug).
            try:
                self.ai_text.update_idletasks()
            except Exception:
                pass
        self.root.after(0, do)

    def ask_ai(self):
        """Ask the AI a free-form question (opencode-first, gateway fallback)
        and show the answer in the AI Analysis box.  The selected register is
        included as context (name + SVD bitfields) so answers stay grounded.
        Standalone: uses whatever AI the user configured for opencode."""
        question = self.ask_entry.get().strip()
        if not question:
            self.update_status("Type a question first")
            return
        group, reg = self.current_register()
        svd = _svd_bitfields(group, reg)
        ctx = f"{group}.{reg}" if group and reg else "no register selected"
        if svd:
            ctx += "\n\n" + svd + "\n\n(Use these EXACT SVD field names/bit positions — do not invent others.)"
        self._ai_write(f"\n── Ask: {question}\n")
        self.update_status(f"Ask AI: {question}")

        def work():
            text = _ai_ask(question, ctx)
            self._ai_write(text + "\n")
            self.root.after(0, lambda: self.update_status("AI answered"))

        threading.Thread(target=work, daemon=True).start()

    def copy_ai_text(self):
        """Copy the AI Analysis box contents to the clipboard."""
        content = self.ai_text.get("1.0", "end-1c").strip()
        if not content:
            self.update_status("Nothing to copy")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.update_status(f"Copied AI analysis ({len(content)} chars)")

    def clear_ai_text(self):
        """Clear the AI Analysis box so repeated analyses don't pile up.  Also
        invalidates any in-flight Strings scan (bumps the generation) so a slow
        scan from a previous chip can't write its stale results afterwards."""
        self._strings_gen += 1
        def do():
            self.ai_text.configure(state="normal")
            self.ai_text.delete("1.0", tk.END)
            self.ai_text.configure(state="disabled")
        self.root.after(0, do)
        self.update_status("AI analysis cleared")

    def scan_strings(self):
        """Scan the chip's flash for readable English text (like the 'strings'
        tool) and list the words/phrases in the AI Analysis box.  Clears the
        box first, like the Clear button.  Runs the flash read in a background
        thread so the GUI stays responsive.  Shows a live progress percentage
        in the status line (a 1MB flash scan takes a while at swdd's 256-byte
        per-read limit).

        A generation guard prevents a slow scan from a PREVIOUS chip writing its
        stale results after this scan (or a Clear) has run — each new scan and
        each Clear bumps the generation; a scan only writes if its generation is
        still current."""
        self.clear_ai_text()  # also bumps the generation
        gen = self._strings_gen
        prog = self._progress_start("Strings: scanning flash… 0%")

        def work():
            try:
                words, kb, bytes_read, dump = scan_flash_strings(
                    progress=lambda p: self._scan_progress(prog, gen, p))
            except Exception as e:
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Strings error: %s" % e))
                return
            # stale scan (board swapped / box cleared while we were reading)?
            if gen != self._strings_gen:
                return
            if bytes_read == 0:
                # every flash read failed — no chip answering on the SWD wire
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Strings: no board attached — SWD not responding"))
                return
            if not words:
                # no readable text — show the raw flash so Terry can see what
                # IS there (blank 0xFF, real code, zeros, etc.)
                if all(b == 0xFF for b in dump):
                    body = "No readable strings found — flash looks BLANK (first bytes all 0xFF)."
                else:
                    body = ("No readable strings found — first %d bytes of flash:\n\n%s"
                            % (len(dump), hex_dump(dump)))
                self._ai_write("── Flash strings ──\n\n%s\n" % body)
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Strings: no readable text found (flash empty or unreadable)"))
                return
            # show each string on its own line (address prefix for the first
            # occurrence); space-separated per Terry's request so words are easy
            # to read and copy.
            body = []
            seen = set()
            for a, s in words:
                body.append(s)
            text = " ".join(body)
            self._ai_write("── Flash strings (%d KB flash, %d found) ──\n\n%s\n"
                           % (kb, len(words), text))
            self.root.after(0, lambda: self._progress_stop(
                prog, "Strings: %d found in %d KB flash" % (len(words), kb)))

        threading.Thread(target=work, daemon=True).start()

    def _scan_progress(self, prog, gen, pct):
        """Called from the scan thread with a percentage; pushes a live
        'Strings: scanning… NN%' status update on the GUI thread.  Ignores
        stale scans (generation no longer current).  THROTTLED to ~5/s — the
        scan calls this after every 256-byte chunk (4096 times for a 1MB scan),
        and flooding the Tk event loop with after() callbacks stalls repainting
        (the window's content appears duplicated/corrupted) and makes Clear
        appear to hang while it sits behind the queue."""
        if gen != self._strings_gen:
            return
        now = time.monotonic()
        if getattr(self, "_scan_last_prog", 0) and (now - self._scan_last_prog) < 0.2:
            return
        self._scan_last_prog = now
        def do():
            self._progress_update(prog, "Strings: scanning flash… %d%%" % pct)
        self.root.after(0, do)

    # ---- Save Flash (download the whole image to a file) ----------------- #
    def save_flash(self):
        """Download the chip's entire flash image to a binary file on the PC,
        with live progress.  Terry can then analyse the image offline (Ghidra,
        strings, objdump) or flash a duplicate chip with it — a Forth kernel
        with all its words/dictionary saved this way boots ready-to-run on
        another identical chip, no reinstall needed.  The path is copied to the
        clipboard and shown in the status line."""
        kb = flash_size_kb()
        if kb is None:
            self.update_status("Save Flash: can't read flash size (swdd down?)")
            return
        chip = "STM32"
        if _CHIP_INFO and _CHIP_INFO.get("name"):
            chip = _CHIP_INFO["name"].split()[0]
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        d = os.path.join(_REPO_ROOT, "flash-dumps")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        path = os.path.join(d, f"{chip}-{ts}-{kb}kb.bin")
        prog = self._progress_start("Save Flash: 0%")

        def progress(pct):
            self._progress_update(prog, "Save Flash: %d%%" % pct)

        def work():
            try:
                total = dump_flash_to_file(path, progress=progress)
            except Exception as e:
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Save Flash error: %s" % e))
                return
            if total is None:
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Save Flash failed (swdd read error)"))
                return
            self._copy_to_clipboard(path)
            self.root.after(0, lambda: self._progress_stop(
                prog, "Save Flash: %d KB saved + copied — %s" % (total // 1024, path)))

        threading.Thread(target=work, daemon=True).start()

    def blank_check(self):
        """Check whether the chip's flash is blank (all 0xFF) — i.e. an erased
        or never-programmed chip.  Reads flash in 256-byte chunks (like Strings)
        and reports either 'flash is blank' or the first non-0xFF address.
        Distinguishes 'no board attached' from a genuinely blank flash.

        The status line shows a live % while the scan runs; on completion the
        Blank? button itself becomes the verdict: 'Blank ✓' (green) if the
        flash is all 0xFF, 'Blank ✗' (red) if anything is programmed."""
        self.clear_ai_text()
        # reset the verdict button to the neutral '?' state
        self.blank_btn.config(text="Blank?", bg="#2a4a4a", fg="#88ffff")
        prog = self._progress_start("Blank Check: 0%")
        kb = flash_size_kb()
        if kb is None:
            self._progress_stop(prog, "Blank Check: can't read flash size (swdd down?)")
            return
        total = kb * 1024
        addr = 0x08000000
        end = 0x08000000 + total
        nchunks = (total + 255) // 256

        def progress(pct):
            # must marshal to the GUI thread (Tk is not thread-safe)
            self.root.after(0, lambda: self._progress_update(
                prog, "Blank Check: %d%%" % pct))

        def work():
            chunk = 0
            bytes_read = 0
            nonff = 0
            first_nonff = None
            a = addr
            while a < end:
                n = min(256, end - a)
                resp = swdd_cmd(f"mem {a:x} {n}", timeout=5)
                if resp:
                    for line in resp.strip().split("\n"):
                        line = line.strip()
                        if not line or line.startswith("MEM") or line == ".":
                            continue
                        if ":" not in line:
                            continue
                        for b in line.split(":")[1].strip().split():
                            try:
                                v = int(b, 16)
                            except ValueError:
                                continue
                            bytes_read += 1
                            if v != 0xFF:
                                nonff += 1
                                if first_nonff is None:
                                    first_nonff = a + (bytes_read - 1)
                a += n
                chunk += 1
                progress(int(100 * chunk / nchunks))
            if bytes_read == 0:
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Blank Check: no board attached — SWD not responding"))
                return
            if first_nonff is None:
                body = "Flash is BLANK — all %d bytes are 0xFF (%d KB erased or never programmed)." % (bytes_read, total // 1024)
                self._ai_write("── Blank Check ──\n\n%s\n" % body)
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Blank Check: flash is BLANK (%d KB)" % (total // 1024)))
                self.root.after(0, lambda: self.blank_btn.config(
                    text="Blank \u2713", bg="#1a4a1a", fg="#88ff88"))
            else:
                body = ("Flash is NOT blank — first non-0xFF byte at 0x%08X, "
                        "%d byte(s) differ from the erased state (of %d read)." % (first_nonff, nonff, bytes_read))
                self._ai_write("── Blank Check ──\n\n%s\n" % body)
                self.root.after(0, lambda: self._progress_stop(
                    prog, "Blank Check: NOT blank — first non-FF at 0x%08X" % first_nonff))
                self.root.after(0, lambda: self.blank_btn.config(
                    text="Blank \u2717", bg="#4a1a1a", fg="#ff8888"))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Snapshot / upload (revived from the retired DevCon facility).
    # ------------------------------------------------------------------ #
    def _find_console_win(self):
        """Return the X window id of this Regmon-RE window, or None."""
        try:
            out = subprocess.run(
                ["xdotool", "search", "--name", "Regmon-RE"],
                capture_output=True, text=True, timeout=6).stdout.split()
            if out:
                return out[-1]
        except Exception:
            pass
        return None

    def _capture(self):
        """Capture this Regmon-RE window (monitor 0).  Returns
        (console_path, console_path) on success, or (None, None).  The Regmon
        display (screen :0.1) is no longer captured — we only snapshot the
        console now."""
        try:
            os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        except OSError:
            pass
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        console_path = os.path.join(SCREENSHOTS_DIR, f"console-{ts}.png")
        env = os.environ.copy()

        win = self._find_console_win()
        env["DISPLAY"] = ":0"
        if win:
            try:
                subprocess.run(["maim", "-x", ":0", "-i", win, console_path],
                               timeout=10, capture_output=True, env=env)
            except Exception:
                console_path = None
            if console_path and (not os.path.isfile(console_path)
                                 or os.path.getsize(console_path) < 100):
                console_path = None
        else:
            console_path = None

        if not console_path:
            self.update_status("Capture failed (console window not found)")
            return None, None

        return console_path, console_path

    def snapshot(self):
        """Capture the Regmon-RE window and save locally. No upload.
        The full path is auto-copied to the clipboard so Terry can paste it
        without needing to read the on-screen text."""
        console_path, _ = self._capture()
        if not console_path:
            return
        self.last_snapshot_label.config(text=f"Last snapshot: {console_path}")
        self._copy_to_clipboard(console_path)
        self.update_status(f"Snapshot saved (path copied): {console_path}")

    def upload_snapshot(self):
        """Capture the Regmon-RE window, commit to the GitHub schematics
        repo, push, and post the raw URL to the swdai chatroom."""
        console_path, _ = self._capture()
        if not console_path:
            return
        uploaded = []
        self.update_status("Pushing to GitHub…")
        self.root.update()
        try:
            basename = os.path.basename(console_path)
            # copy the snapshot from the fossil checkout's pics/ into the git
            # mirror so git add sees it (SCREENSHOTS_DIR and UPLOAD_REPO differ)
            import shutil
            mirror_pic = os.path.join(UPLOAD_REPO, "pics", basename)
            os.makedirs(os.path.dirname(mirror_pic), exist_ok=True)
            shutil.copy2(console_path, mirror_pic)
            subprocess.run(["git", "add", f"pics/{basename}"], cwd=UPLOAD_REPO,
                           capture_output=True, timeout=10)
            uploaded.append(basename)
            if uploaded:
                subprocess.run(
                    ["git", "commit", "-m",
                     f"Regmon console snapshot {datetime.now().strftime('%Y%m%d-%H%M%S')}"],
                    cwd=UPLOAD_REPO, capture_output=True, timeout=10)
                r = subprocess.run(["git", "push"], cwd=UPLOAD_REPO,
                                   capture_output=True, text=True, timeout=30)
            else:
                r = None
        except Exception as e:
            self.update_status(f"Upload failed: {e}")
            return
        if r is not None and r.returncode != 0:
            self.update_status(f"Push failed: {r.stderr[:90]}")
            return
        urls = [f"{RAW_URL}/{b}" for b in uploaded]
        if urls:
            self._post_to_chat("\n".join(urls))
            self.last_snapshot_label.config(text=f"Last snapshot: {console_path}")
            self._copy_to_clipboard(console_path)
            self.update_status(f"Uploaded: {len(urls)} image(s) — {console_path} (path copied)")
        else:
            self.update_status("Nothing to upload")

    def _post_to_chat(self, text):
        """Post a message to the swdai chatroom as the local user."""
        import urllib.request
        import urllib.parse
        lmtime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        data = urllib.parse.urlencode({
            "msg": f"Regmon snapshot:\n{text}",
            "lmtime": lmtime,
        }).encode()
        try:
            req = urllib.request.Request(CHAT_SEND_URL, data=data,
                                         headers={"Referer": CHAT_REFERER})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            self.update_status(f"Uploaded {text} (chat post failed: {e})")

    def selected_text(self):
        """Return the selected register's name and current value as text."""
        group, reg = self.current_register()
        if not group:
            return ""
        key = f"CPU_{reg}" if group == "CPU" else f"{group}_{reg}"
        v = self.values.get(key)
        vstr = f"0x{v:08x}" if v is not None else "—"
        if group == "CPU":
            name = f"CPU_{reg}"
        else:
            name = f"{group}_{reg}"
        return f"{name} = {vstr}"

    def _copy_to_clipboard(self, text):
        """Copy text to the system clipboard."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            return True
        except Exception:
            return False

    def copy_selected(self):
        text = self.selected_text()
        if not text:
            self.update_status("Nothing selected to copy")
            return
        self._copy_to_clipboard(text)
        self.update_status(f"Copied: {text}")

    def _show_copy_menu(self, event):
        # Select the row under the cursor first.
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
        menu = tk.Menu(self.root, tearoff=0, bg="#1a1a2e", fg=FG,
                       font=FONT_SMALL)
        menu.add_command(label="Copy register", command=self.copy_selected)
        menu.add_command(label="Show bitfields", command=self.select_current)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def close(self):
        self._stop = True


def main():
    # Single-instance guard: don't stack up duplicate consoles.
    # Use the PID-file approach (reliable, no pgrep races).
    import fcntl
    pidfile = os.path.expanduser("~/.regmon-console.pid")
    fd = os.open(pidfile, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Regmon-RE already running — exiting.")
        return
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    root = tk.Tk()
    app = RegmonConsole(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
