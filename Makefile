.RECIPEPREFIX := >
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.worker.yml -f docker-compose.monitoring.yml

.PHONY: construir levantar bajar logs estado pruebas humo reprocesar validar

construir:
> docker build -f docker/Dockerfile.python312 -t rag-api:local .

levantar:
> $(COMPOSE) up -d --build

bajar:
> $(COMPOSE) down

logs:
> $(COMPOSE) logs -f --tail=100 api worker

estado:
> $(COMPOSE) ps

pruebas:
> cd services/api-fastapi && python -m pytest tests -q

humo:
> ./scripts/smoke_test.sh

reprocesar:
> $(COMPOSE) exec worker python -m reprocesar --simular

validar:
> cp -n .env.example .env || true
> $(COMPOSE) config -q
