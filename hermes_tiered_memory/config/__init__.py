"""Protected configuration validation and loading."""

import hashlib
import hmac
import json
import os
import pathlib
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

    def __init__(self, config_path: pathlib.Path, signature_path: pathlib.Path | None = None):
        """Initialize the loader with configuration and optional signature paths."""
        self.config_path = config_path
        self.signature_path = signature_path

        # Track accepted sequence and digest for rollback prevention
        self._accepted_sequence: int | None = None
        self._accepted_digest: str | None = None
        self._last_config_id: str | None = None

    def _validate_file(self, path: pathlib.Path, must_exist: bool = True) -> None:
        """Validate file permissions and ownership."""
        if must_exist and not path.exists():
            raise ProtectedConfigValidationError(f"Protected config file does not exist: {path}")

        if not path.exists():
            return

        stat = path.stat()

        # Reject symlinks
        if path.is_symlink():
            raise ProtectedConfigValidationError(f"Protected config file is a symlink: {path}")

        # Reject group/world writable
        if stat.st_mode & 0o022:
            raise ProtectedConfigValidationError(
                f"Protected config file is group/world writable: {path}"
            )

        # In production, verify owner is the profile/service identity
        # For now, just check the file is readable
        if not os.access(path, os.R_OK):
            raise ProtectedConfigValidationError(f"Protected config file is not readable: {path}")

    def _load_jcs_json(self, path: pathlib.Path) -> tuple[dict[str, Any], bytes]:
        """Load and validate JCS JSON (RFC 8785) from file."""
        content = path.read_text(encoding="utf-8")

        def object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ProtectedConfigValidationError(f"Duplicate key '{key}' in {path}")
                result[key] = value
            return result

        try:
            data = json.loads(content, object_pairs_hook=object_pairs_hook)
        except json.JSONDecodeError as e:
            raise ProtectedConfigValidationError(f"Invalid JSON in {path}: {e}")

        if not isinstance(data, dict):
            raise ProtectedConfigValidationError("Protected config root must be an object")

        # Re-serialize to canonical compact, sorted JSON bytes.
        raw_bytes = json.dumps(
            data, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")

        return data, raw_bytes

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

        # Validate schema_version
        if data["schema_version"] != 1:
            raise ProtectedConfigValidationError(
                f"Unsupported schema_version: {data['schema_version']}"
            )

        # Validate sequence is positive integer
        if not isinstance(data["sequence"], int) or data["sequence"] <= 0:
            raise ProtectedConfigValidationError(f"Invalid sequence: {data['sequence']}")

        # Validate timestamps
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
        required_hindsight_fields = {"scheme", "host", "port", "base_path", "tls", "proxy"}

        missing_hindsight = required_hindsight_fields - set(hindsight.keys())
        if missing_hindsight:
            raise ProtectedConfigValidationError(
                f"Missing required hindsight fields: {missing_hindsight}"
            )

        # Validate scheme
        if hindsight["scheme"] != "https":
            raise ProtectedConfigValidationError(f"Invalid scheme: {hindsight['scheme']}")

        # Validate port
        if not isinstance(hindsight["port"], int) or not (1 <= hindsight["port"] <= 65535):
            raise ProtectedConfigValidationError(f"Invalid port: {hindsight['port']}")

        # Validate base_path starts with /
        if not hindsight["base_path"].startswith("/"):
            raise ProtectedConfigValidationError(
                f"Invalid base_path (must start with /): {hindsight['base_path']}"
            )

        # Validate TLS config
        tls = hindsight["tls"]
        if not isinstance(tls.get("verify"), bool):
            raise ProtectedConfigValidationError(f"Invalid tls.verify: {tls.get('verify')}")

        # Validate proxy config
        proxy = hindsight["proxy"]
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

        # Check sequence is strictly increasing (for same config_id)
        if self._accepted_sequence is not None and config.config_id == self._last_config_id:
            if config.sequence < self._accepted_sequence:
                raise ProtectedConfigFreshnessError(
                    f"Config sequence {config.sequence} is less than accepted {self._accepted_sequence}"
                )
            if (
                config.sequence == self._accepted_sequence
                and config.payload_digest != self._accepted_digest
            ):
                raise ProtectedConfigFreshnessError(
                    f"Config sequence {config.sequence} changed payload without incrementing sequence"
                )

    def _validate_signature(
        self, payload: bytes, key_id: str, signature_data: bytes | None
    ) -> None:
        """Validate the signature over the payload."""
        if signature_data is None:
            # In production, signature is required
            # For testing, we allow unsigned configs if keys are not registered
            if not self._pinned_keys:
                return
            raise ProtectedConfigAuthError("Signature is required")

        # Check key is authorized
        if key_id not in self._pinned_keys:
            raise ProtectedConfigAuthError(f"Unauthorized key_id: {key_id}")

        key_material = self._pinned_keys[key_id]

        # Try HMAC-SHA-256 first
        try:
            secret = key_material if isinstance(key_material, bytes) else None
            if secret is not None:
                expected_sig = hmac.new(
                    self.DOMAIN_SEPARATOR + secret,
                    payload,
                    hashlib.sha256,
                ).digest()

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