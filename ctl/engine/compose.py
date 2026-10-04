"""One app's generated Compose project, and every Docker command Mu3Lab runs against it.

The file set is decided here and nowhere else: the app's ``docker-compose.yml``,
the per-machine release record (``docker-compose.digest.yml``), the GPU override
for this computer (``docker-compose.<nvidia|amd>.yml``) and any first-start
overrides a rule adds. Commands go through ``ctl.actions`` so they share its
Docker-group handling, logging and cancellation checks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ctl import actions
from ctl.jobs import redact

Log = Callable[[str], None]
RELEASE_RECORD = "docker-compose.digest.yml"
MARKER_OUTPUT = "MU3LAB_OUTPUT "
MARKER_ERROR = "MU3LAB_ERROR "


@dataclass(frozen=True)
class ScriptResult:
    ok: bool
    outputs: dict[str, str] = field(default_factory=dict)
    error: str = ""
    raw: str = ""


def parse_script_output(output: str) -> tuple[dict[str, str], str]:
    """Collect ``MU3LAB_OUTPUT key=value`` lines and the first ``MU3LAB_ERROR`` message."""
    outputs: dict[str, str] = {}
    error = ""
    for line in output.splitlines():
        if line.startswith(MARKER_OUTPUT):
            key, _, value = line.removeprefix(MARKER_OUTPUT).partition("=")
            if key.strip() and value and "\r" not in value:
                outputs[key.strip()] = value.strip()
        elif MARKER_ERROR in line and not error:
            error = line.split(MARKER_ERROR, 1)[1].strip()
    return outputs, error


@dataclass(frozen=True)
class Compose:
    directory: Path
    gpu_mode: str = "cpu"

    @property
    def file(self) -> Path:
        return self.directory / "docker-compose.yml"

    def files(self, extra: Sequence[Path] = ()) -> list[Path]:
        files = [self.file]
        if (self.directory / RELEASE_RECORD).is_file():
            files.append(self.directory / RELEASE_RECORD)
        gpu = self.directory / f"docker-compose.{self.gpu_mode}.yml"
        if self.gpu_mode != "cpu" and gpu.is_file():
            files.append(gpu)
        files.extend(extra)
        return files

    def overrides(self, extra: Sequence[Path] = ()) -> list[Path]:
        """Everything layered on the main file (what ``actions`` calls extra files)."""
        return [path for path in self.files(extra) if path.name not in ("docker-compose.yml", RELEASE_RECORD)]

    def validate(self, log: Log) -> tuple[int, str]:
        return actions.compose_config(self.directory, log)

    def up(
        self,
        log: Log,
        *,
        wait_seconds: int | None,
        recreate: bool = False,
        services: Sequence[str] = (),
        env: dict[str, str] | None = None,
        extra: Sequence[Path] = (),
    ) -> tuple[int, str]:
        timeout = (wait_seconds or 0) + 300
        return actions.compose_up(
            self.directory,
            log,
            timeout=timeout,
            env=env,
            extra_files=self.overrides(extra),
            wait_timeout=wait_seconds,
            recreate=recreate,
            services=list(services) or None,
        )

    def down(self, log: Log) -> tuple[int, str]:
        return actions.compose_down(self.directory, log)

    def action(self, verb: str, log: Log, env: dict[str, str] | None = None) -> tuple[int, str]:
        return actions.compose_action(self.directory, verb, log, env=env, extra_files=self.overrides())

    def exec(self, service: str, argv: Sequence[str], log: Log, *, timeout: int = 300) -> tuple[int, str]:
        return actions.compose_exec(self.directory, service, list(argv), log, timeout=timeout)

    def run_script(
        self,
        service: str,
        interpreter: Sequence[str],
        script: str,
        log: Log,
        *,
        args: Sequence[str] = (),
        timeout: int = 300,
    ) -> ScriptResult:
        """Pipe ``script`` into ``interpreter`` inside a running container.

        The script travels on standard input, so it never appears in a command
        line or log; only its ``MU3LAB_OUTPUT``/``MU3LAB_ERROR`` lines are kept.
        """
        argv = ["docker", "compose"]
        for path in self.files():
            argv.extend(["-f", str(path)])
        argv.extend(["--project-directory", str(self.directory), "exec", "-T", service, *interpreter, *args])
        rc, output = actions.docker_cmd_with_stdin(argv, script, lambda _line: None, timeout=timeout)
        outputs, error = parse_script_output(output)
        if rc and not error:
            error = "the setup script inside the app did not finish"
        if rc:
            log(f"Setup script in {service} stopped: {redact(error)}")
        return ScriptResult(ok=rc == 0 and not error, outputs=outputs, error=error, raw=output)
