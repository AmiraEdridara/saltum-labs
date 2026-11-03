# Registers the Saltum Labs daily update in Windows Task Scheduler, for the current user only.
# The task keeps its existing name, "Macro Wire daily update". Git Bash: bash scripts/register_daily_update.sh
# Run it yourself, once, from the project folder:
#     powershell -ExecutionPolicy Bypass -File scripts\register_daily_update.ps1
# Remove it again with:
#     Unregister-ScheduledTask -TaskName "Macro Wire daily update" -Confirm:$false
#
# Triggers: 21:45 and 22:45 UK time every day, and when you log on. The script itself checks New York time, so the
# weeks when UK and US clocks change on different dates do not matter: before 16:45 New York time (13:45 on early
# closes) it leaves today for the next run. "Run as soon as possible after a missed start" catches up when the PC
# was off. It runs only while you are logged on (no stored password).

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python not found at $python" }

$action = New-ScheduledTaskAction -Execute $python -Argument "daily_update.py --scheduled" -WorkingDirectory $project
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At "21:45"),
    (New-ScheduledTaskTrigger -Daily -At "22:45"),
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Macro Wire daily update" -Action $action -Trigger $triggers -Settings $settings `
    -Principal $principal -Description "Appends new trading days, events and 'priced now' to Saltum Labs (daily_update.py). Log: data\live\update_log.txt" `
    -Force | Out-Null

Write-Host "Registered 'Macro Wire daily update': daily at 21:45 and 22:45, and at log-on (only while you are logged on)."
Write-Host "Log: $project\data\live\update_log.txt"
