# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

VENV       := venv
PYTHON     := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
DEPS_STAMP := $(VENV)/.deps-installed

.PHONY: venv test lint clean

$(PYTHON):
	python3 -m venv $(VENV)

$(DEPS_STAMP): $(PYTHON) requirements.txt requirements-dev.txt
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt
	touch $(DEPS_STAMP)

venv: $(DEPS_STAMP)      ## create venv and install changed deps

test: venv               ## run the test suite
	$(PYTHON) -m pytest -v --cov=cf_ddns --cov-branch --cov-report=term-missing

lint: venv               ## static checks
	$(VENV)/bin/ruff check .

clean:                   ## remove venv and caches
	rm -rf $(VENV) __pycache__ .pytest_cache .ruff_cache .coverage
