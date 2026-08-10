#!/usr/bin/env python3
"""chip-detect.py — identify the connected STM32 from its IDCODE.

Regmon reads registers by address via swdd; it does not know which chip is
connected.  This module reads the DBGMCU IDCODE through the swdd cmd socket and
maps the DEV_ID to a chip family.  IMPORTANT (Terry's point): the IDCODE only
covers a FAMILY CLASS, not the exact part — 0x410 is the whole STM32F1
medium-density class (F103 etc.), 0x440 is the STM32F0 class (F051 etc.).  The
decoded name is shown as the best available guess, and the matching SVD database
is selected for register addresses.

Usage (import):
    from chip_detect import detect_chip, chip_db_path, CHIPS
    info = detect_chip()          # -> {"dev_id":0x410, "chip":"STM32F1","db":...}
    db = chip_db_path(info)       # -> path to the right SVD database

Exit-code CLI:
    python3 chip-detect.py        # prints "STM32F1 (DEV_ID 0x410)" etc.
"""
import os
import socket

_HERE = os.path.dirname(os.path.abspath(__file__))
SVD_DB = os.path.abspath(os.path.join(_HERE, "..", "databases", "STM32F051.db"))
SWDD_SOCK = "/tmp/swdd-cmd.sock"

# DEV_ID (low 12 bits of DBGMCU_IDCODE) -> chip family.  The DEV_ID identifies a
# CLASS, not the exact part.  Add more as the bench grows.  IDCODE address:
# F1/F4/F7 use DBGMCU @ 0xE0042000; F0/L0/G0/G4 use DBGMCU @ 0x40015800; H7 uses
# DBGMCU @ 0x5C001000.  A chip that does not match its expected IDCODE address
# responds with a bus-fault fill, so the wrong-address probe simply moves on.
CHIPS = {
    0x410: {"chip": "STM32F1",  "name": "STM32F1 (F103 class)", "idcode": 0xE0042000},
    0x413: {"chip": "STM32F4",  "name": "STM32F4 (F407 class)", "idcode": 0xE0042000},
    0x414: {"chip": "STM32F2",  "name": "STM32F2 (F2xx class)", "idcode": 0xE0042000},
    0x415: {"chip": "STM32L4",  "name": "STM32L4 (L4xx class)", "idcode": 0xE0042000},
    0x417: {"chip": "STM32L1",  "name": "STM32L1 (L1xx class)", "idcode": 0xE0042000},
    0x419: {"chip": "STM32F4",  "name": "STM32F4 (F429 class)", "idcode": 0xE0042000},
    0x421: {"chip": "STM32L4",  "name": "STM32L4 (L4x2 class)", "idcode": 0xE0042000},
    0x423: {"chip": "STM32L4",  "name": "STM32L4 (L4x1 class)", "idcode": 0xE0042000},
    0x425: {"chip": "STM32L4",  "name": "STM32L4 (L4x6 class)", "idcode": 0xE0042000},
    0x427: {"chip": "STM32F4",  "name": "STM32F4 (F437 class)", "idcode": 0xE0042000},
    0x429: {"chip": "STM32F4",  "name": "STM32F4 (F439 class)", "idcode": 0xE0042000},
    0x433: {"chip": "STM32L4",  "name": "STM32L4 (L4x3 class)", "idcode": 0xE0042000},
    0x440: {"chip": "STM32F0",  "name": "STM32F0 (F051 class)", "idcode": 0x40015800},
    0x441: {"chip": "STM32L4",  "name": "STM32L4 (L4x6 class)", "idcode": 0xE0042000},
    0x442: {"chip": "STM32F0",  "name": "STM32F0 (F0x2 class)", "idcode": 0x40015800},
    0x444: {"chip": "STM32F0",  "name": "STM32F0 (F0x4 class)", "idcode": 0x40015800},
    0x445: {"chip": "STM32F0",  "name": "STM32F0 (F0x8 class)", "idcode": 0x40015800},
    0x446: {"chip": "STM32F0",  "name": "STM32F0 (F0x1 class)", "idcode": 0x40015800},
    0x447: {"chip": "STM32L0",  "name": "STM32L0 (L0xx class)", "idcode": 0x40015800},
    0x448: {"chip": "STM32F1",  "name": "STM32F1 (F103 class)", "idcode": 0xE0042000},
    0x449: {"chip": "STM32F7",  "name": "STM32F7 (F746 class)", "idcode": 0xE0042000},
    0x451: {"chip": "STM32F7",  "name": "STM32F7 (F756 class)", "idcode": 0xE0042000},
    0x452: {"chip": "STM32F7",  "name": "STM32F7 (F767 class)", "idcode": 0xE0042000},
    0x460: {"chip": "STM32G0",  "name": "STM32G0 (G0xx class)", "idcode": 0x40015800},
    0x464: {"chip": "STM32G4",  "name": "STM32G4 (G4xx class)", "idcode": 0x40015800},
    0x468: {"chip": "STM32G4",  "name": "STM32G4 (G4xx class)", "idcode": 0x40015800},
    0x470: {"chip": "STM32L5",  "name": "STM32L5 (L5xx class)", "idcode": 0x40015800},
    0x480: {"chip": "STM32H7",  "name": "STM32H7 (H743 class)", "idcode": 0x5C001000},
    0x483: {"chip": "STM32H7",  "name": "STM32H7 (H7A3 class)", "idcode": 0x5C001000},
    0x490: {"chip": "STM32H7",  "name": "STM32H7 (H7B0 class)", "idcode": 0x5C001000},
}

