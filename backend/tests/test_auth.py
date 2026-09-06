"""Tests for auth.py — session management and the parts of the WebAuthn
flow that don't require a real browser/authenticator ceremony. The actual
cryptographic verify_registration_response/verify_authentication_response
success paths are exercised indirectly by test_main.py's failure-path
tests (a fabricated credential can't pass real signature verification —
that's the point), plus these directly test everything session/credential-
storage related without needing a real authenticator."""
import json
import pytest

import auth


class TestSessions:
    def test_create_session_is_valid(self):
        token = auth.create_session()
        assert auth.is_valid_session(token) is True

    def test_unknown_token_is_invalid(self):
        assert auth.is_valid_session("not-a-real-token") is False

    def test_none_token_is_invalid(self):
        assert auth.is_valid_session(None) is False

    def test_empty_token_is_invalid(self):
        assert auth.is_valid_session("") is False

    def test_invalidate_session_removes_it(self):
        token = auth.create_session()
        auth.invalidate_session(token)
        assert auth.is_valid_session(token) is False

    def test_invalidating_unknown_token_does_not_raise(self):
        auth.invalidate_session("never-existed")  # should not raise

    def test_sessions_are_unique(self):
        tokens = {auth.create_session() for _ in range(20)}
        assert len(tokens) == 20


class TestFirstRunSetup:
    """Regression tests (external audit 2026-09-05): auth used to be a
    silent no-op on a fresh clone whenever APP_PASSPHRASE wasn't set, with
    no prompt telling the user that was the state they were in. Now a
    fresh clone requires going through setup_required() explicitly."""

    def test_setup_required_when_no_passphrase_and_not_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "not_there"))
        assert auth.setup_required() is True

    def test_setup_not_required_once_passphrase_set(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "not_there"))
        assert auth.setup_required() is False

    def test_setup_not_required_after_explicit_disable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "marker"))
        auth.disable_auth_explicitly()
        assert auth.setup_required() is False

    def test_set_passphrase_persists_to_env_file_and_takes_effect(self, monkeypatch, tmp_path):
        env_path = tmp_path / "test.env"
        monkeypatch.setattr(auth, "_ENV_PATH", str(env_path))
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "marker"))
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "PASSPHRASE_HASH", None)
        auth.set_passphrase("new-secret")
        assert auth.PASSPHRASE is None
        saved = env_path.read_text()
        assert "APP_PASSPHRASE_HASH=pbkdf2_sha256$" in saved
        assert "new-secret" not in saved
        assert auth.verify_passphrase("new-secret") is True
        assert auth.verify_passphrase("wrong-secret") is False
        assert auth.setup_required() is False

    def test_set_passphrase_clears_prior_disabled_marker(self, monkeypatch, tmp_path):
        marker = tmp_path / "marker"
        monkeypatch.setattr(auth, "_ENV_PATH", str(tmp_path / "test.env"))
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(marker))
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "PASSPHRASE_HASH", None)
        auth.disable_auth_explicitly()
        assert marker.exists()
        auth.set_passphrase("new-secret")
        assert not marker.exists()

    def test_set_passphrase_rejects_empty_string(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "_ENV_PATH", str(tmp_path / "test.env"))
        with pytest.raises(ValueError):
            auth.set_passphrase("   ")

    def test_legacy_passphrase_is_marked_for_migration(self, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "legacy-secret")
        monkeypatch.setattr(auth, "PASSPHRASE_HASH", None)
        monkeypatch.setattr(auth, "_LEGACY_PASSPHRASE_LOADED", True)
        assert auth.legacy_passphrase_needs_migration() is True


class TestEnvFileLoading:
    def test_load_env_file_missing_is_a_noop(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "_ENV_PATH", str(tmp_path / "does_not_exist.env"))
        auth._load_env_file()  # should not raise

    def test_load_env_file_parses_and_skips_comments(self, monkeypatch, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("# a comment\n\nSOME_TEST_KEY=hello\nMALFORMED_LINE\n")
        monkeypatch.setattr(auth, "_ENV_PATH", str(env_file))
        monkeypatch.delenv("SOME_TEST_KEY", raising=False)
        auth._load_env_file()
        import os
        assert os.environ.get("SOME_TEST_KEY") == "hello"

    def test_load_env_file_does_not_override_existing(self, monkeypatch, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("SOME_TEST_KEY2=from_file\n")
        monkeypatch.setattr(auth, "_ENV_PATH", str(env_file))
        monkeypatch.setenv("SOME_TEST_KEY2", "already_set")
        auth._load_env_file()
        import os
        assert os.environ.get("SOME_TEST_KEY2") == "already_set"


class TestWebAuthnCredentialStorage:
    def test_not_registered_when_no_credential_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "_CREDENTIAL_PATH", str(tmp_path / "nope.json"))
        assert auth.webauthn_registered() is False

    def test_registered_after_saving_credential(self, monkeypatch, tmp_path):
        cred_path = str(tmp_path / "cred.json")
        monkeypatch.setattr(auth, "_CREDENTIAL_PATH", cred_path)
        auth._save_credential(b"credential-id-bytes", b"public-key-bytes", 0)
        assert auth.webauthn_registered() is True

    def test_saved_credential_round_trips(self, monkeypatch, tmp_path):
        cred_path = str(tmp_path / "cred.json")
        monkeypatch.setattr(auth, "_CREDENTIAL_PATH", cred_path)
        auth._save_credential(b"abc123", b"pubkeybytes", 5)
        loaded = auth._load_credential()
        assert loaded["sign_count"] == 5
        assert isinstance(loaded["credential_id"], str)
        assert isinstance(loaded["public_key"], str)


class TestAuthenticationFlow:
    def test_start_authentication_none_when_not_registered(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "_CREDENTIAL_PATH", str(tmp_path / "nope.json"))
        assert auth.start_authentication() is None

    def test_verify_authentication_false_without_pending_challenge(self, monkeypatch, tmp_path):
        cred_path = str(tmp_path / "cred.json")
        monkeypatch.setattr(auth, "_CREDENTIAL_PATH", cred_path)
        auth._save_credential(b"abc", b"key", 0)
        monkeypatch.setattr(auth, "_pending_authentication_challenge", None)
        assert auth.verify_authentication(json.dumps({"fake": "credential"})) is False

    def test_verify_authentication_false_with_garbage_credential(self, monkeypatch, tmp_path):
        cred_path = str(tmp_path / "cred.json")
        monkeypatch.setattr(auth, "_CREDENTIAL_PATH", cred_path)
        auth._save_credential(b"abc", b"key", 0)
        monkeypatch.setattr(auth, "_pending_authentication_challenge", b"some-challenge-bytes")
        assert auth.verify_authentication(json.dumps({"not": "a real webauthn credential"})) is False

    def test_verify_registration_false_without_pending_challenge(self, monkeypatch):
        monkeypatch.setattr(auth, "_pending_registration_challenge", None)
        assert auth.verify_registration(json.dumps({"fake": "credential"})) is False

    def test_verify_registration_false_with_garbage_credential(self, monkeypatch):
        monkeypatch.setattr(auth, "_pending_registration_challenge", b"some-challenge-bytes")
        assert auth.verify_registration(json.dumps({"not": "a real webauthn credential"})) is False

    def test_start_registration_returns_valid_json_with_challenge(self):
        options_json = auth.start_registration()
        options = json.loads(options_json)
        assert "challenge" in options
        assert options["rp"]["id"] == auth.RP_ID
