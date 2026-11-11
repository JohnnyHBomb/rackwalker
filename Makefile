# World of Confusion: RackWalker: top-level tasks. `make help` lists them.
# .venv, web/node_modules and the Playwright browsers live inside the checkout (all gitignored).
SHELL := /bin/bash
.DEFAULT_GOAL := help
# Target Python is 3.15 (3.14 still works; requires-python >=3.12): a python3.15 on PATH if there is one, else
# python3. Override with PYTHON=/path/to/python3.15.
PYTHON ?= $(firstword $(shell command -v python3.15 2>/dev/null) python3)
VENV ?= $(CURDIR)/.venv
PY := $(VENV)/bin/python
VENV_OK := $(VENV)/.installed
NPM ?= npm
PROXY_PORT ?= 8710
NB_PROJECT ?= rackwalker
NB_PORT ?= 8100
NB_HOST ?= localhost
export NB_PROJECT NB_PORT NB_HOST

# Playwright browsers stay inside the checkout (gitignored), not in ~/.cache.
PLAYWRIGHT_BROWSERS_PATH ?= $(CURDIR)/.ms-playwright
export PLAYWRIGHT_BROWSERS_PATH

.PHONY: help bootstrap test packs-check test-proxy test-live test-live-empty test-web test-e2e e2e e2e-setup e2e-live e2e-themes theme-shots gen dev dev-fixtures build \
        netbox-up netbox-down netbox-destroy netbox-seed netbox-reset netbox-status netbox-check-fixtures netbox-env netbox-tunnel

help:
	@echo "bootstrap        create .venv (Python 3.15 venv + pinned proxy/requirements.lock) and web/node_modules (npm ci)"
	@echo "test             proxy pytest + web typecheck + vitest (fixture/schema validation on both sides)"
	@echo "packs-check      validate every pack under packs/ (schema, sources, the device index and its checksum); part of make test"
	@echo "test-live        proxy contract tests against the dev NetBox (NB_PROJECT/NB_PORT; RACKWALKER_RECORD=1 re-records)"
	@echo "test-live-empty  the onboarding chain on a dev NetBox that was started and never seeded (netbox-destroy netbox-up), timed"
	@echo "test-e2e (e2e)   web golden screenshots per zoom level + interaction + perf (headless chromium; installs it on first use)"
	@echo "e2e-themes       interaction + edit e2e once per theme (?theme=<id>), all five themes"
	@echo "e2e-live         while make dev runs: chromium walks world->site->room->rack->device on the live proxy, screenshots to docs/screenshots/"
	@echo "theme-shots      THEME=<id>|all: every zoom level + picker + slot sheet (all slots/variants, 4x) to docs/screenshots/themes/<id>/"
	@echo "gen              regenerate pydantic models, TS types and fixtures from contracts/ + devnetbox/seed_model.py"
	@echo "dev              proxy on :$(PROXY_PORT) against NETBOX_URL/NETBOX_TOKEN from .env, vite on :5173"
	@echo "dev-fixtures     same, but the proxy serves fixtures/ (no NetBox needed)"
	@echo "netbox-up|down|destroy|seed|reset|status|check-fixtures|env|tunnel   dev NetBox in Docker (NB_PROJECT=$(NB_PROJECT) NB_PORT=$(NB_PORT), URL host $(NB_HOST))"

# The stamp re-runs the install when the lock or pyproject changes (an old .venv otherwise misses new test deps).
$(VENV_OK): proxy/requirements.lock proxy/pyproject.toml
	[ -x $(PY) ] || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r proxy/requirements.lock
	$(VENV)/bin/pip install -q --no-deps -e proxy
	touch $@

web/node_modules/.package-lock.json: web/package-lock.json
	cd web && $(NPM) ci --no-audit --no-fund

bootstrap: $(VENV_OK) web/node_modules/.package-lock.json

test: test-proxy test-web

# Every pack under packs/ against its schema and the rules a schema cannot say (docs/packs.md). No network.
packs-check: $(VENV_OK)
	$(PY) -m rackwalker_proxy.packs

test-proxy: $(VENV_OK) packs-check
	$(PY) -m pytest proxy -p no:cacheprovider

# Contract tests against the live dev NetBox $(NB_PROJECT) (fresh seed: make netbox-reset). RACKWALKER_RECORD=1 also
# rewrites proxy/tests/recorded/ (the replay data of the offline unit tests). The token stays in this shell.
# The proxy under test runs on the proxy's own write token (minimum permissions); the admin token only inspects the
# changelog and undoes the test writes.
test-live: $(VENV_OK)
	@eval "$$(PY=$(PY) devnetbox/nb.sh env rw | sed 's/^/export /')"; \
	export NETBOX_ADMIN_TOKEN="$$(PY=$(PY) devnetbox/nb.sh env admin | sed -n 's/^NETBOX_TOKEN=//p')"; RACKWALKER_LIVE_NETBOX=1 $(PY) -m pytest proxy/tests/test_live_netbox.py proxy/tests/test_live_quests.py proxy/tests/test_live_settlement.py proxy/tests/test_live_setup.py -p no:cacheprovider

