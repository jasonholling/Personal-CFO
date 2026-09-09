# Windows launcher for Personal CFO — the PowerShell equivalent of start.sh.
# Run from PowerShell:  .\start.ps1
# (If PowerShell blocks the script with an "execution policy" error, run
# this once first:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned)
#
# Does the same four things start.sh does on Mac, translated to Windows
# conventions (venv\Scripts instead of venv/bin, `Start-Process` instead
# of `open`, jobs instead of backgrounded `&` + `trap`) — plus upfront
# dependency checks with actionable messages, since a Windows machine
# can't be assumed to already have Python/Node the way a dev Mac might.

$ErrorActionPreference = "Stop"
$RepoDir = $PSScriptRoot

function Write-Ok($msg)   { Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!]  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[x]  $msg" -ForegroundColor Red }

# ── Dependency checks ───────────────────────────────────────────────────────
# Checked upfront, all at once, so a missing dependency gives one clear
# stop with install instructions instead of a confusing failure three
# steps into venv/npm setup.

$missing = @()

# Python: prefer the `py` launcher (installed by python.org's installer
# and what "Add python.exe to PATH" actually wires up on Windows), fall
# back to `python` on PATH directly (covers Microsoft Store installs and
# manual PATH setups). Requires 3.10+ -- same floor as the Mac README --
# and warns above 3.13 for the same reason start.sh pins to an older
# interpreter on Mac: pydantic==2.7.0 has no prebuilt wheel for very new
# Python and fails building from source without a Rust toolchain.
$pyCmd = $null
foreach ($candidate in @("py", "python")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $verOutput = (& $candidate --version 2>&1 | Out-String).Trim()
        if ($verOutput -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) {
                $pyCmd = $candidate
                if ($minor -ge 13) {
                    Write-Warn "Python $major.$minor found via '$candidate' -- pydantic==2.7.0 (pinned in requirements.txt) has no prebuilt wheel for very new Python and may fail to build. If `pip install` fails below, install Python 3.10-3.12 from https://python.org/downloads/ and re-run."
                } else {
                    Write-Ok "Python $major.$minor found via '$candidate'"
                }
                break
            }
        }
    }
}
if (-not $pyCmd) {
    Write-Fail "Python 3.10+ not found (checked 'py' and 'python' on PATH)."
    $missing += "Python 3.10-3.12 -- install from https://python.org/downloads/ and check 'Add python.exe to PATH' during setup"
}

# Node.js / npm -- start.sh only checks for node_modules, not for Node
# itself, but on a fresh Windows machine Node can't be assumed present
# the way it might be on a dev Mac.
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeVer = (node --version) -replace "^v", ""
    $nodeMajor = [int]($nodeVer.Split(".")[0])
    if ($nodeMajor -lt 18) {
        Write-Warn "Node $nodeVer found, but 18+ is required. Install a newer version from https://nodejs.org/"
        $missing += "Node.js 18+ (found $nodeVer) -- https://nodejs.org/"
    } else {
        Write-Ok "Node $nodeVer found"
    }
} else {
    Write-Fail "Node.js not found on PATH."
    $missing += "Node.js 18+ -- install from https://nodejs.org/ (npm comes bundled)"
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Fail "npm not found on PATH (usually bundled with Node.js -- try reinstalling Node)."
    $missing += "npm -- reinstall Node.js from https://nodejs.org/, which bundles npm"
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Fail "Missing dependencies -- install these, then re-run .\start.ps1:"
    foreach ($m in $missing) { Write-Host "  - $m" }
    exit 1
}

# ── Backend ──────────────────────────────────────────────────────────────────
Set-Location "$RepoDir\backend"

if (-not (Test-Path "venv")) {
    Write-Host "Setting up Python environment (first run only)..."
    & $pyCmd -m venv venv
}

Write-Host "Checking backend dependencies..."
& "venv\Scripts\pip.exe" install -q -r requirements.txt

Write-Host "Starting backend..."
$backendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    & "venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
} -ArgumentList "$RepoDir\backend"

# ── Frontend ─────────────────────────────────────────────────────────────────
Set-Location "$RepoDir\frontend"

if (-not (Test-Path "node_modules")) {
    Write-Host "Installing frontend dependencies (first run only)..."
    npm install
}

Write-Host "Starting frontend..."
$frontendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    npx vite
} -ArgumentList "$RepoDir\frontend"

Start-Sleep -Seconds 3
Start-Process "http://localhost:5173"

Write-Host ""
Write-Host "Personal CFO running at http://localhost:5173" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Cyan

# ── Cleanup ──────────────────────────────────────────────────────────────────
# Mirrors start.sh's trap/wait: block here, and however this script exits
# (Ctrl+C or otherwise), stop both background jobs rather than leaving
# orphaned uvicorn/vite processes running.
try {
    while ($true) {
        Start-Sleep -Seconds 1
        if ($backendJob.State -ne "Running" -or $frontendJob.State -ne "Running") {
            Write-Warn "One of the servers stopped unexpectedly -- check the job output below."
            break
        }
    }
} finally {
    Write-Host "Stopping servers..."
    Stop-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Receive-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue | Out-Null
    Remove-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
}
