.PHONY: install run migrate seed test dev

install:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

run:
	hypercorn app.main:app --reload --bind 0.0.0.0:8000

migrate:
	alembic upgrade head

seed:
	python seeds/seed_demo.py

test:
	pytest -q

dev: install migrate seed run
