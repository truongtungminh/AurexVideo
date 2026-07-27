$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/tinbeta/escbase_m3"
$runnerRoot = "C:\FastSceneRunner"
$runnerLabel = "fastscene-windows"

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
  Write-Host "FastScene needs Administrator access to install the Windows build tools." -ForegroundColor Yellow
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
  Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments
  exit 0
}

Write-Host "`n=== FastScene Windows self-hosted runner ===`n" -ForegroundColor Cyan

if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
  throw "winget was not found. Update App Installer from Microsoft Store, then run START-HERE.cmd again."
}

if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Git..." -ForegroundColor Cyan
  winget install --id Git.Git --exact --silent --accept-package-agreements --accept-source-agreements
  $env:Path = "C:\Program Files\Git\cmd;$env:Path"
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$hasBuildTools = $false
if (Test-Path $vswhere) {
  $installation = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  $hasBuildTools = -not [string]::IsNullOrWhiteSpace($installation)
}
if (-not $hasBuildTools) {
  Write-Host "Installing Visual Studio 2022 Build Tools and Windows SDK. This can take 10-30 minutes..." -ForegroundColor Cyan
  winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --silent --accept-package-agreements --accept-source-agreements --override "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
}

New-Item -ItemType Directory -Path $runnerRoot -Force | Out-Null
Set-Location $runnerRoot

if (-not (Test-Path (Join-Path $runnerRoot "run.cmd"))) {
  Write-Host "Downloading the latest GitHub Actions Runner..." -ForegroundColor Cyan
  $release = Invoke-RestMethod -Headers @{ "User-Agent" = "FastScene-Runner-Setup" } -Uri "https://api.github.com/repos/actions/runner/releases/latest"
  $asset = $release.assets | Where-Object { $_.name -match '^actions-runner-win-x64-.*\.zip$' } | Select-Object -First 1
  if (-not $asset) { throw "The GitHub Actions Runner package for Windows x64 was not found." }
  $archive = Join-Path $env:TEMP $asset.name
  Invoke-WebRequest -Headers @{ "User-Agent" = "FastScene-Runner-Setup" } -Uri $asset.browser_download_url -OutFile $archive
  Expand-Archive -Path $archive -DestinationPath $runnerRoot -Force
}

if (-not (Test-Path (Join-Path $runnerRoot ".runner"))) {
  Write-Host "`nOpen GitHub repo > Settings > Actions > Runners > New self-hosted runner." -ForegroundColor Yellow
  Write-Host "Select Windows and x64, then copy the TOKEN from the config.cmd command." -ForegroundColor Yellow
  $token = Read-Host "Paste the one-time registration token"
  if ([string]::IsNullOrWhiteSpace($token)) { throw "The token cannot be empty." }
  $runnerName = "FastScene-Windows-$env:COMPUTERNAME"
  & .\config.cmd --unattended --url $repoUrl --token $token --name $runnerName --labels $runnerLabel --work _work --replace
  if ($LASTEXITCODE -ne 0) { throw "GitHub runner registration failed." }
}

Write-Host "`nRunner is ready. Keep this window open while the build is running." -ForegroundColor Green
Write-Host "When you see 'Listening for Jobs', tell Codex: runner online.`n" -ForegroundColor Green
& .\run.cmd
