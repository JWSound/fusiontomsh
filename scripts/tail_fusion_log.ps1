param(
    [int]$Tail = 500,
    [switch]$Follow
)

$fusionDataRoot = Join-Path $env:LOCALAPPDATA 'Autodesk\Autodesk Fusion 360'
$candidateLogs = Get-ChildItem -LiteralPath $fusionDataRoot -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        $logsDirectory = Join-Path $_.FullName 'logs'
        if (Test-Path -LiteralPath $logsDirectory) {
            Get-ChildItem -LiteralPath $logsDirectory -Filter 'AppLogFile*.log' -File -ErrorAction SilentlyContinue
        }
    }

$latestLog = $candidateLogs | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latestLog) {
    Write-Error "No Fusion application log was found under $fusionDataRoot"
    exit 1
}

Write-Output "Fusion log: $($latestLog.FullName)"
if ($Follow) {
    Get-Content -LiteralPath $latestLog.FullName -Tail $Tail -Wait |
        Select-String -Pattern 'MSHExport FEM:' -CaseSensitive:$false
} else {
    Get-Content -LiteralPath $latestLog.FullName -Tail $Tail |
        Select-String -Pattern 'MSHExport FEM:' -CaseSensitive:$false
}
