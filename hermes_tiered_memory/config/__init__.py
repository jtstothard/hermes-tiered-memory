"""Protected configuration validation and loading."""

import hashlib
import hmac
import json
import os
import pathlib
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519
import cryptography.hazmat.primitives.serialization as serialization


class ProtectedConfigError(Exception):
    """Base exception for protected configuration errors."""

    pass


class ProtectedConfigValidationError(ProtectedConfigError):
    """Raised when configuration validation fails."""

    pass


class ProtectedConfigAuthError(ProtectedConfigError):
    """Raised when configuration authentication fails."""

    pass


class ProtectedConfigFreshnessError(ProtectedConfigError):
    """Raised when configuration is stale or replayed."""

    pass


@dataclass(frozen=True)
class HindsightEndpointConfig:
    """Immutable Hindsight endpoint configuration."""

    scheme: Literal["https"]
    host: str  # IDNA-encoded canonical hostname
    port: int
    base_path: str
    verify_tls: bool
    ca_file: str | None
    proxy_enabled: bool

    def to_url(self) -> str:
        """Return the base URL for Hindsight API calls."""
        return f"{self.scheme}://{self.host}:{self.port}{self.base_path}"


@dataclass(frozen=True)
class ProtectedConfig:
    """Immutable protected configuration with metadata."""

    schema_version: int
    key_id: str
    config_id: str
    sequence: int
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    hindsight: HindsightEndpointConfig
    _raw_payload: bytes
    _signature: bytes | None = None

    @property
    def payload_digest(self) -> str:
        """SHA-256 digest of the raw JCS payload."""
        return hashlib.sha256(self._raw_payload).hexdigest()


