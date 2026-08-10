SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

IMAGE ?= docker.io/library/node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436
PODMAN ?= podman
ARGS ?= --help
CONFIG_PATH ?=
BUILD_COMMAND ?= npm run build
ALLOWED_ARGS := --help -h --nominations --self-stake-stats --validator-info

export CONFIG_PATH

ifneq ($(filter run,$(MAKECMDGOALS)),)
ifneq ($(words $(strip $(ARGS))),1)
$(error ARGS must contain exactly one CLI flag)
endif
ifeq ($(filter $(strip $(ARGS)),$(ALLOWED_ARGS)),)
$(error ARGS must be one of: $(ALLOWED_ARGS))
endif
endif

SOURCE_EXCLUDES := \
	--exclude=./.git \
	--exclude=./.papi \
	--exclude=./dist \
	--exclude=./node_modules \
	--exclude=./nominations.json \
	--exclude=./package-lock.json.next

CONTAINER_FLAGS := \
	--rm \
	--quiet \
	--interactive \
	--pull=missing \
	--read-only \
	--read-only-tmpfs=false \
	--userns=keep-id \
	--cap-drop=ALL \
	--security-opt=no-new-privileges \
	--network=private \
	--no-hosts \
	--ipc=private \
	--pid=private \
	--uts=private \
	--cgroupns=private \
	--pids-limit=512 \
	--memory=2g \
	--memory-swap=2g \
	--cpus=2 \
	--umask=077 \
	--log-driver=none \
	--tmpfs=/work:rw,exec,nosuid,nodev,size=1g,mode=1777 \
	--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=512m,mode=1777

CONTAINER_ENV := \
	--env HOME=/tmp/home \
	--env npm_config_cache=/tmp/npm-cache \
	--env npm_config_ignore_scripts=true \
	--env npm_config_audit=false \
	--env npm_config_fund=false

.PHONY: help check-podman nominations self-stake-stats validator-info run typecheck test test-live package-lock

help:
	@printf '%s\n' \
	  'make nominations       - nomination reports for configured validators' \
	  'make self-stake-stats  - network-wide validator self-stake statistics' \
	  'make validator-info    - validator stake, rewards, and reward projections' \
	  'make run ARGS="..."    - run an explicit CLI command' \
	  'make typecheck         - run the TypeScript compiler without emitting files' \
	  'make test              - run unit and RPC integration tests' \
	  'make test-live         - run full live RPC tests for every CLI mode' \
	  'make package-lock      - regenerate package-lock.json in a container'

check-podman:
	@rootless="$$($(PODMAN) info --format '{{.Host.Security.Rootless}}')"; \
	  if [[ "$$rootless" != "true" ]]; then \
	    printf '%s\n' 'Error: rootless Podman is required.' >&2; \
	    exit 1; \
	  fi

define run_in_container
	@tar --create --file=- --directory="$(CURDIR)" $(SOURCE_EXCLUDES) . | \
	$(PODMAN) run $(CONTAINER_FLAGS) \
	  --env RPC_URL --env CONFIG_PATH \
	  $(CONTAINER_ENV) \
	  $(IMAGE) sh -ceu ' \
	    test "$$(id -u)" -ne 0; \
	    test ! -e /src; \
	    grep -Eq "^CapEff:[[:space:]]+0+$$" /proc/self/status; \
	    grep -Eq "^NoNewPrivs:[[:space:]]+1$$" /proc/self/status; \
	    mkdir -p /work/app "$$HOME"; \
	    chmod 700 /work/app "$$HOME"; \
	    tar --extract --file=- --directory=/work/app; \
	    cd /work/app; \
	    npm ci --ignore-scripts --no-audit --no-fund >&2; \
	    if [ -d node_modules/tsc-prog ]; then \
	      mv node_modules/tsc-prog node_modules/@polkadot-api/cli/node_modules/; \
	    fi; \
	    rpc_url="$$(node -e '\'' \
	      const fs = require("node:fs"); \
	      const path = process.env.CONFIG_PATH || "config.json"; \
	      const config = JSON.parse(fs.readFileSync(path, "utf8")); \
	      process.stdout.write(process.env.RPC_URL || config.rpcUrl || \
	        "wss://rpc-assethub.novasama-tech.org"); \
	    '\'')"; \
	    ./node_modules/.bin/papi add ah -w "$$rpc_url" >&2; \
	    $(BUILD_COMMAND) >&2; \
	    exec "$$@" \
	  ' sh $(1)
endef

nominations: check-podman
	$(call run_in_container,node dist/index.js --nominations)

self-stake-stats: check-podman
	$(call run_in_container,node dist/index.js --self-stake-stats)

validator-info: check-podman
	$(call run_in_container,node dist/index.js --validator-info)

run: check-podman
	$(call run_in_container,node dist/index.js $(ARGS))

typecheck: CONFIG_PATH := test/fixtures/rpc-config.json
typecheck: BUILD_COMMAND := :
typecheck: check-podman
	$(call run_in_container,npm run typecheck)

test: CONFIG_PATH := test/fixtures/rpc-config.json
test: check-podman
	$(call run_in_container,npm test)

test-live: CONFIG_PATH := test/fixtures/rpc-config.json
test-live: check-podman
	$(call run_in_container,npm run test:live)

package-lock: check-podman
	@tar --create --file=- --directory="$(CURDIR)" $(SOURCE_EXCLUDES) . | \
	$(PODMAN) run $(CONTAINER_FLAGS) \
	  $(CONTAINER_ENV) \
	  $(IMAGE) sh -ceu ' \
	    test "$$(id -u)" -ne 0; \
	    test ! -e /src; \
	    grep -Eq "^CapEff:[[:space:]]+0+$$" /proc/self/status; \
	    grep -Eq "^NoNewPrivs:[[:space:]]+1$$" /proc/self/status; \
	    mkdir -p /work/app "$$HOME"; \
	    chmod 700 /work/app "$$HOME"; \
	    tar --extract --file=- --directory=/work/app; \
	    cd /work/app; \
	    npm install --package-lock-only --ignore-scripts --no-audit --no-fund >&2; \
	    cat package-lock.json \
	  ' > package-lock.json.next
	@mv package-lock.json.next package-lock.json
