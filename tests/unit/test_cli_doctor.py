from __future__ import annotations

from pathlib import Path

from hermes_tiered_memory.cli import main
from scripts.generate_protected_config import build_signed_config, generate_ed25519_keypair


def _signed_files(
    tmp_path: Path, *, expires_at: str = "2099-01-01T00:00:00Z"
) -> tuple[Path, Path, str]:
    private, _, public_b64 = generate_ed25519_keypair()
    payload, signature = build_signed_config(
        private,
        key_id="doctor-key",
        config_id="doctor-config",
        host="hindsight.example.test",
        port=443,
        base_path="/api/v1",
        sequence=1,
        issued_at="2026-01-01T00:00:00Z",
        not_before="2026-01-01T00:00:00Z",
        expires_at=expires_at,
    )
    config = tmp_path / "protected.json"
    sig = tmp_path / "protected.sig"
    config.write_bytes(payload)
    sig.write_bytes(signature)
    config.chmod(0o600)
    sig.chmod(0o600)
    return config, sig, public_b64


def _doctor_args(config: Path, sig: Path, public_b64: str, *extra: str) -> list[str]:
    return [
        "doctor",
        "--config",
        str(config),
        "--signature",
        str(sig),
        "--verification-key",
        f"doctor-key={public_b64}",
        *extra,
    ]


def test_doctor_reports_passes_and_skips_optional_l2(monkeypatch, tmp_path, capsys) -> None:
    config, sig, public_b64 = _signed_files(tmp_path)
    monkeypatch.setattr("hermes_tiered_memory.cli._module_available", lambda _: True)

    assert main(_doctor_args(config, sig, public_b64)) == 0

    output = capsys.readouterr().out
    assert "PASS optional extra: mnemosyne" in output
    assert "PASS optional extra: hindsight" in output
    assert "PASS protected config: valid signature and unexpired" in output
    assert "PASS L1: available" in output
    assert "SKIP L2: no endpoint configured" in output
    assert public_b64 not in output


def test_doctor_reports_successful_l2_reachability(monkeypatch, tmp_path, capsys) -> None:
    config, sig, public_b64 = _signed_files(tmp_path)
    monkeypatch.setattr("hermes_tiered_memory.cli._module_available", lambda _: True)
    monkeypatch.setattr(
        "hermes_tiered_memory.cli._check_l2_reachability",
        lambda *_args, **_kwargs: (True, "reachable"),
    )

    assert main(_doctor_args(config, sig, public_b64, "--l2-url", "https://l2.example.test")) == 0
    assert "PASS L2: reachable" in capsys.readouterr().out


def test_doctor_fails_closed_for_bad_signature_and_does_not_mutate_config(
    monkeypatch, tmp_path, capsys
) -> None:
    config, sig, public_b64 = _signed_files(tmp_path)
    sig.write_bytes(b"not-a-signature")
    monkeypatch.setattr("hermes_tiered_memory.cli._module_available", lambda _: True)
    before = config.read_bytes()

    assert main(_doctor_args(config, sig, public_b64)) != 0

    output = capsys.readouterr().out
    assert "FAIL protected config:" in output
    assert "not-a-signature" not in output
    assert config.read_bytes() == before
    assert not (tmp_path / "protected.json.replay-state").exists()


def test_doctor_fails_on_expiry_and_missing_l1(monkeypatch, tmp_path, capsys) -> None:
    config, sig, public_b64 = _signed_files(tmp_path, expires_at="2020-01-01T00:00:00Z")
    monkeypatch.setattr(
        "hermes_tiered_memory.cli._module_available",
        lambda module: module != "mnemosyne",
    )

    assert main(_doctor_args(config, sig, public_b64)) != 0

    output = capsys.readouterr().out
    assert "FAIL protected config:" in output
    assert "expired" in output.lower()
    assert "FAIL L1: unavailable" in output


def test_doctor_l2_reachability_failure_is_nonzero(monkeypatch, tmp_path, capsys) -> None:
    config, sig, public_b64 = _signed_files(tmp_path)
    monkeypatch.setattr("hermes_tiered_memory.cli._module_available", lambda _: True)
    monkeypatch.setattr(
        "hermes_tiered_memory.cli._check_l2_reachability",
        lambda *_args, **_kwargs: (False, "endpoint unreachable"),
    )

    assert main(_doctor_args(config, sig, public_b64, "--l2-url", "https://l2.example.test")) != 0

    output = capsys.readouterr().out
    assert "FAIL L2: endpoint unreachable" in output
    assert "l2.example.test" not in output


def test_doctor_rejects_malformed_verification_key_without_leaking_material(
    monkeypatch, tmp_path, capsys
) -> None:
    config, sig, _ = _signed_files(tmp_path)
    monkeypatch.setattr("hermes_tiered_memory.cli._module_available", lambda _: True)
    secret = "definitely-not-a-key"

    assert (
        main(
            [
                "doctor",
                "--config",
                str(config),
                "--signature",
                str(sig),
                "--verification-key",
                f"x={secret}",
            ]
        )
        != 0
    )

    output = capsys.readouterr().out
    assert "FAIL protected config:" in output
    assert secret not in output
