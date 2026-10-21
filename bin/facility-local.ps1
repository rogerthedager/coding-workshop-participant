param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('init', 'migrate', 'seed', 'serve', 'test', 'status', 'stop')]
    [string]$Command,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$serviceRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend\facility-service'
$pythonPath = if ($env:FACILITY_PYTHON) { $env:FACILITY_PYTHON } else { Join-Path $serviceRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create backend/facility-service/.venv first, or set FACILITY_PYTHON to your Python interpreter.'
}
& $pythonPath (Join-Path $serviceRoot 'scripts\local.py') $Command @Arguments
exit $LASTEXITCODE
