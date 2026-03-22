[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$Runtime = "win-x64",
    [switch]$SelfContained,
    [switch]$CleanOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectPath = Join-Path $rootDir "src/ClaudeSessionsViewer.csproj"
$outputDir = Join-Path $rootDir "app"
$payloadDir = Join-Path $outputDir "payload"
$appName = [System.IO.Path]::GetFileNameWithoutExtension($projectPath)
$launcherName = if ($Runtime -like "win-*") { "$appName.exe" } else { $appName }
$launcherPath = Join-Path $payloadDir $launcherName
$runCmdPath = Join-Path $outputDir "run.cmd"
$consoleWindowTitle = "ClaudeSessionsViewer"

if (-not (Test-Path $projectPath)) {
    throw "Project file not found: $projectPath"
}

if ($CleanOutput -and (Test-Path $outputDir)) {
    Get-ChildItem -Path $outputDir -Force | Remove-Item -Recurse -Force
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
if (Test-Path $payloadDir) {
    Remove-Item -Path $payloadDir -Recurse -Force
}
if (Test-Path $runCmdPath) {
    Remove-Item -Path $runCmdPath -Force
}
New-Item -ItemType Directory -Path $payloadDir -Force | Out-Null

$publishArgs = @(
    "publish"
    $projectPath
    "-c"
    $Configuration
    "-r"
    $Runtime
    "--self-contained"
    $(if ($SelfContained) { "true" } else { "false" })
    "-o"
    $payloadDir
)

Write-Host "Publishing ClaudeSessionsViewer..." -ForegroundColor Cyan
Write-Host "  Configuration : $Configuration"
Write-Host "  Runtime       : $Runtime"
Write-Host "  Self-contained: $($SelfContained.IsPresent)"
Write-Host "  Output        : $outputDir"
Write-Host "  Layout        : run.cmd + payload/"

& dotnet @publishArgs

if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path $launcherPath)) {
    throw "Published launcher not found: $launcherPath"
}

@(
    '@echo off'
    'setlocal'
    ('title {0}' -f $consoleWindowTitle)
    'pushd "%~dp0" >nul'
    ('.\payload\{0} %*' -f $launcherName)
    'set "EXIT_CODE=%ERRORLEVEL%"'
    'popd >nul'
    'exit /b %EXIT_CODE%'
) | Set-Content -Path $runCmdPath -Encoding ASCII

Write-Host ""
Write-Host "Publish completed." -ForegroundColor Green
Write-Host "Runner   : $runCmdPath"
Write-Host "Payload  : $payloadDir"
Write-Host "Copy the entire 'app' folder for distribution."
