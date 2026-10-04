# Shpont Shield: make setup | make selftest | make demo | make redteam | make test | make fresh | make web
PY ?= .venv/bin/python
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3.11 || echo python3)
PORT ?= 8787
SEED ?= 42

.PHONY: setup test selftest fresh seed demo web web-dev redteam redteam-ci redteam-test

setup:            ## venv with the dev and classifier extras, the CPU injection model (~739 MB) and the console build
	@if command -v uv >/dev/null; then uv venv -q --allow-existing --python 3.12 .venv \
		&& uv pip install -q --python $(PY) -e '.[dev,classifier]' -e redteam; \
	else $(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11) and "needs Python 3.11+ (or uv): set PYTHON=python3.12")' \
		&& $(PYTHON) -m venv .venv && $(PY) -m pip install -q -U pip && $(PY) -m pip install -q -e '.[dev,classifier]' -e redteam; fi
	$(PY) -m controllayer.semantic_model download
	cd web && npm install && npm run build

test:
	$(PY) -m pytest -q && $(PY) -m ruff check . && $(PY) -m ruff format --check .

fresh:            ## an empty gateway: no history, no admin overlay
	rm -rf data/fresh
	$(PY) -m controllayer --data-dir data/fresh --port $(PORT)

seed:             ## 30 days of demo history in data/demo (replaces it)
	$(PY) -m seed --data-dir data/demo --days 30 --seed $(SEED)

demo: seed        ## seeded history, then the gateway on it
	$(PY) -m controllayer --data-dir data/demo --port $(PORT)

web:              ## build the SPA into web/dist (served by the gateway at /)
	cd web && npm install && npm run build

web-dev:          ## the SPA with hot reload on :5173, proxying the API to the gateway
	cd web && npm run dev

REDTEAM_PORT ?= 8799

redteam:          ## the Redteam sidecar: attacks the gateway on $(PORT) and serves Console > Attacks
	cd redteam && SHIELD_URL=http://127.0.0.1:$(PORT) REDTEAM_PORT=$(REDTEAM_PORT) $(abspath $(PY)) -m shield_redteam serve

redteam-ci:       ## CI gate: fail below posture 90 or on a broken signature in the feed
	cd redteam && SHIELD_URL=http://127.0.0.1:$(PORT) $(abspath $(PY)) -m shield_redteam run --min-posture 90 \
		&& $(abspath $(PY)) -m shield_redteam feed --check

redteam-test:
	cd redteam && $(abspath $(PY)) -m pytest -q

selftest:         ## the test suite with JUnit XML and the per-control JSON report shown on the console's Tests page
	mkdir -p reports
	$(PY) -m pytest -q --junitxml=reports/junit.xml --acl-report=reports/acl-report.json
