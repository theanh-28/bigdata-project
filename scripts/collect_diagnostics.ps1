$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$diagRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'diagnostics'
$outDir = Join-Path $diagRoot $stamp
New-Item -ItemType Directory -Force $outDir | Out-Null
$report = Join-Path $outDir 'diagnostics.txt'

function Write-Section {
    param([string]$Title)
    Add-Content -Path $report -Value "`r`n============================================================`r`n$Title`r`n============================================================"
}
function Run-Capture {
    param([string]$Title, [string]$Exe, [string[]]$Args)
    Write-Section $Title
    try {
        $output = & $Exe @Args 2>&1 | Out-String
        Add-Content -Path $report -Value $output
        if ($LASTEXITCODE -ne 0) { Add-Content -Path $report -Value "Exit code: $LASTEXITCODE" }
    } catch {
        Add-Content -Path $report -Value "Could not run command: $($_.Exception.Message)"
    }
}

Write-Section 'Basic context'
Add-Content -Path $report -Value "Captured at: $(Get-Date -Format o)"
Add-Content -Path $report -Value "Project root: $(Split-Path -Parent $PSScriptRoot)"
Add-Content -Path $report -Value "PowerShell: $($PSVersionTable.PSVersion)"
# Intentionally do not read .env or collect credentials/secrets.

Run-Capture 'Docker CLI version' 'docker' @('--version')
Run-Capture 'Docker Compose version' 'docker' @('compose','version')
Run-Capture 'Docker daemon/server information' 'docker' @('version')
Run-Capture 'Container list' 'docker' @('ps','-a')
Run-Capture 'Docker resource usage' 'docker' @('stats','--no-stream')
Run-Capture 'Standalone compose status' 'docker' @('compose','-f','compose/docker-compose.single.yml','ps','-a')
Run-Capture 'Standalone recent logs' 'docker' @('compose','-f','compose/docker-compose.single.yml','logs','--tail','100','mongo')
Run-Capture 'Sharded compose status' 'docker' @('compose','-f','compose/docker-compose.shard.yml','ps','-a')
Run-Capture 'Sharded recent logs' 'docker' @('compose','-f','compose/docker-compose.shard.yml','logs','--tail','100','cfg1','shard1','shard2','shard3','mongos')

Write-Section 'Project outputs (names/sizes only; source Yelp data is not collected)'
$projectRoot = Split-Path -Parent $PSScriptRoot
foreach ($sub in @('results','figures')) {
    $dir = Join-Path $projectRoot $sub
    if (Test-Path $dir) {
        Add-Content -Path $report -Value "--- $sub ---"
        Get-ChildItem $dir -File | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize | Out-String | Add-Content -Path $report
    }
}

Write-Section 'E3 script tail (helps detect accidental truncation)'
$e3 = Join-Path $PSScriptRoot 'e3_index_scaling.py'
if (Test-Path $e3) { Get-Content $e3 -Tail 20 | Out-String | Add-Content -Path $report }
Write-Section 'E5c script tail'
$e5c = Join-Path $PSScriptRoot 'e5c_single_vs_sharded.py'
if (Test-Path $e5c) { Get-Content $e5c -Tail 20 | Out-String | Add-Content -Path $report }

$zipPath = Join-Path $diagRoot "diagnostics_$stamp.zip"
try {
    Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zipPath -Force
    Write-Host "Diagnostics captured: $zipPath"
} catch {
    Write-Host "Diagnostics report: $report"
    Write-Host "Could not create ZIP: $($_.Exception.Message)"
}
Write-Host 'Review the bundle before sharing it. It intentionally excludes .env and raw data, but logs may contain local paths or host/container names.'