# The onboarding chain on a NetBox with nothing in it: a stack that was started and never seeded
# (make netbox-destroy netbox-up). It leaves a site, a rack, two devices, a carrier and a circuit behind and prints how
# long it took; destroy or reset the stack afterwards.
test-live-empty: $(VENV_OK)
	@eval "$$(PY=$(PY) devnetbox/nb.sh env rw | sed 's/^/export /')"; \
	export NETBOX_ADMIN_TOKEN="$$(PY=$(PY) devnetbox/nb.sh env admin | sed -n 's/^NETBOX_TOKEN=//p')"; RACKWALKER_LIVE_NETBOX=1 RACKWALKER_LIVE_EMPTY=1 $(PY) -m pytest proxy/tests/test_live_setup.py -k empty_live -p no:cacheprovider -s

test-web: web/node_modules/.package-lock.json
	cd web && npx tsc --noEmit -p . && npx vitest run

test-e2e: web/node_modules/.package-lock.json $(VENV_OK)
	cd web && npx playwright install chromium && npx playwright test
	cd web && npx playwright test -c playwright.setup.config.ts

# The setup companion alone: an empty made-up NetBox (proxy/tests/empty_netbox.py), a write-enabled proxy, the browser.
e2e-setup: web/node_modules/.package-lock.json $(VENV_OK)
	cd web && npx playwright install chromium && npx playwright test -c playwright.setup.config.ts

e2e: test-e2e

# The interaction and edit e2e under every theme (?theme=<id> on every open; golden screenshots stay on the default).
e2e-themes: web/node_modules/.package-lock.json
	cd web && npx playwright install chromium && for t in debug industrial-robotic farm business pagodas-nature; do \
	  echo "== theme $$t"; RACKWALKER_E2E_THEME=$$t npx playwright test e2e/interaction.spec.ts e2e/edit.spec.ts || exit 1; done

# Needs `make dev` running (vite :5173 -> proxy -> dev NetBox). Not part of make test: it needs the live stack.
e2e-live: web/node_modules/.package-lock.json
	mkdir -p docs/screenshots
	cd web && npx playwright install chromium && npx playwright test -c playwright.live.config.ts

# Art review for one theme (or THEME=all): fixtures, ?theme=<id> on every level, plus sheet.html?theme=<id>. Writes PNGs only;
# the theme conformance test (vitest, part of make test) is the gate.
theme-shots: web/node_modules/.package-lock.json
	@case " debug industrial-robotic farm business pagodas-nature all " in *" $(THEME) "*) ;; \
	  *) echo "usage: make theme-shots THEME=<debug|industrial-robotic|farm|business|pagodas-nature|all>"; exit 2;; esac
	cd web && npx playwright install chromium && RACKWALKER_THEME=$(THEME) npx playwright test -c playwright.themes.config.ts

gen: bootstrap
	proxy/gen-models.sh
	cd web && node scripts/gen-types.mjs
	$(PY) fixtures/make_fixtures.py

build: bootstrap
	cd web && $(NPM) run build

# make dev: proxy (uvicorn) + vite via dev.sh. Reads NETBOX_URL / NETBOX_TOKEN / WRITE_ENABLED from .env.
dev: bootstrap
	PROXY_PORT=$(PROXY_PORT) VENV=$(VENV) ./dev.sh netbox

dev-fixtures: bootstrap
	PROXY_PORT=$(PROXY_PORT) VENV=$(VENV) ./dev.sh fixtures

# ---- dev NetBox (devnetbox/nb.sh): the local Docker daemon, or a remote one if DOCKER_HOST is exported ----
netbox-up: ; devnetbox/nb.sh up
netbox-down: ; devnetbox/nb.sh down
netbox-destroy: ; devnetbox/nb.sh destroy
netbox-seed: $(VENV_OK) ; PY=$(PY) devnetbox/nb.sh seed
netbox-reset: $(VENV_OK) ; PY=$(PY) devnetbox/nb.sh reset
netbox-status: $(VENV_OK) ; PY=$(PY) devnetbox/nb.sh status
# fixture ids == live ids (true on a stack seeded once from empty: netbox-reset)
netbox-check-fixtures: $(VENV_OK) ; PY=$(PY) devnetbox/nb.sh check
# writes .env (mode 600, gitignored) for this stack; keeps an existing .env unless FORCE=1. The proxy never gets the
# admin token: by default a read-only token of the `rackwalker-proxy` user (write_enabled=false) and WRITE_ENABLED=0;
# WRITE=1 writes that user's write token (minimum add/change permissions) and WRITE_ENABLED=1.
# One shell for the whole recipe; nb.sh env is read first and .env is written only when it succeeded.
netbox-env: $(VENV_OK)
	@if [ -f .env ] && [ "$(FORCE)" != 1 ]; then echo ".env exists (FORCE=1 to overwrite)"; exit 0; fi; \
	out=$$(PY=$(PY) devnetbox/nb.sh env $(if $(filter 1,$(WRITE)),rw,ro)) || { echo "nb.sh env failed: .env left as it was"; exit 1; }; \
	umask 077; { echo "# dev NetBox $(NB_PROJECT) on $(NB_HOST):$(NB_PORT); synthetic data only; proxy user rackwalker-proxy"; \
	  printf '%s\n' "$$out"; echo "WRITE_ENABLED=$(if $(filter 1,$(WRITE)),1,0)"; } > .env; \
	echo "wrote .env for $(NB_PROJECT) ($(if $(filter 1,$(WRITE)),write,read-only) proxy token; token not shown)"
# Only needed if $(NB_HOST):$(NB_PORT) is firewalled from this machine: then use NB_URL=http://localhost:$(NB_PORT).
netbox-tunnel: ; devnetbox/nb.sh tunnel
