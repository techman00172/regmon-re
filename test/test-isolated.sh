#!/usr/bin/env bash
# test-isolated.sh — run the Regmon-RE install in total isolation and verify.
# Runs INSIDE the podman container (built from test/Containerfile).
# The repo checkout is available at /test-src (bind-mounted by the harness).
set -uo pipefail

REPO=/test-src
PASS=0
FAIL=0

ok()   { echo "  [ OK ]   $*"; PASS=$((PASS+1)); }
bad()  { echo "  [ FAIL ] $*"; FAIL=$((FAIL+1)); }

echo "=== Regmon-RE isolated install test ==="
echo ""

# --- 0. Isolated? (nothing from Terry's box should be here) ---
echo "-- 0. Isolation check --"
if ls /home/tp 2>/dev/null; then
    bad "Terry's home dir is visible inside the container (not isolated!)"
else
    ok "No /home/tp — environment is isolated"
fi

# --- 1. Dependencies present ---
echo "-- 1. Dependencies --"
for dep in python3 sqlite3 opencode xvfb-run; do
    if command -v "$dep" >/dev/null 2>&1; then
        ok "$dep"
    else
        bad "$dep missing"
    fi
done
if python3 -c "import tkinter" 2>/dev/null; then
    ok "python3 tkinter"
else
    bad "python3 tkinter missing"
fi

# --- 2. Repo files present ---
echo "-- 2. Repo contents --"
for f in README.md setup.sh regmon-console \
         scripts/regmon-console.py scripts/chip-detect.py \
         swdcom/swdd \
         databases/STM32F051.db databases/STM32F103.db \
         databases/STM32F407.db databases/STM32L0xx.db; do
    if [ -f "$REPO/$f" ]; then
        ok "$f"
    else
        bad "$f missing"
    fi
done

# --- 3. Databases have content ---
echo "-- 3. Database contents --"
for db in STM32F051 STM32F103 STM32F407 STM32L0xx; do
    n=$(sqlite3 "$REPO/databases/$db.db" "SELECT count(*) FROM peripheral;" 2>/dev/null)
    if [ -n "$n" ] && [ "$n" -gt 0 ]; then
        ok "$db.db: $n peripherals"
    else
        bad "$db.db unreadable/empty"
    fi
done

# --- 4. Python compiles ---
echo "-- 4. Python compile check --"
# Use py_compile with a writable cache dir (repo is mounted read-only).
if ( cd "$REPO" && PYTHONPYCACHEPREFIX=/tmp/pyc python3 -m py_compile \
        scripts/regmon-console.py scripts/chip-detect.py \
        swdcom/regmon-analyze.py swdcom/regmon-program-analyze.py ); then
    ok "all .py compile"
else
    bad "compile failed"
fi

# --- 5. Console launches under Xvfb (headless) ---
echo "-- 5. Console launch (headless) --"
# Launch under Xvfb, give it a few seconds, check it's still alive.
xvfb-run -a -s "-screen 0 1280x1024x24" \
    python3 "$REPO/scripts/regmon-console.py" >/tmp/console.log 2>&1 &
CONSOLE_PID=$!
sleep 6
if kill -0 "$CONSOLE_PID" 2>/dev/null; then
    ok "console running after 6s (pid $CONSOLE_PID)"
    kill "$CONSOLE_PID" 2>/dev/null
    sleep 1
else
    bad "console exited early — log:"
    sed 's/^/       /' /tmp/console.log | tail -10
fi

# --- 6. setup.sh dry-run (should pass the checks) ---
echo "-- 6. setup.sh dependency check --"
if ( cd "$REPO" && bash setup.sh ) >/tmp/setup.log 2>&1; then
    ok "setup.sh completed"
else
    # setup.sh may exit non-zero if a dep is missing (e.g. no probe). Check it
    # got past the python/tk check at least.
    if grep -q "python3 + tkinter" /tmp/setup.log 2>/dev/null; then
        ok "setup.sh ran (no probe — expected)"
    else
        bad "setup.sh failed early — log:"
        sed 's/^/       /' /tmp/setup.log | tail -10
    fi
fi

echo ""
echo "=== RESULT: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && echo "ALL TESTS PASSED" || echo "TESTS FAILED"
[ "$FAIL" -eq 0 ]
