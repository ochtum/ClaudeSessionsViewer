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

if (-not (Test-Path $projectPath)) {
    throw "Project file not found: $projectPath"
}

if ($CleanOutput -and (Test-Path $outputDir)) {
    Get-ChildItem -Path $outputDir -Force | Remove-Item -Recurse -Force
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

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
    $outputDir
)

Write-Host "Publishing ClaudeSessionsViewer..." -ForegroundColor Cyan
Write-Host "  Configuration : $Configuration"
Write-Host "  Runtime       : $Runtime"
Write-Host "  Self-contained: $($SelfContained.IsPresent)"
Write-Host "  Output        : $outputDir"

& dotnet @publishArgs

if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Publish completed." -ForegroundColor Green
Write-Host "Copy the entire 'app' folder for distribution."
