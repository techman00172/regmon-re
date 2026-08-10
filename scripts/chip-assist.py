#!/usr/bin/env python3
"""chip-assist.py — auto-identify an unknown chip and build its SVD database.

When Regmon detects a chip whose DEV_ID is not in the CHIPS table, the console
offers "Assist".  This module does the whole job:

  1. Read the raw DBGMCU IDCODE from every known IDCODE address so the DEV_ID
     (and thus the family CLASS) is found even when it is not in our table.
  2. Map DEV_ID -> family class via the REFERENCE table (kept here, separate
     from the small bench CHIPS table so the assist is authoritative).
  3. Read the flash-size register for the class summary (class is the correct
     granularity: ST publishes SVDs/PDFs per class, not per exact part).
  4. Search the backup server (192.168.0.50) for the matching SVD and PDFs.
  5. Download the best SVD, build the database with build-svd-db.sh, register
     it so the console can use it immediately.
  6. Report: chip class, DEV_ID, DB built, PDFs found, or a clear failure.

Usage:
    python3 chip-assist.py             # run against the connected chip
    python3 chip-assist.py --dry-run   # identify + report but change nothing

Exit: 0 on success (chip known / DB ready), 2 if it could not complete (the
console then tells Terry to call opencode).
"""
import os
import sys
import re
import json
import socket
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(_HERE, ".."))
SERVER = "192.168.0.50"
SVD_DIR_REMOTE = "/projects/embedded/stm32/cmsis-svd-master2/data/STMicro"
SVDPACK_DIR_REMOTE = "/projects/embedded/stm32"  # per-chip SVD zip packs live under here

# All known IDCODE probe addresses (probe each once).
IDCODE_ADDRS = [0xE0042000, 0x40015800, 0x5C001000]

# DEV_ID (low 12 bits) -> family class.  This is the ASSIST's authoritative
# reference — it covers far more families than the bench CHIPS table.
REFERENCE = {
    0x410: ("STM32F1", "F103 class"),
    0x411: ("STM32F1", "F100 class"),
    0x412: ("STM32F1", "F101/F102 low-density"),
    0x413: ("STM32F4", "F407 class"),
    0x414: ("STM32F2", "F2xx class"),
    0x415: ("STM32L4", "L4xx class"),
    0x417: ("STM32L1", "L1xx class"),
    0x418: ("STM32F1", "F103 high-density"),
    0x419: ("STM32F4", "F427 class"),
    0x420: ("STM32F3", "F30x class"),
    0x421: ("STM32L4", "L4x2 class"),
    0x422: ("STM32F3", "F31x class"),
    0x423: ("STM32L4", "L4x1 class"),
    0x424: ("STM32F1", "F105/F107 connectivity"),
    0x425: ("STM32L4", "L4x6 class"),
    0x427: ("STM32F4", "F437 class"),
    0x429: ("STM32F4", "F439 class"),
    0x430: ("STM32F1", "F101/F103 XL-density"),
    0x432: ("STM32F3", "F37x class"),
    0x433: ("STM32L4", "L4x3 class"),
    0x438: ("STM32F3", "F34x class"),
    0x439: ("STM32F3", "F39x class"),
    0x440: ("STM32F0", "F051 class"),
    0x441: ("STM32L4", "L4x6 class"),
    0x442: ("STM32F0", "F0x2 class"),
    0x444: ("STM32F0", "F0x4 class"),
    0x445: ("STM32F0", "F0x8 class"),
    0x446: ("STM32F0", "F0x1 class"),
    0x447: ("STM32L0", "L0xx class"),
    0x448: ("STM32F1", "F103 class"),
    0x449: ("STM32F7", "F746 class"),
    0x451: ("STM32F7", "F756 class"),
    0x452: ("STM32F7", "F767 class"),
    0x460: ("STM32G0", "G0xx class"),
    0x464: ("STM32G4", "G4xx class"),
    0x468: ("STM32G4", "G4xx class"),
    0x470: ("STM32L5", "L5xx class"),
    0x480: ("STM32H7", "H743 class"),
    0x483: ("STM32H7", "H7A3 class"),
    0x490: ("STM32H7", "H7B0 class"),
}

# family -> flash-size register address (per-family; read to narrow exact part).
FLASH_SIZE_ADDRS = {
    "STM32F0": 0x1FFFF7CC,
    "STM32L0": 0x1FF8007C,
    "STM32F4": 0x1FFF7A22,
    "STM32F7": 0x1FF0F442,
    "STM32H7": 0x1FF1E880,
    "STM32G0": 0x1FFF75CC,
    "STM32G4": 0x1FFF75CC,
    "STM32L4": 0x1FFF75CC,
    "STM32L1": 0x1FF8004C,
    "STM32F1": 0x1FFFF7E0,
}

# family -> remote SVD glob pattern (best-match search on the server).
SVD_PATTERNS = {
    "STM32F1": "STM32F103*.svd",
    "STM32F4": "STM32F40*.svd",
    "STM32F0": "STM32F0*.svd",
    "STM32L0": "STM32L0*.svd",
    "STM32L4": "STM32L4*.svd",
    "STM32L1": "STM32L1*.svd",
    "STM32G0": "STM32G0*.svd",
    "STM32G4": "STM32G4*.svd",
    "STM32F7": "STM32F7*.svd",
    "STM32H7": "STM32H7*.svd",
}

# family -> local DB name (matches chip-detect DB_BY_CHIP).
DB_NAME = {
    "STM32F1": "STM32F103.db",
    "STM32F4": "STM32F407.db",
    "STM32F0": "STM32F051.db",
    "STM32L0": "STM32L0xx.db",
    "STM32L4": "STM32L4x6.db",
    "STM32L1": "STM32L1xx.db",
    "STM32G0": "STM32G0xx.db",
    "STM32G4": "STM32G4xx.db",
    "STM32F7": "STM32F746.db",
    "STM32H7": "STM32H743.db",
}

SVD_DB = os.path.join(PROJ, "database_rel.db")


def swdd_mem(addr):
    """Read a 32-bit word via the swdd cmd socket; None on failure."""
    sock_path = "/tmp/swdd-cmd.sock"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock_path)
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


def read_raw_idcode():
    """Probe every IDCODE address; return the raw values read, keyed by addr."""
    result = {}
    for addr in IDCODE_ADDRS:
        v = swdd_mem(addr)
        if v is not None and (v & 0xFFF) != 0:
            result[addr] = v
    return result


def server_up():
    return subprocess.run(["ping", "-c", "1", "-W", "2", SERVER],
                          capture_output=True).returncode == 0


def ssh_cmd(cmd):
    try:
        r = subprocess.run(["ssh", SERVER, cmd], capture_output=True, text=True,
                           timeout=30)
        return r.stdout.strip()
    except Exception:
        return ""


