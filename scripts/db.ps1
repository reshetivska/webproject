docker compose exec postgres psql -U app -d webproject -c "\dt"
docker compose exec postgres psql -U app -d webproject -c "SELECT id, filter_name, radius, status, duration_sec, workers_used FROM tasks ORDER BY created_at DESC LIMIT 5;"
docker compose exec postgres psql -U app -d webproject -c "SELECT id, username, created_at FROM users;"
