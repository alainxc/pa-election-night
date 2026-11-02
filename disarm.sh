#!/bin/bash
# Remove the scheduled start. Does not stop a poller that is already running (use ./stop.sh).
launchctl bootout "gui/$(id -u)/com.pa-election-night.poll" 2>/dev/null && echo "disarmed" || echo "was not armed"
rm -f "$HOME/Library/LaunchAgents/com.pa-election-night.poll.plist"
