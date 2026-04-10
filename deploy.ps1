# Deploy Model Forecast frontend to GitHub Pages
# Usage: .\deploy.ps1

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$FRONTEND = Join-Path $ROOT "frontend"
$DIST = Join-Path $FRONTEND "dist"

Write-Host "Building frontend..." -ForegroundColor Cyan
Set-Location $FRONTEND
$env:VITE_API_URL = "https://model-forecast-693545589581.us-central1.run.app"
npm run build
Remove-Item env:\VITE_API_URL

# Add .nojekyll so GitHub Pages serves _ prefixed files
New-Item -Path (Join-Path $DIST ".nojekyll") -ItemType File -Force | Out-Null

# Copy index.html to 404.html for SPA routing
Copy-Item (Join-Path $DIST "index.html") (Join-Path $DIST "404.html")

Write-Host "Deploying to gh-pages branch..." -ForegroundColor Cyan
Set-Location $DIST
Remove-Item -Recurse -Force ".git" -ErrorAction SilentlyContinue
git init
git checkout -b gh-pages
git add -A
git commit -m "deploy: GitHub Pages"
git remote add origin https://github.com/ShianMike/ModelForecast.git
git push origin gh-pages --force
Remove-Item -Recurse -Force ".git"

Set-Location $ROOT
Write-Host "`nDeployed! https://shianmike.github.io/ModelForecast/" -ForegroundColor Green
