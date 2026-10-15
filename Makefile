# Mu3Lab :: Makefile
# WHAT:  Shortcuts for the common workflows. Thin wrappers only — real logic
#        lives in install.sh / start.sh / unittest / npm.
# WHY:   One canonical spelling per task so docs and muscle memory agree.
# DEBUG: `make -n <target>` prints the commands without running them.

# CI passes UV=uv (its own pinned uv); locally the toolchain's copy.
UV ?= .tools/bin/uv
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.tools/python
export UV_CACHE_DIR := $(CURDIR)/.tools/cache
RUN := $(UV) run --frozen

.PHONY: check-updates approve pause resume install start dev-setup test lint format typecheck contract verify verify-security sbom check vm-test dry-run clean nuke ui-dev

install:
	./install.sh

start:
	./start.sh

# dev-setup: the pinned toolchain, developer tools and dashboard dependencies.
dev-setup:
	ROOT="$(CURDIR)" bash -c 'source tools/toolchain.sh && mu3lab_ensure_uv'
	$(UV) sync --frozen
	ROOT="$(CURDIR)" bash -c 'source tools/toolchain.sh && mu3lab_ensure_bw'
	$(RUN) python -m tools.dashboard build

test:
	$(RUN) python -m unittest discover -s tests -t . -v
	$(RUN) python -m tools.dashboard check

lint:
	$(RUN) python tools/check_no_app_ids.py
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format:
	$(RUN) ruff check --fix .
	$(RUN) ruff format .
	$(RUN) python -m tools.dashboard format

typecheck:
	$(RUN) python -m tools.api_schema --check
	$(RUN) mypy

# contract: the catalog, Compose files and scripts are deployable; nothing private is tracked.
contract:
	git ls-files -z '*.sh' | xargs -0 -n1 bash -n
	$(RUN) python -c "from ctl.manifest.catalog import load; print(len(load().apps), 'apps valid')"
	$(RUN) python -m tools.validate_compose
	@! git ls-files | grep -E '(^|/)\.env$$' || { echo 'A private .env file is tracked.'; exit 1; }
	@! git ls-files | grep -E '(^|/)(data|backups|runtime)/' || { echo 'Runtime data is tracked.'; exit 1; }

# verify: every check that needs no network. CI runs exactly this and verify-security
# (tests/test_ci_parity.py keeps them from drifting apart).
verify: lint typecheck test contract
	$(RUN) python -m tools.dashboard build

# verify-security: checks that ask the network: dependency advisories (Python, dashboard,
# connectors), committed secrets, and that every pinned image exists for x86-64.
verify-security:
	$(UV) export --frozen --no-dev --no-hashes --format requirements.txt --output-file .tools/requirements.lock.txt
	$(UV) tool run --from pip-audit==2.9.0 pip-audit --disable-pip --no-deps -r .tools/requirements.lock.txt
	$(RUN) python -m tools.audit
	$(RUN) python -m tools.check_images

# sbom: the Python dependency inventory published with CI runs.
sbom:
	$(UV) export --frozen --no-dev --format requirements.txt --output-file .tools/requirements.lock.txt
	$(UV) tool run --from cyclonedx-bom==7.5.0 cyclonedx-py requirements .tools/requirements.lock.txt --output-format JSON --output-file mu3lab-python-sbom.json

# check-updates / approve: the maintainer's release review. See tools/approve_release.py.
check-updates:
	$(RUN) python -m tools.approve_release check

approve:
	@[ -n "$(APP)" ] && [ -n "$(VERSION)" ] || { echo 'usage: make approve APP=mealie VERSION=v3.23.0 [IMAGES="service=registry/name:tag"]'; exit 2; }
	$(RUN) python -m tools.approve_release approve "$(APP)" "$(VERSION)" $(foreach image,$(IMAGES),--image "$(image)")

# check: read-only report of this computer's readiness (changes nothing).
check:
	python3 -m ctl.preflight

# vm-test: boot a throwaway Ubuntu VM with this checkout for installer testing.
vm-test:
	tools/vm/fresh-vm.sh up

dry-run: check

# pause/resume: stop and start only Mu3Lab's containers, for example to copy
# /srv/mu3lab/data while no database is writing. Other containers are untouched.
pause:
	@ids="$$(tools/mu3lab-containers.sh)"; [ -z "$$ids" ] || docker stop $$ids >/dev/null; echo "Mu3Lab paused."

resume:
	@ids="$$(tools/mu3lab-containers.sh --all)"; [ -z "$$ids" ] || docker start $$ids >/dev/null; echo "Mu3Lab resumed."

# clean: stop Mu3Lab's containers. Keeps data, volumes, images and the toolchain.
clean: pause

# nuke: remove this checkout's build output and toolchain. App data is only
# removed by ./uninstall.sh. Type NUKE to confirm.
nuke:
	@echo "This deletes .venv, .tools, dashboard/node_modules and dashboard/dist."
	@echo "Type NUKE to confirm:"; read ans; [ "$$ans" = "NUKE" ]
	rm -rf .venv .tools dashboard/node_modules dashboard/dist

ui-dev:
	$(RUN) python -m tools.dashboard dev

.PHONY: api-schema
api-schema:
	$(RUN) python -m tools.api_schema
	$(RUN) python -m tools.dashboard api-schema
