$ErrorActionPreference = 'Stop'

$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backendRoot = Join-Path $repository 'dist\KnoweBackend'
$backendExe = Join-Path $backendRoot 'KnoweBackend.exe'
$mxcExe = Join-Path $repository 'node_modules\@microsoft\mxc-sdk\bin\x64\wxc-exec.exe'
$launcherExe = Join-Path $repository 'build\native\knowe-sandbox-launcher.exe'
$smokeRoot = Join-Path $repository '.tmp-tests\packaged-backend-smoke'
$dataRoot = Join-Path $smokeRoot 'data'
$runtimeToken = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'

foreach ($required in @($backendExe, $mxcExe, $launcherExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Packaged backend smoke prerequisite missing: $required"
    }
}
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null

$variables = @(
    'KNOWE_RUNTIME_TOKEN', 'KNOWE_HEALTH_PORT', 'KNOWE_WS_PORT',
    'KNOWE_DATA_DIR', 'KNOWE_INSTALL_ROOT', 'KNOWE_MXC_EXECUTABLE',
    'KNOWE_SANDBOX_LAUNCHER', 'KNOWE_PACKAGED', 'KNOWE_AGENT'
)
$saved = @{}
foreach ($name in $variables) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$env:KNOWE_RUNTIME_TOKEN = $runtimeToken
$env:KNOWE_HEALTH_PORT = '18081'
$env:KNOWE_WS_PORT = '18080'
$env:KNOWE_DATA_DIR = $dataRoot
$env:KNOWE_INSTALL_ROOT = $backendRoot
$env:KNOWE_MXC_EXECUTABLE = $mxcExe
$env:KNOWE_SANDBOX_LAUNCHER = $launcherExe
$env:KNOWE_PACKAGED = '1'
$env:KNOWE_AGENT = 'fake'

$process = Start-Process -FilePath $backendExe -WorkingDirectory $backendRoot -PassThru -WindowStyle Hidden
try {
    $health = $null
    foreach ($attempt in 1..60) {
        Start-Sleep -Milliseconds 500
        try {
            $candidate = Invoke-RestMethod -Uri 'http://127.0.0.1:18081/health' `
                -Headers @{ 'X-Knowe-Runtime-Token' = $runtimeToken } -TimeoutSec 1
            if ($candidate.status -eq 'ok') {
                $health = $candidate
                break
            }
        } catch {
            # Cold PyInstaller startup may not have bound the loopback port yet.
        }
    }
    if ($null -eq $health) {
        throw 'Packaged backend did not become healthy within 30 seconds.'
    }

    $unauthenticatedStatus = 0
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:18081/health' -TimeoutSec 2 -ErrorAction Stop | Out-Null
    } catch {
        if ($null -ne $_.Exception.Response) {
            $unauthenticatedStatus = [int]$_.Exception.Response.StatusCode
        }
    }
    if ($unauthenticatedStatus -ne 401) {
        throw "Expected unauthenticated /health to return 401, got $unauthenticatedStatus."
    }
    Write-Output "Packaged backend smoke passed (pid=$($process.Id), unauthenticated=401)."
    $health | ConvertTo-Json -Compress
} finally {
    try {
        Invoke-WebRequest -Method POST -Uri 'http://127.0.0.1:18081/shutdown' `
            -Headers @{ 'X-Knowe-Runtime-Token' = $runtimeToken } -TimeoutSec 2 | Out-Null
    } catch {
        # The final process wait/kill below is the authoritative cleanup.
    }
    if (-not $process.WaitForExit(5000)) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    foreach ($name in $variables) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}
