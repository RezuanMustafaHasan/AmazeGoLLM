import json
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from firebase_admin import credentials

from backend.app.config import Settings
from backend.app.repository import FirestoreRepository


@pytest.fixture
def service_account_info():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "test-key-id",
        "private_key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_service_account_environment_initializes_sdk_without_a_credential_file(
    monkeypatch, service_account_info
):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", json.dumps(service_account_info))
    settings = Settings(
        _env_file=None,
        firebase_project_id="test-project",
        google_application_credentials=None,
        firestore_emulator_host=None,
    )
    database = MagicMock()
    monkeypatch.setattr("backend.app.repository.firestore.client", lambda *, app: database)

    repository = FirestoreRepository(settings)
    try:
        credential = repository.app.credential
        assert isinstance(credential, credentials.Certificate)
        assert credential.get_credential().service_account_email == service_account_info[
            "client_email"
        ]
        assert repository.app.project_id == "test-project"
        assert service_account_info["private_key"] not in repr(settings)
        assert service_account_info["private_key"] not in settings.model_dump_json()
        database.collection.assert_called_once_with("amaze_go")
    finally:
        repository.close()


def test_omitting_service_account_json_preserves_application_default_credentials(monkeypatch):
    settings = Settings(
        _env_file=None,
        firebase_project_id="test-project",
        google_application_credentials=None,
        firestore_emulator_host=None,
        firebase_service_account_json=None,
    )
    monkeypatch.setattr("backend.app.repository.firestore.client", lambda *, app: MagicMock())

    repository = FirestoreRepository(settings)
    try:
        assert isinstance(repository.app.credential, credentials.ApplicationDefault)
    finally:
        repository.close()
