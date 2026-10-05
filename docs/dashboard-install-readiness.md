# Dashboard readiness after installation

The October 5, 2026 installation reached Tailscale and Authentik successfully,
but `/api/v1/snapshot` returned HTTP 500 with `Cannot operate on a closed database`.
The October 4 shared-store and snapshot changes (`c59b65a` and `5219716`)
exposed MCP readers that explicitly closed a connection borrowed by the snapshot.

## Fix and connection ownership

`McpActivity.permission()` and `_read_only()` now use the managed database
context. Nested callers must use `with db.connect(...)`; they must not directly
close a borrowed connection. Only the outermost context commits or rolls back
and closes it. Regression coverage includes an installed MCP gateway in a real
snapshot request, plus permission and category reads within an active transaction.

## Installer acceptance

The installer requires a running, online Tailscale device with a valid private
DNS name. A successful `tailscale up` command alone does not complete the join
step; the terminal continues waiting for those signals.

Final verification checks the private HTTPS routes with certificate validation.
Only HTTP 200 or an expected redirect to this installation's private Authentik
counts as a responding route. These route checks do not prove that dashboard
data works.

Setup then opens the dashboard and asks the operator to sign in. A fully
validated snapshot records a readiness timestamp only after its shared database
transaction finishes. Setup requires a timestamp newer than the current
verification attempt, so an earlier install cannot satisfy the check. Household
sessions and failed snapshots cannot mark setup ready. The owner may have
walked away during the downloads, so setup waits for that sign-in without a
deadline. After five minutes it reminds them of the address and of the
journal command for dashboard errors, and Ctrl+C stops it safely. A headless
install prints the address for another tailnet device.

The dashboard distinguishes HTTP/data errors from network errors and expired
sessions. HTTP 500 responses no longer show Tailscale connection advice.

## Validation and integration

Regression tests live in `test_store.py`, `test_status_snapshot.py`,
`test_install_updates.py`, `test_terminal_installer.py`, `test_preflight.py`, and
`dashboard/src/state/dashboard.test.tsx`.

This work is committed on `fix/dashboard-install-readiness` in the attached
`dashboard-install-readiness` worktree. It has not been merged or deployed to the
running installation. Other agents should inspect that branch and this note
before changing shared connection ownership or installer completion criteria.
