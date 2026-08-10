# Regmon-RE — Programming the Chip (the SWD ring buffer & the OFF/ON dance)

_How Regmon-RE talks to the chip, and why you switch SWD OFF before flashing —
and back ON after. Works off-the-shelf with Forth; a small habit for anything
else._

## The core idea: one probe, two masters

The ST-Link is a **single-owner** device. Only one program can hold the USB
claim at a time:

- **Regmon-RE** (through `swdd`) holds it to read registers live.
- **st-flash** needs it to write new firmware.

They cannot both hold it at once. That is the whole reason for the SWD buttons.

## How Regmon-RE reads the chip (the synchronized ring buffer)

When the chip runs **Mecrisp-Stellaris Forth** (the default firmware on these
bench boards), Regmon-RE talks through a **synchronized circular ring buffer**
in the chip's RAM. The Forth kernel and the host (`swdd`) share this buffer:

- the kernel writes replies into the ring,
- the host reads them out over the debug wire,
- both sides coordinate so neither overwrites the other.

This is why register reads work **live, non-invasively** — the CPU keeps
running its program while Regmon-RE peeks at the silicon through the ring. No
halt, no pause, no interference. (This is the mechanism from the Regmon v2
podcast — the "synchronized circular buffer" that lets Regmon and Forth share
the same USB port.)

With a Mecrisp kernel in flash, Regmon-RE works **off the shelf** — nothing to
install on the chip side; the ring buffer is already there.

## If you are not running Forth (C, Python, bare metal, etc.)

The ring buffer is a Forth-kernel feature. If your firmware is C, Python-driven,
or bare metal, Regmon-RE still reads registers fine over the SWD wire — but the
chip is running your program, not a Forth kernel, so the ring-buffer dance
doesn't apply on the target side. What still matters is the **single-owner
probe**: swdd holds the claim while the console is reading, so flashing still
needs the OFF/ON dance below.

## The programming workflow (SWD OFF → flash → SWD ON)

Painless — two clicks, every time:

1. In the console, click **SWDD OFF** (red). This releases the ST-Link claim.
2. Flash your new binary with **st-flash**:
   ```
   st-flash write firmware.bin 0x08000000
   ```
3. Click **SWDD ON** (green). swdd reconnects and the live registers come back.

That's it. The buttons show the real state (solid block = current), so you
always know whether the probe is yours or free for flashing.

## Why it matters

Without the dance: st-flash fails with "unable to claim" (swdd still holds the
probe), or Regmon-RE reads garbage (st-flash stomped the ring while swdd was
reading). With it: flashing and monitoring coexist cleanly, no terminal juggling
— the buttons are right there in the console.

## If the chip is already running Forth

You usually don't even need to think about this for normal use — Regmon-RE just
works. The OFF/ON dance is only for the moment you want to write new firmware.
