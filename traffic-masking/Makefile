# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

VENV       := venv
PYTHON     := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
RUFF       := $(VENV)/bin/ruff
DEPS_STAMP := $(VENV)/.deps-installed

COV := --cov=control_protocol --cov=masking_lib \
       --cov=traffic_masking_server --cov=traffic_masking_client \
       --cov=enhanced --cov-branch --cov-report=term-missing

.PHONY: venv test test-fast test-live lint run-server run-client clean

$(PYTHON):
	python3 -m venv $(VENV)

$(DEPS_STAMP): $(PYTHON) requirements.txt requirements-dev.txt
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt
	touch $(DEPS_STAMP)

venv: $(DEPS_STAMP)        ## create venv and install changed deps

test: venv                ## run the full test suite (unit + live)
	$(PYTHON) -m pytest -v $(COV)

test-fast: venv           ## run only fast unit tests (skip live)
	$(PYTHON) -m pytest -v -m "not live" $(COV)

test-live: venv           ## run only the live end-to-end tests
	$(PYTHON) -m pytest -v -m live

lint: venv                ## static checks
	$(RUFF) check .

run-server: venv          ## demo server (floating 2-8 Mbps, advanced)
	$(PYTHON) traffic_masking_server.py --min-mbps 2 --max-mbps 8 --advanced --profile mixed --insecure-diagnostic

run-client: venv          ## demo client against 127.0.0.1
	$(PYTHON) traffic_masking_client.py --server 127.0.0.1 --advanced --uplink-profile mixed --insecure-diagnostic

clean:                    ## remove venv and generated artifacts
	rm -rf $(VENV) __pycache__ enhanced/__pycache__ \
	       .pytest_cache .ruff_cache .coverage htmlcov *.log
