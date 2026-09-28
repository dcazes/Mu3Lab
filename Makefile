# Mu3Lab :: Makefile
# WHAT:  Shortcuts for the common workflows. Thin wrappers only — real logic
#        lives in install.sh / start.sh / unittest / npm.
# WHY:   One canonical spelling per task so docs and muscle memory agree.
# DEBUG: `make -n <target>` prints the commands without running them.

.PHONY: install start dev-setup test lint format typecheck verify check vm-test dry-run clean nuke

install:
	./install.sh

start:
	./start.sh

dev-setup:
	.venv/bin/pip install -r requirements-dev.txt
	cd dashboard && npm ci

test:
	.venv/bin/python -m unittest discover -s tests -t . -v
	cd dashboard && npm test

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	cd dashboard && npm run lint && npm run format:check

format:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .
	cd dashboard && npm run format

typecheck:
	.venv/bin/mypy
	cd dashboard && npm run typecheck

# verify: everything CI checks, in one command.
verify: lint typecheck test
	cd dashboard && npm run build

# check: read-only report of this computer's readiness (changes nothing).
check:
	python3 -m ctl.preflight

# vm-test: boot a throwaway Ubuntu VM with this checkout for installer testing.
vm-test:
	tools/vm/fresh-vm.sh up

dry-run: check

# clean: stop containers, drop runtime state. Keeps volumes, venv, images.
clean:
	for d in core/*/; do \
		[ -f "$$d/docker-compose.yml" ] && (cd "$$d" && docker compose down || true); \
	done
	rm -rf .state

# nuke: full reset to a fresh checkout. Type NUKE to confirm.
nuke:
	@echo "This deletes containers AND volumes, .venv, node_modules, .state."
	@echo "Type NUKE to confirm:"; read ans; [ "$$ans" = "NUKE" ]
	for d in core/*/; do \
		[ -f "$$d/docker-compose.yml" ] && (cd "$$d" && docker compose down -v || true); \
	done
	rm -rf .state .venv dashboard/node_modules dashboard/dist
