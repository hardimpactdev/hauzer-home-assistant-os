from pathlib import Path
import struct
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "hauzer_utility_exporter"


class PackagingTest(unittest.TestCase):
    def test_manifest_declares_public_supervisor_app_contract(self) -> None:
        repository = yaml.safe_load((ROOT / "repository.yaml").read_text())
        config = yaml.safe_load((APP / "config.yaml").read_text())

        self.assertEqual(repository["name"], "Hauzer Home Assistant Apps")
        self.assertEqual(repository["url"], "https://hauzer.app")
        self.assertEqual(repository["maintainer"], "Hard Impact <support@hauzer.app>")
        self.assertEqual(config["slug"], "hauzer_utility_exporter")
        self.assertEqual(config["version"], "0.1.0")
        self.assertEqual(config["url"], "https://hauzer.app")
        self.assertEqual(config["image"], "ghcr.io/hardimpactdev/hauzer-home-assistant-os")
        self.assertEqual(config["arch"], ["aarch64", "amd64"])
        self.assertEqual(
            config["options"]["hauzer_url"],
            "https://hauzer.app/api/utility-imports",
        )
        self.assertTrue(config["homeassistant_api"])
        for default_field in ("apparmor", "boot", "hassio_role", "startup"):
            self.assertNotIn(default_field, config)
        self.assertIn("aarch64", config["arch"])
        self.assertEqual(config["schema"]["hauzer_token"], "password")
        self.assertEqual(config["schema"]["hauzer_host_ip"], "str?")
        self.assertTrue(config["options"]["verify_tls"])
        self.assertEqual(config["options"]["initial_backfill_hours"], 168)

    def test_runtime_uses_s6_and_current_app_labels(self) -> None:
        run_script = (APP / "rootfs/etc/services.d/hauzer-utility-exporter/run").read_text()
        finish_script = (APP / "rootfs/etc/services.d/hauzer-utility-exporter/finish").read_text()
        dockerfile = (APP / "Dockerfile").read_text()

        self.assertIn("exec python3 -m hauzer_utility_exporter", run_script)
        self.assertIn("/run/s6-linux-init-container-results/exitcode", finish_script)
        self.assertIn("websocket-client==1.8.0", dockerfile)
        self.assertIn("update-ca-certificates", dockerfile)
        self.assertIn('io.hass.type="app"', dockerfile)
        self.assertIn("io.hass.version", dockerfile)
        self.assertIn("io.hass.arch", dockerfile)

    def test_apparmor_allows_only_required_runtime_surfaces(self) -> None:
        profile = (APP / "apparmor.txt").read_text()
        config = yaml.safe_load((APP / "config.yaml").read_text())

        self.assertIn("/data/** rw", profile)
        self.assertIn("network inet stream", profile)
        self.assertIn("network inet6 stream", profile)
        self.assertIn("signal (receive)", profile)
        self.assertIn("/etc/hosts rw", profile)
        self.assertNotIn("/homeassistant/", profile)
        self.assertNotIn("ports", config)
        self.assertNotIn("ingress", config)

    def test_public_tree_excludes_private_development_surfaces(self) -> None:
        excluded_paths = [
            ROOT / ".env.example",
            ROOT / "config",
            ROOT / "scripts",
            ROOT / "src",
            ROOT / "tests/test_ha_side_check.py",
            APP
            / "rootfs/usr/local/share/ca-certificates"
            / ("orbit-" + "root.crt"),
        ]

        for path in excluded_paths:
            self.assertFalse(path.exists(), str(path.relative_to(ROOT)))

        forbidden_fragments = [
            "." + "nmbp",
            "192." + "168.",
            "10." + "6.",
            "orbit-" + "root",
        ]

        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
                continue

            try:
                contents = path.read_text()
            except UnicodeDecodeError:
                continue

            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, contents, str(path.relative_to(ROOT)))

    def test_public_documentation_and_store_artwork_are_complete(self) -> None:
        required_files = [
            ROOT / "LICENSE",
            ROOT / "SECURITY.md",
            ROOT / "CONTRIBUTING.md",
            APP / "icon.png",
            APP / "logo.png",
        ]

        for path in required_files:
            self.assertTrue(path.is_file(), str(path.relative_to(ROOT)))

        install_url = (
            "https://my.home-assistant.io/redirect/"
            "supervisor_add_addon_repository/"
            "?repository_url=https%3A%2F%2Fgithub.com%2Fhardimpactdev%2F"
            "hauzer-home-assistant-os"
        )
        public_docs = [ROOT / "README.md", APP / "README.md", APP / "DOCS.md"]

        for path in public_docs:
            contents = path.read_text().lower()
            self.assertIn(install_url.lower(), contents, str(path.relative_to(ROOT)))
            for fragment in (
                "private",
                "." + "nmbp",
                "192." + "168.",
                "10." + "6.",
                "ssh",
                "long-lived access token",
            ):
                self.assertNotIn(fragment, contents, str(path.relative_to(ROOT)))

        self.assertEqual(self._png_dimensions(APP / "icon.png"), (128, 128))
        self.assertEqual(self._png_dimensions(APP / "logo.png"), (250, 100))

    def test_pull_request_workflows_enforce_quality_and_security(self) -> None:
        test_workflow_path = ROOT / ".github/workflows/test.yaml"
        lint_workflow_path = ROOT / ".github/workflows/lint.yaml"
        dependabot_path = ROOT / ".github/dependabot.yml"

        for path in (test_workflow_path, lint_workflow_path, dependabot_path):
            self.assertTrue(path.is_file(), str(path.relative_to(ROOT)))

        test_workflow = yaml.safe_load(test_workflow_path.read_text())
        lint_workflow = yaml.safe_load(lint_workflow_path.read_text())
        dependabot = yaml.safe_load(dependabot_path.read_text())
        test_text = test_workflow_path.read_text()
        lint_text = lint_workflow_path.read_text()

        self.assertIn("pull_request", self._workflow_triggers(test_workflow))
        self.assertIn("push", self._workflow_triggers(test_workflow))
        self.assertEqual(test_workflow["permissions"], {"contents": "read"})
        self.assertIn("python-tests", test_workflow["jobs"])
        self.assertIn("container-smoke", test_workflow["jobs"])
        self.assertIn("secret-scan", test_workflow["jobs"])
        self.assertIn("actions/checkout@v7.0.0", test_text)
        self.assertIn("actions/setup-python@v6.3.0", test_text)
        self.assertIn(
            "zricethezav/gitleaks@sha256:"
            "cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854",
            test_text,
        )
        self.assertIn("python -m unittest discover -s tests -v", test_text)
        self.assertIn("tests.test_container_smoke", test_text)

        self.assertIn("pull_request", self._workflow_triggers(lint_workflow))
        self.assertIn("schedule", self._workflow_triggers(lint_workflow))
        self.assertEqual(lint_workflow["permissions"], {"contents": "read"})
        self.assertIn("frenck/action-addon-linter@v2.21", lint_text)
        self.assertIn("path: ./hauzer_utility_exporter", lint_text)

        ecosystems = {update["package-ecosystem"] for update in dependabot["updates"]}
        self.assertEqual(ecosystems, {"github-actions", "pip"})
        for update in dependabot["updates"]:
            self.assertEqual(update["open-pull-requests-limit"], 5)
            self.assertEqual(update["schedule"]["interval"], "weekly")

    def test_release_workflows_publish_and_sign_multi_arch_images(self) -> None:
        build_path = ROOT / ".github/workflows/build-app.yaml"
        release_path = ROOT / ".github/workflows/release.yaml"

        for path in (build_path, release_path):
            self.assertTrue(path.is_file(), str(path.relative_to(ROOT)))

        build_workflow = yaml.safe_load(build_path.read_text())
        release_workflow = yaml.safe_load(release_path.read_text())
        build_text = build_path.read_text()
        release_text = release_path.read_text()

        self.assertIn("workflow_call", self._workflow_triggers(build_workflow))
        self.assertIn("push", self._workflow_triggers(release_workflow))
        self.assertIn('"v*.*.*"', release_text)
        self.assertIn("test \"$tag_version\" = \"$manifest_version\"", release_text)
        self.assertIn(
            "home-assistant/actions/helpers/info@"
            "f4ca6f671bd429efb108c0f2fa0ae8af0215986c",
            build_text,
        )
        self.assertIn(
            "home-assistant/builder/actions/prepare-multi-arch-matrix@2026.06.0",
            build_text,
        )
        self.assertIn(
            "home-assistant/builder/actions/build-image@2026.06.0",
            build_text,
        )
        self.assertIn(
            "home-assistant/builder/actions/publish-multi-arch-manifest@2026.06.0",
            build_text,
        )
        self.assertIn("sigstore/cosign-installer@v4.1.2", release_text)
        self.assertEqual(release_workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            release_workflow["jobs"]["sign"]["permissions"],
            {"contents": "read", "id-token": "write", "packages": "write"},
        )

    @staticmethod
    def _png_dimensions(path: Path) -> tuple[int, int]:
        contents = path.read_bytes()
        if contents[:8] != b"\x89PNG\r\n\x1a\n":
            raise AssertionError(f"{path.name} is not a PNG file")

        return struct.unpack(">II", contents[16:24])

    @staticmethod
    def _workflow_triggers(workflow: dict[object, object]) -> object:
        return workflow.get("on", workflow.get(True, {}))
