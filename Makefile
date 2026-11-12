# Relay: one command per milestone. `make help` lists them.
PY     ?= python3
CONFIG ?= config.yaml
DEMO   := config.demo.yaml
RELAY  := $(PY) -m relay --config $(CONFIG)
PORT   ?= 8000

.PHONY: help setup test data models smoke profile verify-sandbox run-all limiter-sim \
        analyze check-page repro demo demo-repro serve clean-demo

help:            ## list targets
	@grep -E '^[a-zA-Z0-9_%-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup:           ## install Relay and test deps (use a virtualenv)
	$(PY) -m pip install -e ".[dev]"

test:            ## unit and end-to-end tests (offline, no API key)
	$(PY) -m pytest -q

data:            ## download HumanEvalFix once, check it in the sandbox, freeze the 40-task sample
	$(RELAY) data

models:          ## list Groq model ids and flag configured ids that are missing
	$(RELAY) models

smoke:           ## M0 exit test: one traced call per backend, repeat is a cache hit
	$(RELAY) smoke

profile:         ## M1: TTFT and tokens/s per backend on 20 fixed prompts
	$(RELAY) profile

verify-sandbox:  ## M2 exit test: known-bad patches fail, known-good pass, network blocked
	$(RELAY) verify-sandbox

run-%:           ## run or resume one policy over the 40 tasks, e.g. make run-P3
	$(RELAY) run --policy $*

run-all:         ## run every policy (resumable; stops cleanly at the daily budget)
	$(RELAY) run --policy all

limiter-sim:     ## replay recorded Groq calls through both rate-limiter reservation rules
	$(PY) -m analysis.limiter_sim --config $(CONFIG)

analyze:         ## rebuild results.json, tables and figures from traces (no API calls)
	$(PY) -m analysis.build --config $(CONFIG)

check-page:      ## M5 exit test: every number on the page exists in results.json (needs playwright)
	$(PY) -m analysis.check_page --config $(CONFIG)

repro:           ## replay every run from cache (zero API calls) and rebuild every table and figure
	$(RELAY) repro

demo:            ## offline demo on the simulator: run all policies, then analyze
	$(PY) -m relay --config $(DEMO) run --policy all
	$(PY) -m analysis.build --config $(DEMO)

demo-repro:      ## make repro, on the committed demo traces and cache
	$(PY) -m relay --config $(DEMO) repro

serve:           ## serve the results page at http://localhost:$(PORT)/replay/
	@echo "results: http://localhost:$(PORT)/replay/   demo: http://localhost:$(PORT)/replay/?results=demo-results.json"
	$(PY) -m http.server $(PORT)

clean-demo:      ## delete demo traces, cache and outputs
	rm -rf demo build/demo-repro build/demo-state replay/demo-results.json
