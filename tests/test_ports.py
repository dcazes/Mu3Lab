"""Settings > Ports reads each published port's inside port from the app's Compose file."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctl.ports import _container_ports, _https


class ContainerPortTests(unittest.TestCase):
    def test_short_and_long_port_forms_map_to_the_port_inside(self):
        with tempfile.TemporaryDirectory() as folder:
            compose = Path(folder) / "docker-compose.yml"
            compose.write_text(
                """
services:
  app:
    ports:
      - "127.0.0.1:13378:80"
      - "${MU3LAB_HOME_IP:?MU3LAB_HOME_IP is required}:8096:8096"
      - "${MU3LAB_TAILNET_IP:?required}:7882:7882/udp"
      - target: 9000
        published: 9001
""",
                encoding="utf-8",
            )
            self.assertEqual(
                _container_ports(compose),
                {(13378, "tcp"): 80, (8096, "tcp"): 8096, (7882, "udp"): 7882, (9001, "tcp"): 9000},
            )

    def test_a_missing_or_broken_compose_file_maps_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(_container_ports(Path(folder) / "docker-compose.yml"), {})
            broken = Path(folder) / "broken.yml"
            broken.write_text("services: [", encoding="utf-8")
            self.assertEqual(_container_ports(broken), {})

    def test_the_default_https_port_is_left_out_of_the_address(self):
        self.assertEqual(_https("host.ts.net", 443), "https://host.ts.net")
        self.assertEqual(_https("host.ts.net", 8448), "https://host.ts.net:8448")


if __name__ == "__main__":
    unittest.main()
