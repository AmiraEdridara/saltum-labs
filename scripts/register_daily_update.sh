#!/usr/bin/env bash
# Git Bash version of register_daily_update.ps1. It runs that script, which registers the
# Saltum Labs daily update in Windows Task Scheduler for the current user only.
# The task keeps its existing name, "Macro Wire daily update".
# Run it yourself, once, from the project folder:
#     bash scripts/register_daily_update.sh
# Remove the task again with:
#     powershell.exe -NoProfile -Command 'Unregister-ScheduledTask -TaskName "Macro Wire daily update" -Confirm:$false'
set -euo pipefail
cd "$(dirname "$0")/.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/register_daily_update.ps1
