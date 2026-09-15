SHELL := /bin/bash
.DEFAULT_GOAL := help
BACKEND_PY ?= $(CURDIR)/backend/.venv/bin/python

.PHONY: help install install-frontend install-backend init-local dev demo backend test test-backend test-frontend build install-agent install-agent-extras check-agent
help:
	@echo "Compass: install | init-local | install-agent | install-agent-extras | check-agent | dev | demo | backend | test | build"
install: install-frontend install-backend
install-frontend:
	cd frontend && npm ci
install-backend:
	test -x "$(BACKEND_PY)" || python3 -m venv backend/.venv
	"$(BACKEND_PY)" -m pip install -r backend/requirements-dev.txt
init-local:
	python3 scripts/init_local.py
dev:
	cd frontend && npm run dev
demo:
	cd frontend && VITE_MOCK_MODE=1 npm run dev
backend:
	cd backend && "$(BACKEND_PY)" -m capelle_platform.main
test: test-backend test-frontend
test-backend:
	cd backend && "$(BACKEND_PY)" -m pytest
test-frontend:
	cd frontend && npm test
build:
	cd frontend && npm run build

install-agent:
	python3 scripts/setup_agent.py
install-agent-extras:
	python3 scripts/setup_agent.py --extras
check-agent:
	python3 scripts/check_agent.py
