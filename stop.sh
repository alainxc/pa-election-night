#!/bin/bash
# Stop the poller.
cd "$(dirname "$0")"
if [ -f poll.pid ]; then
  kill "$(cat poll.pid)" 2>/dev/null && echo "stopped pid $(cat poll.pid)" || echo "poller was not running"
  rm -f poll.pid
else
  pkill -f "python -u poll.py" && echo "stopped" || echo "poller was not running"
fi
