#!/usr/bin/env python3
"""regmon-program-analyze.py — prototype: whole-chip activity fingerprint.

Extends regmon-analyze.py from ONE register to the WHOLE program:

  1. Read the RCC enable registers (AHBENR, APB1ENR, APB2ENR) to find which
     peripherals have their clock enabled — disabled ones are skipped.
  2. For each ACTIVE peripheral, classify its registers as DYNAMIC (status,
     counter, data, interrupt) or STATIC (config: CR, MODER, AFRH, etc).
  3. Sample the dynamic registers uniformly (default 10 Hz over ~2 s = 20
     samples) via swdd; read static registers once.
  4. Compute deltas: which registers changed (active/toggling), which stayed
     frozen (possibly in-use-but-locked, or unused-but-clock-enabled).
  5. Feed the whole fingerprint (RCC set + deltas + static configs) to the
     local gateway AI, grounded against the SVD/RM/knowledge base.

Usage:
  regmon-program-analyze.py                # full fingerprint + AI verdict
  regmon-program-analyze.py --json         # emit the raw fingerprint only
  regmon-program-analyze.py --rate 20 --samples 40   # 20 Hz, 40 samples (~2s)

Exit code 0 = verdict produced; 1 = swdd down / AI unavailable.
"""
import os
import sys
import json
import time
import socket
import sqlite3
import subprocess
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, ".."))
SVD_DB = os.path.join(_REPO, "databases", "STM32F051.db")
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

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:9001")
GATEWAY_MODEL = os.environ.get("GATEWAY_MODEL", "koda")

# RCC enable registers (AHBENR / APB1ENR / APB2ENR) are built DYNAMICALLY from
# the (chip-aware) SVD database: the enable-bit field names (IOPAEN, USART1EN,
# TIM2EN, ...) already carry the peripheral name and bit offset.  This avoids a
# hardcoded F0 map being wrong on the F1 (where GPIO clocks live in APB2ENR bits
# 2-4, not AHBENR bits 17-19).
RCC_EN = None  # lazily built by build_rcc_en()

EN_NAME_MAP = {
    "AHBENR": {"DMA1": "DMA", "DMA2": "DMA"},
    "APB1ENR": {"PWREN": "PWR", "BKPEN": "BKP", "DACEN": "DAC", "CECEN": "CEC"},
    "APB2ENR": {"SYSCFGEN": "SYSCFG", "AFIOEN": "AFIO", "ADC1EN": "ADC",
                "ADC2EN": "ADC", "ADC3EN": "ADC", "TIM9EN": "TIM9",
                "TIM10EN": "TIM10", "TIM11EN": "TIM11", "TIM8EN": "TIM8",
                "TIM15EN": "TIM15", "TIM16EN": "TIM16", "TIM17EN": "TIM17"},
}


def build_rcc_en():
    """Build {ENREG: {bit: peripheral}} from the SVD database field table.
    Field names like 'IOPAEN' or 'USART1EN' name the enabled peripheral; the
    bit offset comes from the DB.  Falls back to the F0 hardcoded map if the
    DB is unavailable."""
    if not os.path.isfile(SVD_DB):
        return None
    en = {}
    try:
        conn = sqlite3.connect(SVD_DB)
        rows = conn.execute(
            "SELECT register_name, name, bitOffset FROM field "
            "WHERE peripheral_name='RCC' AND "
            "(register_name IN ('AHBENR','APB1ENR','APB2ENR')) "
            "ORDER BY register_name, bitOffset").fetchall()
        conn.close()
    except Exception:
        return None
    for reg, fname, bit in rows:
        if not fname:
            continue
        if bit is None:
            continue
        peri = None
        if fname.endswith("EN"):
            base = fname[:-2]
            # strip a leading IOP -> GPIO (IOPAEN -> GPIOA)
            if base.startswith("IOP") and len(base) >= 4:
                peri = "GPIO" + base[3]
            elif base in EN_NAME_MAP.get(reg, {}):
                peri = EN_NAME_MAP[reg][base]
            else:
                peri = base
        if peri:
            en.setdefault(reg, {})[int(bit)] = peri
    return en or None

