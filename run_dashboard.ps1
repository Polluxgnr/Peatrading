# Launch PEA Sniper Terminal dashboard.
# Streamlit opens the browser itself when headless=false — do NOT also Start-Process
# (that caused a double browser tab).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$py = Join-Path $Root "venv_x64\Scripts\streamlit.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv_x64 missing. Create it first (Python 3.11 x64)." -ForegroundColor Red
    exit 1
}

Write-Host "Starting PEA Sniper Terminal on http://localhost:8501 ..." -ForegroundColor Green
& $py run "05_interfaces/terminal_dashboard.py" --server.headless false --browser.gatherUsageStats false --server.port 8501
