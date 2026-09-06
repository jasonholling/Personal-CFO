"""
Optional lock for the frontend — not internet-facing security (the app is
still bound to 127.0.0.1 only, see CLAUDE.md), just a "someone else picks
up this Mac" deterrent for a household member or a screen-share glance.

Two ways in:
  - Touch ID via WebAuthn (platform authenticator) — the normal path once
    registered.
  - A passphrase — the fallback, and the only way in before Touch ID is
    registered (registration itself requires being logged in already, so
    an unauthenticated attacker can't just register their own fingerprint).

First run requires going through setup (see /api/auth/setup in main.py):
either pick a passphrase, which is persisted to backend/.env, or explicitly
skip, which is recorded in the gitignored .auth_disabled marker. Until one
of those happens, all /api routes are blocked. Auth is only OFF (every
request allowed) after an explicit skip — never by default on a fresh
clone.

Session tokens and the WebAuthn challenge live in memory only — they reset
on backend restart. The session cookie itself is a browser session cookie
(no expiry), so "ask again" happens on either a fresh browser session or a
backend restart, whichever comes first. The one thing that IS persisted to
disk is the registered Touch ID credential (public key only — never
biometric data, which never leaves the device's secure enclave), in a
small local JSON file, gitignored, separate from cfo.db.
"""
import os
import json
import secrets
import hashlib
import hmac
from typing import Optional

from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
)
from webauthn.helpers import options_to_json, bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, AuthenticatorAttachment,
    UserVerificationRequirement, ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
)

_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
_CREDENTIAL_PATH = os.path.join(os.path.dirname(__file__), ".webauthn_credential.json")
_AUTH_DISABLED_PATH = os.path.join(os.path.dirname(__file__), ".auth_disabled")

RP_ID   = "localhost"
RP_NAME = "Personal CFO"
ORIGIN  = "http://localhost:5173"


def _load_env_file():
    """Minimal .env loader (KEY=VALUE per line, '#' comments, blank lines
    ignored) — avoids adding python-dotenv as a dependency for one value.
    Does not override an already-set environment variable."""
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()

# APP_PASSPHRASE is supported only to avoid locking out an existing local
# install. New passphrases are stored as a salted PBKDF2-HMAC-SHA256 hash
# instead. (Originally scrypt via hashlib.scrypt, but that function is only
# present when Python's OpenSSL build includes scrypt support — Apple's
# bundled Python links LibreSSL instead, which doesn't, so `set_passphrase`
# raised AttributeError on this exact machine. pbkdf2_hmac has no such
# dependency: it's implemented in hashlib unconditionally on every
# platform. Iteration count follows OWASP's 2023 PBKDF2-SHA256 guidance.)
PASSPHRASE = os.environ.get("APP_PASSPHRASE", "").strip() or None
PASSPHRASE_HASH = os.environ.get("APP_PASSPHRASE_HASH", "").strip() or None
_LEGACY_PASSPHRASE_LOADED = bool(PASSPHRASE and not PASSPHRASE_HASH)
_PBKDF2_ITERATIONS = 600_000


def auth_enabled() -> bool:
    return bool(PASSPHRASE_HASH or PASSPHRASE)


def legacy_passphrase_needs_migration() -> bool:
    return _LEGACY_PASSPHRASE_LOADED and bool(PASSPHRASE) and not PASSPHRASE_HASH


def _hash_passphrase(passphrase: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_passphrase(passphrase: str) -> bool:
    """Constant-time verification of a new hash or a legacy local value."""
    if PASSPHRASE_HASH:
        try:
            algorithm, iterations, salt_hex, expected_hex = PASSPHRASE_HASH.split("$")
            if algorithm != "pbkdf2_sha256":
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations),
            ).hex()
            return hmac.compare_digest(actual, expected_hex)
        except (ValueError, TypeError):
            return False
    return bool(PASSPHRASE) and hmac.compare_digest(passphrase, PASSPHRASE)


def auth_disabled_explicitly() -> bool:
    """True once the user has clicked "skip" on first-run setup — the only
    way auth can be off going forward. Distinct from PASSPHRASE being unset
    on a brand-new clone, which instead means setup hasn't happened yet."""
    return os.path.exists(_AUTH_DISABLED_PATH)