class ProtectedConfigLoader:
    """Load and validate protected configuration with integrity authentication."""

    DOMAIN_SEPARATOR = b"hermes-tiered-memory/config/v1\0"
    CLOCK_SKEW_SECONDS = 300  # 5 minutes
    ALGORITHM_HMAC_SHA256 = "hmac-sha-256"
    ALGORITHM_ED25519 = "ed25519"
    SUPPORTED_ALGORITHMS = {ALGORITHM_HMAC_SHA256, ALGORITHM_ED25519}

    # Pinned authorized keys (key_id -> secret for HMAC, public key for Ed25519)
    # In production, these would be loaded from a secure key store
    _pinned_keys: dict[str, bytes | tuple[bytes, bytes]] = {}

    @classmethod
    def register_key(cls, key_id: str, secret_or_keypair: bytes | tuple[bytes, bytes]) -> None:
        """Register a pinned authorized key."""
        cls._pinned_keys[key_id] = secret_or_keypair

    @classmethod
    def clear_keys(cls) -> None:
        """Clear all pinned keys (for testing)."""
        cls._pinned_keys.clear()

    def __init__(self, config_path: pathlib.Path, signature_path: pathlib.Path | None = None, replay_state_path: pathlib.Path | None = None):
        """Initialize the loader with configuration and optional signature paths."""
        self.config_path = config_path
        self.signature_path = signature_path
        self.replay_state_path = replay_state_path or config_path.with_name(f"{config_path.name}.replay-state")

        # Track accepted sequence and digest for rollback prevention
        self._accepted_sequence: int | None = None
        self._accepted_digest: str | None = None
        self._last_config_id: str | None = None
        self._load_replay_state()

    def _validate_file(self, path: pathlib.Path, must_exist: bool = True) -> None:
        """Validate file permissions and ownership."""
        # Reject links before existence checks, stat calls, or reads.
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            path_stat = None
        if path_stat is not None and stat.S_ISLNK(path_stat.st_mode):
            raise ProtectedConfigValidationError(f"Protected config path is a symlink: {path}")
        if must_exist and path_stat is None:
            raise ProtectedConfigValidationError(f"Protected config file does not exist: {path}")
        if path_stat is None:
            return

        stat_result = path_stat

        if not stat.S_ISREG(stat_result.st_mode):
            raise ProtectedConfigValidationError(f"Protected config path is not a regular file: {path}")
        current_uid = getattr(os, "getuid", lambda: None)()
        if current_uid is not None and stat_result.st_uid not in {0, current_uid}:
            raise ProtectedConfigValidationError(f"Protected config file has unsafe ownership: {path}")

        # Reject group/world writable
        if stat_result.st_mode & 0o022:
            raise ProtectedConfigValidationError(
                f"Protected config file is group/world writable: {path}"
            )

        # In production, verify owner is the profile/service identity
        # For now, just check the file is readable
        if not os.access(path, os.R_OK):
            raise ProtectedConfigValidationError(f"Protected config file is not readable: {path}")

    def _load_jcs_json(self, path: pathlib.Path) -> tuple[dict[str, Any], bytes]:
        """Load the strict canonical JSON subset used for signed configs.

        Full RFC 8785 number canonicalization is not claimed: no JCS
        dependency is available. Floats and non-finite numbers are rejected.
        """
        self._validate_file(path, must_exist=True)
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProtectedConfigValidationError(f"Invalid UTF-8 JSON in {path}: {e}") from e

        def object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ProtectedConfigValidationError(f"Duplicate key '{key}' in {path}")
                result[key] = value
            return result

        try:
            data = json.loads(text, object_pairs_hook=object_pairs_hook, parse_constant=lambda value: (_ for _ in ()).throw(ProtectedConfigValidationError(f"Unsupported non-finite number {value} in {path}")))
        except json.JSONDecodeError as e:
            raise ProtectedConfigValidationError(f"Invalid JSON in {path}: {e}")

        if not isinstance(data, dict):
            raise ProtectedConfigValidationError("Protected config root must be an object")

        def reject_float(value: Any) -> Any:
            if type(value) is float:
                raise ProtectedConfigValidationError(f"Unsupported floating-point number in {path}")
            if isinstance(value, dict):
                return {key: reject_float(item) for key, item in value.items()}
            if isinstance(value, list):
                return [reject_float(item) for item in value]
            return value

        data = reject_float(data)

        # Restricted canonical compact, sorted JSON; full RFC 8785 numeric
        # canonicalization is intentionally not claimed without a JCS library.
        raw_bytes = json.dumps(
            data, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")

        if content != raw_bytes:
            raise ProtectedConfigValidationError(f"Protected config is not encoded as exact canonical JSON: {path}")
        return data, content

    def _load_replay_state(self) -> None:
        if not self.replay_state_path.exists():
            return
        self._validate_file(self.replay_state_path)
        try:
            state = json.loads(self.replay_state_path.read_bytes().decode("utf-8"))
            if (not isinstance(state, dict) or set(state) != {"config_id", "sequence", "digest"} or not isinstance(state.get("config_id"), str) or type(state.get("sequence")) is not int or not isinstance(state.get("digest"), str)):
                raise ValueError("invalid replay state shape")
            self._last_config_id = state["config_id"]
            self._accepted_sequence = state["sequence"]
            self._accepted_digest = state["digest"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
            raise ProtectedConfigValidationError(f"Invalid protected config replay state: {self.replay_state_path}") from e

    def _persist_replay_state(self, config: ProtectedConfig) -> None:
        parent = self.replay_state_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = self.replay_state_path.with_name(f".{self.replay_state_path.name}.tmp")
        self._validate_file(self.replay_state_path, must_exist=False)
        self._validate_file(temporary, must_exist=False)
        payload = json.dumps({"config_id": config.config_id, "sequence": config.sequence, "digest": config.payload_digest}, separators=(",", ":"), sort_keys=True).encode("utf-8")
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.replay_state_path)
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()

    def _parse_timestamp(self, value: str) -> datetime:
        """Parse RFC 3339 UTC timestamp."""
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError as e:
            raise ProtectedConfigValidationError(f"Invalid timestamp '{value}': {e}")

    def _validate_config_schema(self, data: dict[str, Any]) -> None:
        """Validate the configuration schema."""
        required_fields = {
            "schema_version",
            "key_id",
            "config_id",
            "sequence",
            "issued_at",
            "not_before",
            "expires_at",
            "hindsight",
        }

        missing = required_fields - set(data.keys())
        if missing:
            raise ProtectedConfigValidationError(f"Missing required fields: {missing}")

        unknown = set(data) - required_fields
        if unknown:
            raise ProtectedConfigValidationError(f"Unsupported config fields: {unknown}")

        # Validate schema_version
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ProtectedConfigValidationError(
                f"Unsupported schema_version: {data['schema_version']}"
            )

        # Validate sequence is positive integer
        if type(data["sequence"]) is not int or data["sequence"] <= 0:
            raise ProtectedConfigValidationError(f"Invalid sequence: {data['sequence']}")

        # Validate timestamps are strings before parsing
        for field in ["issued_at", "not_before", "expires_at"]:
            if not isinstance(data[field], str):
                raise ProtectedConfigValidationError(f"Invalid {field}: expected string, got {type(data[field]).__name__}")
        data["issued_at_parsed"] = self._parse_timestamp(data["issued_at"])
        data["not_before_parsed"] = self._parse_timestamp(data["not_before"])
        data["expires_at_parsed"] = self._parse_timestamp(data["expires_at"])

        # Validate timestamp ordering
        if not (
            data["issued_at_parsed"] <= data["not_before_parsed"] <= data["expires_at_parsed"]
        ):
            raise ProtectedConfigValidationError("Invalid timestamp ordering")

        # Validate hindsight config
        hindsight = data["hindsight"]
        if not isinstance(hindsight, dict):
            raise ProtectedConfigValidationError(
                f"Invalid hindsight: expected object, got {type(hindsight).__name__}"
            )
        required_hindsight_fields = {"scheme", "host", "port", "base_path", "tls", "proxy"}

        missing_hindsight = required_hindsight_fields - set(hindsight.keys())
        if missing_hindsight:
            raise ProtectedConfigValidationError(
                f"Missing required hindsight fields: {missing_hindsight}"
            )

        unknown_hindsight = set(hindsight) - required_hindsight_fields
        if unknown_hindsight:
            raise ProtectedConfigValidationError(f"Unsupported hindsight fields: {unknown_hindsight}")

        # Validate scheme is a string
        if not isinstance(hindsight["scheme"], str):
            raise ProtectedConfigValidationError(
                f"Invalid scheme: expected string, got {type(hindsight['scheme']).__name__}"
            )
        if hindsight["scheme"] != "https":
            raise ProtectedConfigValidationError(f"Invalid scheme: {hindsight['scheme']}")

        # Validate port
        if type(hindsight["port"]) is not int or not (1 <= hindsight["port"] <= 65535):
            raise ProtectedConfigValidationError(f"Invalid port: {hindsight['port']}")

        # Validate base_path is a string and starts with /
        if not isinstance(hindsight["base_path"], str):
            raise ProtectedConfigValidationError(
                f"Invalid base_path: expected string, got {type(hindsight['base_path']).__name__}"
            )
        if not hindsight["base_path"].startswith("/"):
            raise ProtectedConfigValidationError(
                f"Invalid base_path (must start with /): {hindsight['base_path']}"
            )

        # Validate TLS config
        tls = hindsight["tls"]
        if not isinstance(tls, dict) or set(tls) != {"verify", "ca_file"}:
            raise ProtectedConfigValidationError("Unsupported or invalid tls fields")
        if not isinstance(tls.get("verify"), bool):
            raise ProtectedConfigValidationError(f"Invalid tls.verify: {tls.get('verify')}")
        if not tls["verify"]:
            raise ProtectedConfigValidationError("Invalid tls.verify: TLS verification must be enabled")

        ca_file = tls.get("ca_file")
        if ca_file is not None:
            if (
                not isinstance(ca_file, str)
                or not ca_file.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in ca_file)
            ):
                raise ProtectedConfigValidationError("Invalid tls.ca_file: expected a canonical absolute path")

            ca_path = pathlib.Path(ca_file)
            if not ca_path.is_absolute():
                raise ProtectedConfigValidationError("Invalid tls.ca_file: expected a canonical absolute path")
            try:
                canonical_ca_path = ca_path.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as e:
                raise ProtectedConfigValidationError(
                    "Invalid tls.ca_file: expected an existing readable file"
                ) from e

            if str(canonical_ca_path) != ca_file or not canonical_ca_path.is_file():
                raise ProtectedConfigValidationError(
                    "Invalid tls.ca_file: expected a canonical absolute regular file"
                )
            if not stat.S_IMODE(canonical_ca_path.stat().st_mode) & 0o444:
                raise ProtectedConfigValidationError(
                    "Invalid tls.ca_file: expected an existing readable file"
                )
            try:
                with canonical_ca_path.open("rb"):
                    pass
            except OSError as e:
                raise ProtectedConfigValidationError(
                    "Invalid tls.ca_file: expected an existing readable file"
                ) from e

        # Validate proxy config
        proxy = hindsight["proxy"]
        if not isinstance(proxy, dict) or set(proxy) != {"enabled"}:
            raise ProtectedConfigValidationError("Unsupported or invalid proxy fields")
        if not isinstance(proxy.get("enabled"), bool):
            raise ProtectedConfigValidationError(f"Invalid proxy.enabled: {proxy.get('enabled')}")

        if proxy.get("enabled"):
            raise ProtectedConfigValidationError("Proxy is not supported in the first release")

    def _validate_freshness(self, config: ProtectedConfig) -> None:
        """Validate config freshness (clock skew, expiry, sequence)."""
        now = datetime.now(timezone.utc)

        # Check clock skew
        if config.not_before > now + timedelta(seconds=self.CLOCK_SKEW_SECONDS):
            raise ProtectedConfigFreshnessError(
                f"Config not_before is too far in the future: {config.not_before}"
            )

        if now > config.expires_at + timedelta(seconds=self.CLOCK_SKEW_SECONDS):
            raise ProtectedConfigFreshnessError(f"Config has expired: {config.expires_at}")

        if self._accepted_sequence is not None and config.config_id == self._last_config_id:
            if config.sequence < self._accepted_sequence:
                raise ProtectedConfigFreshnessError(
                    f"Config sequence {config.sequence} is less than accepted {self._accepted_sequence}"
                )
            if config.sequence == self._accepted_sequence and config.payload_digest != self._accepted_digest:
                raise ProtectedConfigFreshnessError(f"Config sequence {config.sequence} changed payload without incrementing sequence")

    def _validate_signature(
        self, payload: bytes, key_id: str, signature_data: bytes | None
    ) -> None:
        """Validate the signature over the payload."""
        if signature_data is None:
            raise ProtectedConfigAuthError("Signature is required")

        # Check key is authorized
        if key_id not in self._pinned_keys:
            raise ProtectedConfigAuthError(f"Unauthorized key_id: {key_id}")

        key_material = self._pinned_keys[key_id]

        # Try HMAC-SHA-256 first
        try:
            secret = key_material if isinstance(key_material, bytes) else None
            if secret is not None:
                expected_sig = hmac.new(secret, self.DOMAIN_SEPARATOR + payload, hashlib.sha256).digest()

                if not hmac.compare_digest(expected_sig, signature_data):
                    raise ProtectedConfigAuthError(
                        f"Invalid {self.ALGORITHM_HMAC_SHA256} signature"
                    )
                return
        except Exception:
            pass

        # Try Ed25519
        try:
            public_key_bytes = (
                key_material[0]
                if isinstance(key_material, tuple)
                else None
            )
            if public_key_bytes is not None:
                public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
                public_key.verify(signature_data, self.DOMAIN_SEPARATOR + payload)
                return
        except Exception as e:
            raise ProtectedConfigAuthError(f"Invalid {self.ALGORITHM_ED25519} signature: {e}")

        raise ProtectedConfigAuthError("Unsupported signature algorithm")

    def _parse_hindsight_config(self, data: dict[str, Any]) -> HindsightEndpointConfig:
        """Parse Hindsight endpoint config from validated data."""
        hindsight = data["hindsight"]
        tls = hindsight["tls"]

        return HindsightEndpointConfig(
            scheme=hindsight["scheme"],
            host=hindsight["host"],
            port=hindsight["port"],
            base_path=hindsight["base_path"],
            verify_tls=tls["verify"],
            ca_file=tls.get("ca_file"),
            proxy_enabled=hindsight["proxy"]["enabled"],
        )

    def load(self) -> ProtectedConfig:
        """Load, validate, and authenticate protected configuration."""
        # Validate files
        self._validate_file(self.config_path, must_exist=True)
        if self.signature_path:
            self._validate_file(self.signature_path, must_exist=True)

        # Load and validate config
        data, raw_payload = self._load_jcs_json(self.config_path)
        self._validate_config_schema(data)

        # Load signature
        signature = None
        if self.signature_path and self.signature_path.exists():
            signature = self.signature_path.read_bytes()

        # Validate signature
        self._validate_signature(raw_payload, data["key_id"], signature)

        # Build config object
        hindsight_config = self._parse_hindsight_config(data)
        config = ProtectedConfig(
            schema_version=data["schema_version"],
            key_id=data["key_id"],
            config_id=data["config_id"],
            sequence=data["sequence"],
            issued_at=data["issued_at_parsed"],
            not_before=data["not_before_parsed"],
            expires_at=data["expires_at_parsed"],
            hindsight=hindsight_config,
            _raw_payload=raw_payload,
            _signature=signature,
        )

        # Validate freshness
        self._validate_freshness(config)

        # Persist accepted sequence and digest for rollback prevention
        self._last_config_id = config.config_id
        self._accepted_sequence = config.sequence
        self._accepted_digest = config.payload_digest
        self._persist_replay_state(config)

        return config


__all__ = [
    "HindsightEndpointConfig",
    "ProtectedConfig",
    "ProtectedConfigAuthError",
    "ProtectedConfigError",
    "ProtectedConfigFreshnessError",
    "ProtectedConfigLoader",
    "ProtectedConfigValidationError",
]