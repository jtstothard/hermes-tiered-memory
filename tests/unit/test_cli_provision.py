from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from hermes_tiered_memory.cli import main
from hermes_tiered_memory.config import ProtectedConfigLoader


def test_provision_round_trips_signed_config_and_writes_reviewable_patch(tmp_path: Path) -> None:
    output = tmp_path / "provisioned"

    assert (
        main(
            [
                "provision",
                "--output-dir",
                str(output),
                "--key-id",
                "release-key",
                "--config-id",
                "release-config",
                "--host",
                "hindsight.example.test",
                "--auth-mode",
                "none",
                "--not-before",
                "2026-01-01T00:00:00Z",
                "--expires-at",
                "2099-01-01T00:00:00Z",
            ]
        )
        == 0
    )

    private_key = output / "release-key.private.pem"
    config_path = output / "protected-config.json"
    signature_path = output / "protected-config.sig"
    patch_path = output / "memory.tiered.patch.yaml"
    assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(signature_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(patch_path.stat().st_mode) == 0o600

    document = json.loads(config_path.read_text())
    patch = patch_path.read_text()
    private = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    public_key_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
    ).decode("ascii")
    ProtectedConfigLoader.clear_keys()
    ProtectedConfigLoader.register_verification_keys(
        [{"key_id": "release-key", "algorithm": "ed25519", "public_key_b64": public_key_b64}]
    )
    loaded = ProtectedConfigLoader(config_path, signature_path, tmp_path / "replay.json").load()
    assert loaded.config_id == "release-config"
    assert loaded.hindsight.host == document["hindsight"]["host"]
    assert 'auth_mode: "none"' in patch
    assert "api_key" not in patch
    ProtectedConfigLoader.clear_keys()


def test_provision_api_key_mode_only_emits_mode_and_does_not_read_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HINDSIGHT_API_KEY", "must-not-be-read")
    output = tmp_path / "out"

    assert (
        main(
            [
                "provision",
                "--output-dir",
                str(output),
                "--key-id",
                "k",
                "--config-id",
                "c",
                "--host",
                "h.example.test",
                "--auth-mode",
                "api_key",
                "--not-before",
                "2026-01-01T00:00:00Z",
                "--expires-at",
                "2099-01-01T00:00:00Z",
            ]
        )
        == 0
    )
    patch = (output / "memory.tiered.patch.yaml").read_text()
    assert 'auth_mode: "api_key"' in patch
    assert "HINDSIGHT_API_KEY" not in patch
    assert "secret" not in patch.lower()
