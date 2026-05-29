# Deploy Lead-Graph OS to Render (requires RENDER_API_KEY in environment)
$ErrorActionPreference = "Stop"

$apiKey = $env:RENDER_API_KEY
if (-not $apiKey) {
    Write-Host @"

RENDER_API_KEY не задан.

Первый деплой (один раз):
  1. Откройте https://dashboard.render.com/blueprint/new
  2. Подключите репозиторий: nevzlad/lead-graph-os (ветка main)
  3. Укажите Blueprint Path: render.yaml
  4. Задайте секреты: HF_API_KEY, TG_BOT_TOKEN, ONBOARDING_BOT_TOKEN
  5. Нажмите Deploy Blueprint

Повторный деплой через API:
  `$env:RENDER_API_KEY = 'rnd_...'
  .\scripts\deploy-render.ps1

"@
    exit 1
}

$headers = @{ Authorization = "Bearer $apiKey" }
$services = Invoke-RestMethod -Uri "https://api.render.com/v1/services?limit=100" -Headers $headers
$targets = @("leadgraph-api", "leadgraph-worker", "leadgraph-bot")

foreach ($item in $services) {
    $svc = if ($item.service) { $item.service } else { $item }
    if ($svc.name -in $targets) {
        $uri = "https://api.render.com/v1/services/$($svc.id)/deploys"
        Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body "{}"
        Write-Host "Deploy triggered: $($svc.name)"
    }
}

Write-Host "Done. Статус: https://dashboard.render.com"