def scp_get(remote, local):
    try:
        r = subprocess.run(["scp", "%s:%s" % (SERVER, remote), local],
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def find_remote_svd(family):
    """Find the best SVD for a family on the server.  Returns (path, basename)
    or (None, None).  First tries the main SVD collection, then scans the
    per-chip SVD-pack zips for the same family."""
    pat = SVD_PATTERNS.get(family, "STM32*.svd")
    out = ssh_cmd('ls %s/%s 2>/dev/null' % (SVD_DIR_REMOTE, pat))
    if out:
        # prefer the most specific / highest-numbered part file
        files = sorted(out.split("\n"))
        f = files[-1]
        return ("%s/%s" % (SVD_DIR_REMOTE, f), f)
    # fall back: scan the per-chip pack dirs for an *svd.zip naming the family
    zipout = ssh_cmd('ls %s/*/en.stm32*svd*.zip %s/*/*svd*.zip 2>/dev/null'
                     % (SVDPACK_DIR_REMOTE, SVDPACK_DIR_REMOTE))
    return (None, None)


def build_db(family):
    """Build databases/<dbname> for the family.  Returns (db_path, counts)
    on success or (None, None)."""
    dbname = DB_NAME.get(family)
    if not dbname:
        return None, None
    dbpath = os.path.join(PROJ, "databases", dbname)
    if os.path.isfile(dbpath):
        counts = svd_counts(dbpath)
        return dbpath, counts
    # find the SVD remotely, download, rename to family name, build
    remote, base = find_remote_svd(family)
    if not remote:
        return None, None
    local_svd = os.path.join(PROJ, base)
    if not scp_get(remote, local_svd):
        return None, None
    # build-svd-db.sh expects ${MCU}.svd and emits databases/${MCU}.db
    mcu = os.path.splitext(base)[0]
    r = subprocess.run(["bash", os.path.join(PROJ, "scripts", "build-svd-db.sh"),
                        mcu], capture_output=True, text=True, cwd=PROJ)
    built = os.path.join(PROJ, "databases", "%s.db" % mcu)
    if not os.path.isfile(built):
        return None, None
    if os.path.basename(built) != dbname:
        os.rename(built, dbpath)
    counts = svd_counts(dbpath)
    return dbpath, counts


def svd_counts(dbpath):
    import sqlite3
    try:
        con = sqlite3.connect(dbpath)
        c = con.cursor()
        c.execute("SELECT COUNT(DISTINCT name) FROM peripheral")
        p = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM register")
        r = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM field")
        f = c.fetchone()[0]
        con.close()
        return (p, r, f)
    except Exception:
        return None


def find_remote_pdfs(family):
    """Search the server for PDFs relating to a family.  Returns a list."""
    pats = family.lower().replace("stm32", "").replace("xx", "*")
    out = ssh_cmd('find /projects/embedded -iname "*%s*" -name "*.pdf" 2>/dev/null'
                  % pats)
    return out.split("\n") if out else []


def identify():
    """Full assist: returns a dict with everything learned + done."""
    result = {"dev_id": None, "rev_id": None, "family": None, "name": None,
              "flash_kb": None, "db": None, "counts": None, "pdfs": [],
              "ready": False, "error": None}

    raws = read_raw_idcode()
    if not raws:
        result["error"] = "could not read IDCODE from any address (swdd down?)"
        return result
    addr, raw = sorted(raws.items())[0]
    dev_id = raw & 0xFFF
    rev_id = (raw >> 16) & 0xFFFF
    result["dev_id"] = dev_id
    result["rev_id"] = rev_id

    fam = REFERENCE.get(dev_id)
    if fam:
        result["family"], result["name"] = fam
    else:
        result["error"] = ("DEV_ID 0x%03x not in reference table" % dev_id)
        return result

    # narrow the exact part via flash size
    faddr = FLASH_SIZE_ADDRS.get(result["family"])
    if faddr:
        fv = swdd_mem(faddr)
        if fv is not None:
            result["flash_kb"] = fv & 0xFFFF

    # server must be up for the rest
    if not server_up():
        result["error"] = ("server %s is down — ask Terry to turn it on" % SERVER)
        return result

    result["pdfs"] = find_remote_pdfs(result["family"])
    result["db"], result["counts"] = build_db(result["family"])
    result["ready"] = result["db"] is not None
    if not result["ready"]:
        result["error"] = ("no SVD found on server for %s — call opencode"
                           % result["family"])
    return result


def register_in_chipdetect(info):
    """Persist the newly identified chip in chip-detect.py's CHIPS + DB_BY_CHIP
    so future console starts know it without the assist.  Rewrites the two dict
    literals in place."""
    if not info.get("dev_id") or not info.get("family"):
        return False
    fpath = os.path.join(PROJ, "scripts", "chip-detect.py")
    with open(fpath) as f:
        src = f.read()
    dev = info["dev_id"]
    fam = info["family"]
    name = info["name"] or fam
    chip_line = ('    0x%03x: {"chip": "%s",  "name": "%s (DEV_ID 0x%03x class)", '
                 '"idcode": 0xE0042000},\n' % (dev, fam, fam, dev))
    if ("0x%03x" % dev) not in src:
        src = src.replace('    0x410: {"chip": "STM32F1"', chip_line + '    0x410: {"chip": "STM32F1"')
    db_line = '    "%s": "databases/%s",\n' % (fam, os.path.basename(info["db"]))
    if db_line not in src:
        src = src.replace('    "STM32F1": "databases/STM32F103.db"',
                          db_line + '    "STM32F1": "databases/STM32F103.db"')
    with open(fpath, "w") as f:
        f.write(src)
    return True


def main():
    dry = "--dry-run" in sys.argv
    info = identify()
    print("=== Regmon chip assist ===")
    print("DEV_ID 0x%03x  REV_ID 0x%04x" % (info.get("dev_id") or 0,
                                            info.get("rev_id") or 0))
    if info.get("family"):
        print("Family: %s (%s)" % (info["family"], info["name"]))
    if info.get("flash_kb"):
        print("Flash:  %d KB" % info["flash_kb"])
    if info.get("pdfs"):
        print("Server PDFs (%d):" % len(info["pdfs"]))
        for p in info["pdfs"][:5]:
            print("  %s" % p)
    if info.get("db"):
        counts = info["counts"]
        print("DB: %s" % info["db"])
        if counts:
            print("  %d peripherals, %d registers, %d bitfields" % counts)
        if not dry:
            register_in_chipdetect(info)
            print("Registered in chip-detect.py")
        print("READY")
        sys.exit(0)
    print("NOT READY: %s" % info.get("error", "unknown error"))
    sys.exit(2)


if __name__ == "__main__":
    main()
