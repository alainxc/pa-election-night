#!/bin/bash
# Arm the hands-off start: installs a per-user launchd job that runs
# run_election_night.sh at 7:55 pm on Nov 3, 2026 (local time). If the Mac is
# asleep at 7:55 the job fires as soon as it wakes. Run once, today: ./arm.sh
cd "$(dirname "$0")"
PLIST="$HOME/Library/LaunchAgents/com.pa-election-night.poll.plist"
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.pa-election-night.poll</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$PWD/run_election_night.sh</string></array>
  <key>StartCalendarInterval</key><dict>
    <key>Month</key><integer>11</integer><key>Day</key><integer>3</integer>
    <key>Hour</key><integer>19</integer><key>Minute</key><integer>55</integer>
  </dict>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>StandardOutPath</key><string>$PWD/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$PWD/logs/launchd.err</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.pa-election-night.poll" 2>/dev/null
if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
  echo "armed: the poller starts by itself at 7:55 pm on Nov 3 and begins polling at 8:00 pm."
  echo "Keep the Mac plugged in and logged in. Disarm with ./disarm.sh; stop a running poller with ./stop.sh."
else
  echo "could not load the launchd job; fallback: run ./start.sh --start-at 2026-11-03T20:00:00-05:00 and leave the terminal open."
fi
