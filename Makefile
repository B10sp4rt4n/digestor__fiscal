.PHONY: install run migrate seed dev

install:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

run:
	uvicorn app.main:app --reload --port 8000

migrate:
	alembic upgrade head

seed:
	python seeds/seed_demo.py

dev: install migrate seed run
