$action = New-ScheduledTaskAction `
    -Execute "C:\Users\vy.nguyenth\gmail-classifer\.venv\Scripts\python.exe" `
    -Argument "C:\Users\vy.nguyenth\gmail-classifer\dispute_tracker.py poll" `
    -WorkingDirectory "C:\Users\vy.nguyenth\gmail-classifer"

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
    -TaskName "DisputeTrackerPoller" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force

Write-Host "Task registered. Checking status..."
Get-ScheduledTask -TaskName "DisputeTrackerPoller" | Select-Object TaskName, State