def setup_required() -> bool:
    """First run: no passphrase configured and the user hasn't explicitly
    opted out. Blocks all /api access (see main.py's auth_middleware) until
    one of set_passphrase()/disable_auth() below is called."""
    return not auth_enabled() and not auth_disabled_explicitly()


def set_passphrase(passphrase: str):
    """Persist a salted passphrase hash in backend/.env and activate it."""
    global PASSPHRASE, PASSPHRASE_HASH
    passphrase = passphrase.strip()
    if not passphrase:
        raise ValueError("Passphrase cannot be empty")
    lines = []
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.startswith("APP_PASSPHRASE=")
                     and not l.startswith("APP_PASSPHRASE_HASH=")]
    PASSPHRASE_HASH = _hash_passphrase(passphrase)
    lines.append(f"APP_PASSPHRASE_HASH={PASSPHRASE_HASH}")
    with open(_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.environ.pop("APP_PASSPHRASE", None)
    os.environ["APP_PASSPHRASE_HASH"] = PASSPHRASE_HASH
    PASSPHRASE = None
    # Clear the disabled marker, if any, now that a real passphrase exists.
    if os.path.exists(_AUTH_DISABLED_PATH):
        os.remove(_AUTH_DISABLED_PATH)


def disable_auth_explicitly():
    """Records that the user saw first-run setup and chose to skip it."""
    with open(_AUTH_DISABLED_PATH, "w") as f:
        f.write("skipped at first run\n")


# ── Sessions ─────────────────────────────────────────────────────────────
_valid_sessions = set()


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _valid_sessions.add(token)
    return token


def is_valid_session(token) -> bool:
    return bool(token) and token in _valid_sessions


def invalidate_session(token):
    _valid_sessions.discard(token)


# ── Touch ID (WebAuthn) ──────────────────────────────────────────────────
_pending_registration_challenge = None
_pending_authentication_challenge = None


def _load_credential():
    if not os.path.exists(_CREDENTIAL_PATH):
        return None
    with open(_CREDENTIAL_PATH) as f:
        return json.load(f)


def _save_credential(credential_id: bytes, public_key: bytes, sign_count: int):
    with open(_CREDENTIAL_PATH, "w") as f:
        json.dump({
            "credential_id": bytes_to_base64url(credential_id),
            "public_key":    bytes_to_base64url(public_key),
            "sign_count":    sign_count,
        }, f)


def webauthn_registered() -> bool:
    return _load_credential() is not None


def start_registration() -> str:
    """Returns JSON options for navigator.credentials.create()."""
    global _pending_registration_challenge
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_name="jason",
        user_display_name="Personal CFO",
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    _pending_registration_challenge = options.challenge
    return options_to_json(options)


def verify_registration(credential_json: str) -> bool:
    global _pending_registration_challenge
    if _pending_registration_challenge is None:
        return False
    try:
        verification = verify_registration_response(
            credential=credential_json,
            expected_challenge=_pending_registration_challenge,
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
        )
    except Exception:
        return False
    finally:
        _pending_registration_challenge = None
    _save_credential(verification.credential_id, verification.credential_public_key, verification.sign_count)
    return True


def start_authentication() -> str:
    """Returns JSON options for navigator.credentials.get()."""
    global _pending_authentication_challenge
    stored = _load_credential()
    if not stored:
        return None
    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(stored["credential_id"]))],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _pending_authentication_challenge = options.challenge
    return options_to_json(options)


def verify_authentication(credential_json: str) -> bool:
    global _pending_authentication_challenge
    stored = _load_credential()
    if not stored or _pending_authentication_challenge is None:
        return False
    try:
        verification = verify_authentication_response(
            credential=credential_json,
            expected_challenge=_pending_authentication_challenge,
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            credential_public_key=base64url_to_bytes(stored["public_key"]),
            credential_current_sign_count=stored["sign_count"],
        )
    except Exception:
        return False
    finally:
        _pending_authentication_challenge = None
    _save_credential(base64url_to_bytes(stored["credential_id"]), base64url_to_bytes(stored["public_key"]), verification.new_sign_count)
    return True
