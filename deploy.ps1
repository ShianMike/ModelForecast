# Build Model Forecast frontend and copy to static/ for Cloud Run deployment
# The app is served entirely from Cloud Run at https://modelforecastpy.app
# Usage: .\deploy.ps1

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$FRONTEND = Join-Path $ROOT "frontend"

Write-Host "Building frontend for https://modelforecastpy.app ..." -ForegroundColor Cyan
Set-Location $FRONTEND
$env:VITE_API_URL = "https://modelforecastpy.app"
npm run build
Remove-Item env:\VITE_API_URL

Set-Location $ROOT
Write-Host "Copying dist/ to static/..." -ForegroundColor Cyan
Copy-Item "frontend\dist\*" "static\" -Recurse -Force

Write-Host "`nFrontend built and copied to static/." -ForegroundColor Green
Write-Host "Now commit and push, then deploy to Cloud Run." -ForegroundColor Yellow
