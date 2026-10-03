# Shpont Shield: make test | make fresh | make demo | make web
PY ?= .venv/bin/python
PORT ?= 8787
SEED ?= 42

.PHONY: test fresh seed demo web web-dev

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
