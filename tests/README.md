# Mu3Lab :: tests/README.md
# WHAT:  Rules for writing tests in this repo. Read before adding a test file.
# WHY:   Two incident classes came from test code trusting the test box:
#        (1) a fixture PID collided with a live browser process, so a real
#        /proc read leaked into assertions; (2) group-DB assertions passed
#        here and would fail on boxes without a docker group.
# RULES:
#  1. Stub the environment, always. PIDs, users, groups, /proc, $HOME,
#     network, clocks: if the value comes from the box, inject it.
#  2. The single explicitly-live test (preflight run_all shape) asserts
#     SHAPE only (keys present, statuses in enum) — never verdicts.
#  3. Mocks patch at the narrowest seam (module attribute the production
#     code looks up at call time) so production paths stay identical.
#  4. No test spawns the full suite (fork-bomb), binds ports, writes outside
#     tmp dirs, or needs root/sudo/network.
#  5. The runtime root is isolated automatically: tests/__init__.py sets
#     MU3LAB_RUNTIME_ROOT to an empty temp path, so never patch around it by
#     reading /srv/mu3lab. Use tests.support.runtime_paths() for a populated one.
# DEBUG: `python3 -m unittest discover -s tests -t . -v` must pass on a bare
#  checkout with stock system python3, no installs, first try.
