# Mu3Lab :: Makefile
# WHAT:  Shortcuts for the common workflows. Thin wrappers only — real logic
#        lives in install.sh / start.sh / unittest / npm.
# WHY:   One canonical spelling per task so docs and muscle memory agree.
# DEBUG: `make -n <target>` prints the commands without running them.

UV := .tools/bin/uv
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.tools/python
export UV_CACHE_DIR := $(CURDIR)/.tools/cache
RUN := $(UV) run --frozen

.PHONY: check-updates approve pause resume install start dev-setup test lint format typecheck verify check vm-test dry-run clean nuke

install:
	./install.sh

start:
	./start.sh

# dev-setup: the pinned toolchain, developer tools and dashboard dependencies.
dev-setup:
	ROOT="$(CURDIR)" bash -c 'source tools/toolchain.sh && mu3lab_ensure_uv'
	$(UV) sync --frozen
	cd dashboard && npm ci

test:
	$(RUN) python -m unittest discover -s tests -t . -v
	cd dashboard && npm test

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	cd dashboard && npm run lint && npm run format:check

format:
	$(RUN) ruff check --fix .
	$(RUN) ruff format .
	cd dashboard && npm run format

typecheck:
	$(RUN) mypy
	cd dashboard && npm run typecheck

# verify: everything CI runs, in one command.
verify: lint typecheck test
	cd dashboard && npm run build

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
