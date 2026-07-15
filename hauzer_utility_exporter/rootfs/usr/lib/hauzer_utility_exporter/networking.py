from pathlib import Path
from urllib.parse import urlparse

from hauzer_utility_exporter.configuration import AppConfig, ConfigurationError


def install_host_override(
    config: AppConfig,
    hosts_path: Path = Path("/etc/hosts"),
) -> None:
    if config.hauzer_host_ip is None:
        return

    hostname = urlparse(config.hauzer_url).hostname
    if hostname is None:
        raise ConfigurationError("The Hauzer URL must include a hostname.")

    entry = f"{config.hauzer_host_ip} {hostname}"
    contents = hosts_path.read_text(encoding="utf-8")
    if entry in contents.splitlines():
        return

    with hosts_path.open("a", encoding="utf-8") as hosts_file:
        if contents and not contents.endswith("\n"):
            hosts_file.write("\n")
        hosts_file.write(f"{entry}\n")
