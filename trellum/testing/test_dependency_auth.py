"""Offline checks of the optional drivers' installed authentication libraries."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest


def test_google_oauth_authorization_code_exchange(monkeypatch):
    flow_module = pytest.importorskip("google_auth_oauthlib.flow")
    import requests

    flow = flow_module.Flow.from_client_config({"web": {
        "client_id": "synthetic-client", "client_secret": "synthetic-secret",
        "auth_uri": "https://identity.example/authorize",
        "token_uri": "https://identity.example/token",
    }}, scopes=["openid"], redirect_uri="https://portal.example/callback")
    url, state = flow.authorization_url(state="synthetic-state")
    assert parse_qs(urlsplit(url).query)["state"] == [state]

    def send(session, request, **kwargs):
        assert request.url == "https://identity.example/token"
        body = parse_qs(request.body)
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["synthetic-code"]
        assert body["redirect_uri"] == ["https://portal.example/callback"]
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response._content = b'{"access_token":"synthetic-token","token_type":"Bearer","expires_in":3600,"refresh_token":"synthetic-refresh"}'
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    flow.fetch_token(code="synthetic-code", timeout=5)
    assert flow.credentials.token == "synthetic-token"
    assert flow.credentials.refresh_token == "synthetic-refresh"


def test_google_service_account_and_asn1_certificate():
    service_account = pytest.importorskip("google.oauth2.service_account")
    pytest.importorskip("rsa")  # optional Google pure-Python crypto backend
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth.crypt import _python_rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    credentials = service_account.Credentials.from_service_account_info({
        "type": "service_account", "private_key": pem,
        "client_email": "synthetic@example.iam.gserviceaccount.com",
        "token_uri": "https://identity.example/token",
    }, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    message = b"synthetic credential compatibility check"
    signature = credentials.sign_bytes(message)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=5)).sign(key, hashes.SHA256()))
    verifier = _python_rsa.RSAVerifier.from_string(cert.public_bytes(serialization.Encoding.PEM))
    assert verifier.verify(message, signature)
    assert not verifier.verify(b"changed", signature)
    signer = _python_rsa.RSASigner.from_string(pem)
    assert verifier.verify(message, signer.sign(message))


def test_databricks_installed_pat_authentication():
    auth = pytest.importorskip("databricks.sql.auth.auth")
    http = Mock()
    provider = auth.get_python_sql_connector_auth_provider(
        "warehouse.example", http, access_token="synthetic-pat",
    )
    headers = {}
    provider.add_headers(headers)
    assert headers["Authorization"] == "Bearer synthetic-pat"
    assert http.mock_calls == []


def test_ldap_installed_mock_directory():
    ldap3 = pytest.importorskip("ldap3")
    server = ldap3.Server("directory.example", get_info=ldap3.NONE)
    connection = ldap3.Connection(server, user="cn=synthetic,dc=example",
                                  password="synthetic-password", client_strategy=ldap3.MOCK_SYNC)
    connection.strategy.add_entry("cn=synthetic,dc=example", {
        "objectClass": ["person"], "sn": "Synthetic", "cn": "synthetic",
        "userPassword": "synthetic-password",
    })
    assert connection.bind()
    assert connection.search("dc=example", "(cn=synthetic)", attributes=["cn"])
    assert connection.entries[0].cn.value == "synthetic"
    connection.unbind()
