param([ValidateSet('Start','Stop','Restart','Status')][string]$Action='Status')
$ErrorActionPreference='Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runtimePath = Join-Path $projectRoot '.local'

function Get-ProductProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -eq $pythonPath -and
        ($_.CommandLine -match '-m product\.worker(?:\s|$)' -or $_.CommandLine -match '-m uvicorn product\.api:create_app(?:\s|$)')
    })
}

if ($Action -in @('Stop','Restart')) {
    foreach ($process in (Get-ProductProcesses)) {
        # Only the validated launcher rooted in this checkout, including its child process.
        & taskkill.exe /PID $process.ProcessId /T /F
        if ($LASTEXITCODE -ne 0) { throw 'Unable to stop owned product service' }
    }
}
if ($Action -in @('Start','Restart')) {
    $active = Get-ProductProcesses
    if (-not ($active | Where-Object { $_.CommandLine -match '-m uvicorn ' })) {
        $apiProcess=Start-Process -FilePath $pythonPath -ArgumentList '-m uvicorn product.api:create_app --factory --host 127.0.0.1 --port 18080 --no-access-log' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimePath 'api.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'api.stderr.log') -PassThru
        Write-Output "API launcher started: $($apiProcess.Id)"
    }
    if (-not ($active | Where-Object { $_.CommandLine -match '-m product\.worker' })) {
        $workerProcess=Start-Process -FilePath $pythonPath -ArgumentList '-m product.worker' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimePath 'worker.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'worker.stderr.log') -PassThru
        Write-Output "Worker launcher started: $($workerProcess.Id)"
    }
    $healthy=$false
    for ($attempt=0; $attempt -lt 15; $attempt++) {
        try {
            $health=Invoke-RestMethod -Uri 'http://127.0.0.1:18080/health' -TimeoutSec 1
            if ($health.status -eq 'ok') { $healthy=$true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $healthy) { throw 'API health check failed; inspect .local/api.stderr.log' }
    Write-Output "API healthy: stage=$($health.stage) mode=$($health.mode)"
}
Get-ProductProcesses | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress
