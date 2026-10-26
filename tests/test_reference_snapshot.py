import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/server/install_reference_snapshot.py"
spec = importlib.util.spec_from_file_location("reference_snapshot", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ReferenceSnapshotTests(unittest.TestCase):
    def fixture(self, root):
        contents = {
            "LICENSE": b"fixture license",
            "pyproject.toml": b"[project]\n",
            "uv.lock": b"version = 1\n",
            "envs/appworld/validate.py": b"# fixture\n",
            "src/harness_zero/student.py": b"# source\n",
            "data/appworld-harbor/split_train147.txt": "\n".join(installer.TRAIN_FIXTURES).encode(),
            "data/unrelated-benchmark/private-answer.txt": b"never installed",
        }
        archive = root / "reference.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for name, data in contents.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))
        files = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                 for name, data in sorted(contents.items())]
        canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        metadata = {"files": files, "content_sha256": hashlib.sha256(canonical).hexdigest(),
                    "archive_sha256": installer.sha256(archive)}
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps(metadata))
        return archive, manifest

    def test_installs_source_and_preserves_scope_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, manifest = self.fixture(root)
            destination = root / "installed"
            report = installer.install(archive, manifest, destination)
            self.assertEqual(report["selected_files_sha256_verified"], 6)
            self.assertFalse(report["complete_reference_checkout"])
            self.assertFalse(report["official_appworld_data_installed"])
            self.assertTrue((destination / "src/harness_zero/student.py").is_file())
            self.assertFalse((destination / "data/unrelated-benchmark").exists())

    def test_changed_manifest_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, manifest = self.fixture(root)
            metadata = json.loads(manifest.read_text())
            metadata["files"][0]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(metadata))
            destination = root / "installed"
            with self.assertRaisesRegex(ValueError, "manifest"):
                installer.install(archive, manifest, destination)
            self.assertFalse(destination.exists())

    def test_existing_directory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, manifest = self.fixture(root)
            destination = root / "installed"
            destination.mkdir()
            marker = destination / "my-work.txt"
            marker.write_text("keep")
            with self.assertRaises(FileExistsError):
                installer.install(archive, manifest, destination)
            self.assertEqual(marker.read_text(), "keep")
