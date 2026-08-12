PYTHON ?= python3

.PHONY: install dev test lint evals demo run up down

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	pytest -v

lint:
	ruff check src tests evals

evals:
	$(PYTHON) -m evals.run_evals

demo:
	$(PYTHON) scripts/demo.py

run:
	$(PYTHON) -m uvicorn relay.api.app:app --reload

up:
	docker compose up --build

down:
	docker compose down -v
