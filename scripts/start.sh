#!/bin/sh
[ -f .env ] || cp .env.example .env
docker compose up -d --build
echo "Сайт: https://localhost:8443"
