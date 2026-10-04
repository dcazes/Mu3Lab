Vaultwarden 1.37.3 uses Bitwarden CLI 2026.5.0, pinned with the SHA-256 digests
published on the official GitHub release. Both Linux architectures have
standalone binaries. CLI 2026.9.0 was tested but rejected this server during
its new user-key-ID migration; this pair is explicitly integration-tested.

The CLI owns login, synchronization, folders, items, collection access and
member confirmation. Each job gets a 0700 temporary cache which is deleted
on completion or failure. Passwords and session keys are passed in the
process environment; item JSON is sent on stdin, never in command arguments.
A temporary loopback HTTPS bridge supplies a certificate trusted only by
that CLI process. It does not change system trust or the live ingress.

Account registration and organization creation retain the small bootstrap
crypto implementation because the official CLI cannot perform them. The
admin API sends account invitations with a generated admin password backed
by an Argon2 PHC hash in the project's private environment. The owner approved
using `/api/organizations/{id}/users/invite` for organization invitations:
`/admin/invite` cannot add organization membership. The bootstrap HTTP login
is limited to authenticating those unsupported organization operations.

The isolated integration project is `mu3lab-test-vaultwarden` on port 19902.
It creates an owner and member, shares a collection/login, verifies the member
can decrypt it through the official CLI, then its runner removes the test
containers and volume. No production project or data is used. Run it with
`.venv/bin/python -m tools.test_vaultwarden`.

Sources: https://github.com/bitwarden/clients/releases/tag/cli-v2026.5.0,
https://github.com/dani-garcia/vaultwarden/blob/1.37.3/src/api/admin.rs,
https://github.com/dani-garcia/vaultwarden/blob/1.37.3/src/api/core/organizations.rs.
