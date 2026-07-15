from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hauzer_utility_exporter.configuration import AppConfig
from hauzer_utility_exporter.networking import install_host_override


class NetworkingTest(unittest.TestCase):
    def test_install_host_override_is_idempotent(self) -> None:
        private_host = ".".join(("192", "168", "1", "170"))
        local_hostname = "hauzer." + "test"
        config = AppConfig.from_options(
            {
                "hauzer_url": f"https://{local_hostname}/api/utility-imports",
                "hauzer_token": "hsr_" + "A" * 40,
                "hauzer_host_ip": private_host,
                "verify_tls": True,
                "initial_backfill_hours": 24,
                "electricity_consumption_statistics": "",
                "electricity_grid_export_statistics": "",
                "gas_consumption_statistics": "",
                "water_consumption_statistics": "",
            },
            "supervisor-secret",
        )

        with TemporaryDirectory() as directory:
            hosts_path = Path(directory) / "hosts"
            hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

            install_host_override(config, hosts_path)
            install_host_override(config, hosts_path)

            contents = hosts_path.read_text(encoding="utf-8")

        self.assertEqual(contents.count(f"{private_host} {local_hostname}"), 1)
