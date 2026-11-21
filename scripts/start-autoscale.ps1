$env:COMPOSE_PARALLEL_LIMIT = 1
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$env:COMPOSE_FILE = "docker-compose.yml;docker-compose.autoscale.yml"
$env:COMPOSE_PROFILES = "autoscale"
docker compose up -d --build
Write-Host "Лог автомасштабувача: docker compose logs -f autoscaler"