# Registers that are DYNAMIC (change during execution) per peripheral family.
# A register is dynamic if its name contains one of these markers.
DYNAMIC_MARKERS = ("SR", "CNT", "DR", "DATA", "ISR", "IFR", "CCR", "RDR",
                   "TDR", "RX", "TX", "COUNT", "VAL", "STATUS", "FLAG",
                   "PSR", "TXE", "RXNE")

# Registers to skip entirely (pure control we don't need for the fingerprint).
SKIP_MARKERS = ("RSTR",)  # reset registers: reading is harmless but noisy


def read_mem_word(addr):
    """Read a 32-bit word via the swdd daemon. Mirrors regmon-analyze."""
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


def read_flash_bytes(addr, length):
    """Read raw flash bytes via the swdd daemon (256-byte chunks)."""
    out = bytearray()
    while length > 0:
        n = min(256, length)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(SWDD_SOCK)
            s.sendall((f"mem {addr:x} {n}\n").encode())
            s.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            s.close()
        except Exception:
            break
        for line in data.decode(errors="replace").split("\n"):
            line = line.strip()
            if not line or line.startswith("MEM") or line == ".":
                continue
            if ":" not in line:
                continue
            try:
                for b in line.split(":")[1].strip().split():
                    out.append(int(b, 16))
            except ValueError:
                continue
        addr += n
        length -= n
    return bytes(out)


def mecrisp_banner():
    """Look for the Mecrisp-Stellaris banner in the first KB of flash.  Returns
    the banner text if found (proves the chip runs Mecrisp-Stellaris, so a
    frozen-PC spinloop is the kernel's normal wait-for-input, not a hang), else
    None."""
    try:
        flash = read_flash_bytes(0x08000000, 1024)
    except Exception:
        return None
    idx = flash.find(b"Mecrisp-Stellaris")
    if idx < 0:
        return None
    # take the text up to the next newline OR the first non-printable byte
    end = idx
    while end < len(flash):
        c = flash[end]
        if c == 0x0A or not (32 <= c < 127):
            break
        end += 1
    try:
        return flash[idx:end].decode("ascii", errors="replace").strip()
    except Exception:
        return None


