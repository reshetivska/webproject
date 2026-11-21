$env:COMPOSE_PARALLEL_LIMIT = 1
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d --build
Write-Host "Сайт: https://localhost:8443"
