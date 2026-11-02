#!/bin/bash
# Start the election-night poller in the background (one command).
# Usage: ./start.sh            -> starts now, polls every 3 minutes, pushes to GitHub
#        ./start.sh --start-at 2026-11-03T20:00:00-05:00   -> waits until 8 pm Eastern
cd "$(dirname "$0")"
if [ -f poll.pid ] && kill -0 "$(cat poll.pid)" 2>/dev/null; then
  echo "poller already running (pid $(cat poll.pid)); ./stop.sh first"; exit 1
fi
mkdir -p logs
nohup .venv/bin/python -u poll.py --chambers senate,house "$@" >> logs/poll.out 2>&1 &
echo $! > poll.pid
echo "poller started, pid $(cat poll.pid). Log: tail -f logs/poll.log"