def read_cpu_reg(reg_num):
    """Read CPU register N (0-15) via the swdd 'reg' command."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(SWDD_SOCK)
        s.sendall(("reg %d\n" % reg_num).encode())
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
        if "= 0x" in line:
            try:
                return int(line.split("= 0x")[1].split()[0], 16)
            except (IndexError, ValueError):
                return None
    return None


def cpu_activity(n_samples=6, interval=0.15):
    """Return a dict describing CPU activity independent of peripheral clocks.

    A Mecrisp Forth kernel runs on the CPU via the SWD ring buffer with NO
    peripheral clocks enabled, so RCC-gating alone would wrongly call an active
    chip 'idle'.  Sampling the CPU register set (R0-R15) over time reveals
    whether the core is actually executing: any register that changes between
    samples means code is running.  PC (R15) and SP (R13) are singled out in
    the report because a moving PC is the definitive "executing" signal.

    Returns {"regs": {R0..R15: [samples]}, "changing": [...], "pc": [...],
             "sp": [...], "running": bool, "sampled": int}.
    """
    regs = {}
    for r in range(16):
        regs["R%d" % r] = []
    for _ in range(n_samples):
        for r in range(16):
            v = read_cpu_reg(r)
            if v is not None:
                regs["R%d" % r].append(v)
        time.sleep(interval)

    changing = [name for name, s in regs.items() if len(set(s)) > 1]
    pcs = regs.get("R15", [])
    sps = regs.get("R13", [])
    return {
        "regs": regs,
        "changing": changing,
        "pc": pcs,
        "sp": sps,
        "running": len(set(pcs)) > 1 or len(set(sps)) > 1 or bool(changing),
        "sampled": len(pcs),
    }


def reg_address(peripheral, register):
    """Look up a register's address from the SVD database."""
    if not os.path.isfile(SVD_DB):
        return None
    try:
        conn = sqlite3.connect(SVD_DB)
        row = conn.execute(
            "SELECT r.address FROM register r "
            "WHERE r.peripheral_name = ? AND r.name = ?",
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


def enumerate_peripheral_registers():
    """Return {PERIPHERAL: {REGISTER: addr}} from the SVD database."""
    out = {}
    try:
        conn = sqlite3.connect(SVD_DB)
        rows = conn.execute(
            "SELECT peripheral_name, name, address FROM register "
            "ORDER BY peripheral_name, name").fetchall()
        conn.close()
    except Exception:
        return out
    for peri, reg, addr in rows:
        if not addr:
            continue
        try:
            a = int(addr.replace("$", "0x"), 16)
        except ValueError:
            continue
        out.setdefault(peri.upper(), {})[reg.upper()] = a
    return out


def rcc_active_peripherals():
    """Read RCC enable registers; return the set of clock-enabled peripherals."""
    active = set()
    rcc_read_ok = False
    rcc = build_rcc_en()
    if not rcc:
        return active, rcc_read_ok
    for enreg, bitmap in rcc.items():
        addr = reg_address("RCC", enreg)
        if addr is None:
            continue
        val = read_mem_word(addr)
        if val is None:
            continue
        rcc_read_ok = True
        for bit, peri in bitmap.items():
            if val & (1 << bit):
                active.add(peri)
    return active, rcc_read_ok


def is_dynamic(regname):
    return any(m in regname for m in DYNAMIC_MARKERS)


def fingerprint(rate=10, samples=20):
    """Capture the whole-chip fingerprint. Returns a dict for the AI.

    RCC-gating only: exactly the peripherals whose clock-enable bit is set are
    fingerprinted.  If only a couple are enabled, only those are analysed — no
    fallback to scanning everything.  If none are enabled, the report is empty
    (and the caller says so honestly)."""
    active, rcc_read_ok = rcc_active_peripherals()
    all_regs = enumerate_peripheral_registers()

    # RCC-gating is NOT a reliable idle detector: a Mecrisp Forth kernel runs on
    # the CPU via the SWD ring buffer with no peripheral clocks at all.  Always
    # sample CPU activity (PC/SP) so an active core is never reported 'idle'
    # just because no peripheral clock is enabled.
    cpu = cpu_activity()

    # Debug core status (DHCSR @ 0xE000EDF0): the ground truth for whether the
    # core is actually sleeping (S_SLEEP), halted (S_HALT), or executing
    # (S_RETIRE_ST).  Lets the AI tell WFI-idle from a busy-wait spinloop.
    debug = {}
    d = read_mem_word(0xE000EDF0)
    if d is not None:
        debug = {
            "dhcsr": "0x%08x" % d,
            "S_HALT": bool(d & (1 << 1)),
            "S_SLEEP": bool(d & (1 << 18)),
            "S_LOCKUP": bool(d & (1 << 19)),
            "S_RETIRE_ST": bool(d & (1 << 24)),
        }

    # Mecrisp-Stellaris banner check: if present, a frozen-PC spinloop is the
    # kernel's normal wait-for-input, not a hang.  Gives the AI certainty instead
    # of telling it to check flash itself.
    banner = mecrisp_banner()

    scan_peris = [p for p in sorted(active) if p in all_regs]

    result = {
        "rcc_enabled": sorted(active),
        "rcc_read_ok": rcc_read_ok,
        "cpu_activity": cpu,
        "debug_status": debug,
        "mecrisp": banner,
        "rate_hz": rate,
        "samples": samples,
        "duration_s": round(samples / rate, 2),
        "peripherals": {},
    }

    for peri in scan_peris:
        regs = all_regs.get(peri, {})
        if not regs:
            continue
        dynamic = [r for r in regs if is_dynamic(r)
                   and not any(s in r for s in SKIP_MARKERS)]
        static = [r for r in regs
                  if not is_dynamic(r) and not any(s in r for s in SKIP_MARKERS)]

        peri_info = {"static": {}, "dynamic": {}}

        # Static config registers: read once.
        for r in static:
            v = read_mem_word(regs[r])
            if v is not None:
                peri_info["static"][r] = v

        # Dynamic registers: uniform time series, then delta analysis.
        for r in dynamic:
            addr = regs[r]
            series = []
            for _ in range(samples):
                v = read_mem_word(addr)
                if v is None:
                    break
                series.append(v)
                time.sleep(1.0 / rate)
            if not series:
                continue
            uniq = set(series)
            peri_info["dynamic"][r] = {
                "values": series,
                "n_samples": len(series),
                "changed": len(uniq) > 1,
                "distinct": len(uniq),
                "last": series[-1],
            }

        result["peripherals"][peri] = peri_info

    return result


def compare_fingerprints(a, b):
    """Diff two captured fingerprints (chip A vs chip B) and return a list of
    human-readable difference lines.

    Both a and b are dicts as produced by fingerprint() (--json).  The compare
    is machine-oriented: identical fields are ignored; only differences are
    reported, so running the SAME program on two chips (e.g. a genuine F103 vs
    an APM32/GD32 clone) reveals how the silicon differs.

    Returns a list of strings (empty = fingerprints identical at this depth).
    """
    out = []

    # Chip identity / RCC
    ra, rb = set(a.get("rcc_enabled") or []), set(b.get("rcc_enabled") or [])
    if ra != rb:
        out.append("RCC: A=%s B=%s" % (sorted(ra), sorted(rb)))

    # CPU activity
    ca, cb = a.get("cpu_activity") or {}, b.get("cpu_activity") or {}
    ca_chg = set(ca.get("changing") or [])
    cb_chg = set(cb.get("changing") or [])
    if ca_chg != cb_chg:
        out.append("CPU changing regs: A=%s B=%s" % (sorted(ca_chg), sorted(cb_chg)))
    pa, pb = ca.get("pc") or [], cb.get("pc") or []
    if pa != pb:
        out.append("CPU PC: A=%s B=%s" % (pa, pb))
    if bool(ca.get("running")) != bool(cb.get("running")):
        out.append("CPU running: A=%s B=%s" % (ca.get("running"), cb.get("running")))

    # Peripherals present on either
    peris = sorted(set(a.get("peripherals") or {}) | set(b.get("peripherals") or {}))
    for peri in peris:
        pa_, pb_ = (a.get("peripherals") or {}).get(peri), \
                   (b.get("peripherals") or {}).get(peri)
        if pa_ is None or pb_ is None:
            out.append("%s: present only on %s" % (peri, "A" if pa_ else "B"))
            continue
        # Static config values
        sa, sb = pa_.get("static") or {}, pb_.get("static") or {}
        for reg in sorted(set(sa) | set(sb)):
            va, vb = sa.get(reg), sb.get(reg)
            if va != vb:
                out.append("%s.%s static: A=0x%08x B=0x%08x"
                           % (peri, reg, va or 0, vb or 0))
        # Dynamic registers: compare last value + whether toggling
        da, db_ = pa_.get("dynamic") or {}, pb_.get("dynamic") or {}
        for reg in sorted(set(da) | set(db_)):
            da_i, db_i = da.get(reg), db_.get(reg)
            la = da_i.get("last") if da_i else None
            lb = db_i.get("last") if db_i else None
            ta = bool(da_i.get("changed")) if da_i else False
            tb = bool(db_i.get("changed")) if db_i else False
            if la != lb or ta != tb:
                out.append("%s.%s dynamic: A=last 0x%08x toggling=%s "
                           "B=last 0x%08x toggling=%s"
                           % (peri, reg, la or 0, ta, lb or 0, tb))

    return out


def build_prompt(fp):
    """Assemble the AI prompt from the fingerprint."""
    lines = [
        "You are debugging a live STM32. Below is a whole-chip activity "
        "fingerprint captured over a few seconds via SWD (background memory "
        "access, zero CPU overhead).",
        "",
        f"Clock-enabled peripherals (RCC): {', '.join(fp['rcc_enabled']) or 'none reported'}",
    ]
    ca = fp.get("cpu_activity") or {}
    if ca.get("sampled"):
        pc_uniq = len(set(ca.get("pc") or []))
        changing = ca.get("changing") or []
        if ca.get("running"):
            state = ("CPU EXECUTING — %d of R0-R15 changing" % len(changing))
        else:
            state = ("CPU registers frozen (no register changed) — could be "
                     "WFI/sleep, debugger-halted, OR a tight spin-wait loop; "
                     "distinguish via DHCSR S_SLEEP and the instruction at the PC")
        lines.append(
            f"CPU activity: {state} — registers that changed during the window: "
            f"{', '.join(changing) or 'none'} (PC samples {ca.get('pc')}, "
            f"{pc_uniq} distinct).  INTERPRETATION RULES: a MOVING PC (multiple "
            "distinct values) means code is executing even if no peripheral clock "
            "is enabled (e.g. a Forth kernel using the SWD ring buffer).  A FROZEN "
            "PC is NOT proof of idle/sleep: if the core is not in WFI the chip may "
            "be in a busy-wait spinloop.  IMPORTANT: on a Mecrisp-Stellaris kernel "
            "a frozen-PC spinloop polling a console/UART flag is NORMAL idle — the "
            "kernel busy-waits for input with no peripheral clocks (S_SLEEP clear, "
            "instructions retiring).  That is a healthy Forth wait-state, NOT a "
            "deadlock.  Suspect a true hang only when the spinloop polls a flag "
            "with no way to ever be set AND no console/input source exists.  "
            "Distinguish WFI (core sleeps, S_SLEEP set) from an active spinloop "
            "(core running, S_SLEEP clear).  Check the instruction at the PC: WFI "
            "is 0xBF30.")
        ds = fp.get("debug_status") or {}
        if ds:
            lines.append(
                "Debug core status (DHCSR): "
                "S_HALT=%s S_SLEEP=%s S_LOCKUP=%s S_RETIRE_ST=%s (%s)"
                % (ds.get("S_HALT"), ds.get("S_SLEEP"), ds.get("S_LOCKUP"),
                   ds.get("S_RETIRE_ST"), ds.get("dhcsr", "")))
        banner = fp.get("mecrisp")
        if banner:
            lines.append(
                "FIRMWARE CONFIRMED: flash banner reads %r — the chip runs "
                "Mecrisp-Stellaris with the SWD terminal replacement.  So a "
                "frozen-PC spinloop is the kernel's NORMAL wait for input, NOT a "
                "hang." % banner)
        else:
            lines.append(
                "No Mecrisp-Stellaris banner found in the first KB of flash — "
                "the firmware is NOT a stock Mecrisp kernel, so a frozen-PC "
                "spinloop is more likely a genuine hang/deadlock.")
    lines += [
        f"Sampling: {fp['rate_hz']} Hz, {fp['samples']} samples, "
        f"~{fp['duration_s']}s.",
        "",
        "Per-register detail (static = config read once; dynamic = sampled "
        "over time, 'changed' says if the value moved during the window):",
    ]
    for peri, info in sorted(fp["peripherals"].items()):
        lines.append("")
        lines.append(f"--- {peri} ---")
        if info["static"]:
            lines.append("  static config:")
            for r, v in sorted(info["static"].items()):
                lines.append(f"    {r} = 0x{v:08x}")
        if info["dynamic"]:
            lines.append("  dynamic:")
            for r, d in sorted(info["dynamic"].items()):
                state = "TOGGLING" if d["changed"] else "frozen"
                lines.append(
                    f"    {r} = 0x{d['last']:08x} ({state}, "
                    f"{d['n_samples']} samples, {d['distinct']} distinct)")
    lines.append("")
    if not fp["peripherals"]:
        lines.append(
            "No peripherals have their clock enabled, but the CPU activity "
            "above tells you whether the core is actually running (a moving PC "
            "means code executes — e.g. a Forth kernel via the SWD ring buffer — "
            "even with no peripheral clocks).  Give your assessment of what the "
            "CPU is doing from the PC/SP samples, and whether the chip is "
            "genuinely idle or actively executing.  IMPORTANT: with no peripheral "
            "clocks, an ACTIVE core (S_SLEEP clear, instructions retiring) is "
            "usually a Mecrisp-Stellaris kernel busy-waiting for console input — "
            "that is NORMAL Forth idle, not a hang.  The banner above confirms "
            "whether the chip actually runs Mecrisp.  Only call it a "
            "hang/deadlock if the spinloop polls a flag with no input source to "
            "set it.  Genuine idle = the core is in WFI/sleep (S_SLEEP set) or "
            "halted.  Do not call an executing core 'idle'.")
    else:
        lines.append(
            "Give your assessment: which peripherals are actively doing something, "
            "which look configured-but-idle, and which are suspicious (clock "
            "enabled but register frozen — that can mean locked up, not just "
            "unused). Name the registers that decided your answer. Aim for a "
            "tight paragraph, then a short 'Most likely: ...' line. Start "
            "'Whole-program picture: ...'")
    return "\n".join(lines)


def opencode_chat(prompt):
    """Standalone AI via `opencode run` — any AI the user configured."""
    import shutil
    exe = os.environ.get("RE_OPENCODE", "opencode")
    agent = os.environ.get("RE_RUN_AGENT", "build")
    if not shutil.which(exe):
        return None
    try:
        proc = subprocess.run(
            [exe, "run", "--agent", agent, prompt],
            capture_output=True, text=True, timeout=int(os.environ.get("RE_AI_TIMEOUT", "120")))
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def gateway_chat(prompt):
    """Call the fossilcrew gateway's Coder agent (OpenAI-compatible API)."""
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
            return text
    except Exception:
        pass
    return None


def main():
    args = sys.argv[1:]
    debug_json = "--json" in args
    args = [a for a in args if a != "--json"]

    # --compare FILEA FILEB: diff two saved fingerprint JSON files.
    if "--compare" in args:
        i = args.index("--compare")
        fa = args[i + 1] if len(args) > i + 1 else ""
        fb = args[i + 2] if len(args) > i + 2 else ""
        if not (fa and fb):
            print("usage: regmon-program-analyze.py --compare <chipA.json> <chipB.json>",
                  file=sys.stderr)
            return 2
        try:
            with open(fa) as f:
                a = json.load(f)
            with open(fb) as f:
                b = json.load(f)
        except Exception as e:
            print("could not read fingerprint files: %s" % e, file=sys.stderr)
            return 1
        diffs = compare_fingerprints(a, b)
        if not diffs:
            print("Fingerprints identical at this depth.")
            return 0
        print("Differences between %s (A) and %s (B):" % (fa, fb))
        for d in diffs:
            print("  - %s" % d)
        return 0

    rate = 10
    samples = 20
    if "--rate" in args:
        i = args.index("--rate")
        rate = int(args[i + 1]); del args[i:i + 2]
    if "--samples" in args:
        i = args.index("--samples")
        samples = int(args[i + 1]); del args[i:i + 2]

    print("Capturing whole-chip fingerprint…", file=sys.stderr)
    fp = fingerprint(rate=rate, samples=samples)
    if not fp.get("rcc_read_ok"):
        print("Could not read RCC registers (swdd down or no response)",
              file=sys.stderr)
        return 1

    # No peripherals clocked is NOT 'nothing happening' — a Forth kernel can
    # run flat-out via the SWD ring buffer with zero peripheral clocks.  Only
    # a halted CPU is genuinely idle.  So we always analyse (the prompt carries
    # the CPU-activity finding).
    if not fp["peripherals"]:
        print("No clock-enabled peripherals — analysing CPU activity only.",
              file=sys.stderr)

    if debug_json:
        print(json.dumps(fp, indent=2))
        return 0

    prompt = build_prompt(fp)
    verdict = opencode_chat(prompt)
    if not verdict:
        verdict = gateway_chat(prompt)
    if not verdict:
        print("(AI unavailable — no opencode and no gateway responding)", file=sys.stderr)
        return 1
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
