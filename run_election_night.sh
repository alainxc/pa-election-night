#!/bin/bash
# Launched by launchd at 7:55 pm on election night (see arm.sh). Keeps the Mac
# awake while it runs, resets rehearsal state, waits until 8:00 pm, then polls
# both chambers every 3 minutes and pushes. Stop with ./stop.sh.
cd "$(dirname "$0")"
mkdir -p logs
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f poll.pid ] && kill -0 "$(cat poll.pid)" 2>/dev/null; then
  echo "poller already running (pid $(cat poll.pid))"; exit 0
fi
echo "$$" > poll.pid
exec /usr/bin/caffeinate -i .venv/bin/python -u poll.py --reset-state --chambers senate,house \
  --start-at 2026-11-03T20:00:00-05:00 >> logs/poll.out 2>&1