# DEV_ID -> SVD database (one DB per chip family, same schema).
DB_BY_CHIP = {
    "STM32F1": "databases/STM32F103.db",
    "STM32F4": "databases/STM32F407.db",
    "STM32F0": "databases/STM32F051.db",
    "STM32L0": "databases/STM32L0xx.db",
    "STM32L4": "databases/STM32L4x6.db",
    "STM32G0": "databases/STM32G0xx.db",
    "STM32G4": "databases/STM32G4xx.db",
    "STM32F7": "databases/STM32F746.db",
    "STM32H7": "databases/STM32H743.db",
}


def swdd_mem(addr):
    """Read a 32-bit word via the swdd cmd socket; None on failure."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(SWDD_SOCK)
        s.sendall(("mem %x 4\n" % addr).encode())
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


def detect_chip():
    """Read the IDCODE and return a dict describing the connected chip.

    Returns {"dev_id": int, "chip": str, "name": str, "idcode": int,
             "unknown": bool}.  Probes each distinct IDCODE address once, then
    matches the DEV_ID against the CHIPS table.  On unknown/absent the result
    has unknown=True and chip=None.
    """
    # Probe each distinct IDCODE address exactly once (F1/F4/F7 share 0xE0042000,
    # F0/L0/G0/G4 share 0x40015800, H7 has its own).  On a mismatched chip the
    # read returns either None (unmapped -> swdd error) or a non-matching value
    # (bus-fault fill), so the DEV_ID simply won't match and we move on.
    seen = set()
    raw_by_addr = {}
    for meta in CHIPS.values():
        addr = meta["idcode"]
        if addr in seen:
            continue
        seen.add(addr)
        val = swdd_mem(addr)
        if val is not None:
            raw_by_addr[addr] = val
    for dev_id, meta in CHIPS.items():
        val = raw_by_addr.get(meta["idcode"])
        if val is None:
            continue
        got = val & 0xFFF
        if got == dev_id:
            return {
                "dev_id": dev_id,
                "chip": meta["chip"],
                "name": meta["name"],
                "idcode": meta["idcode"],
                "unknown": False,
            }
    # Fall back: no recognised chip class.  Report it plainly as unknown — do
    # NOT guess a class, and keep the existing F051 database so the console
    # still works (addresses will be read against it, but the header makes the
    # mismatch obvious).
    return {
        "dev_id": None,
        "chip": None,
        "name": "unknown chip",
        "idcode": None,
        "unknown": True,
    }


def chip_db_path(info=None):
    """Return the SVD database path for the detected chip.  Falls back to the
    F051 database (the historical default) when the chip DB file is absent.
    Standalone: databases live next to the scripts, in the repo's databases/."""
    info = info or detect_chip()
    here = os.path.dirname(os.path.abspath(__file__))
    db = DB_BY_CHIP.get(info["chip"], "STM32F051.db")
    full = os.path.join(here, "..", "databases", db)
    full = os.path.abspath(full)
    if os.path.isfile(full):
        return full
    return SVD_DB


def main():
    info = detect_chip()
    name = info["name"]
    if info["unknown"]:
        print(name)
    else:
        print("%s (DEV_ID 0x%03x @ 0x%08x)" % (name, info["dev_id"], info["idcode"]))
    print("db: %s" % chip_db_path(info))


if __name__ == "__main__":
    main()
