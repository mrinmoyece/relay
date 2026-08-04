.PHONY: install dev test lint evals demo run up down

install:
	pip install -e ".[dev]"

test:
	pytest -v

lint:
	ruff check src tests evals

evals:
	python -m evals.run_evals

demo:
	python scripts/demo.py

run:
	uvicorn relay.api.app:app --reload

up:
	docker compose up --build

down:
	docker compose down -v
