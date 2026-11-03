# Registers a Windows scheduled task that runs outage_sync.py every 30 minutes.
# Run from this folder in a normal (non-admin) PowerShell:  .\register_task.ps1
param(
    [int]$EveryMinutes = 30,
    [string]$TaskName = "Chernihivoblenergo Outage Sync"
)

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { throw "pythonw.exe not found on PATH. Install Python 3.10+ and tick 'Add to PATH'." }

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\outage_sync.py`"" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
              -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$logon    = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
              -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger, $logon) `
    -Settings $settings -Description "Pushes Chernihivoblenergo outages to Google Calendar" `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - runs every $EveryMinutes min and at logon. Log: $dir\outage_sync.log"
