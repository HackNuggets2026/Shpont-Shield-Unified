# Shpont Shield: make test | make fresh | make demo | make web | make redteam
PY ?= .venv/bin/python
PORT ?= 8787
SEED ?= 42

.PHONY: test fresh seed demo web web-dev redteam redteam-ci redteam-test

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
