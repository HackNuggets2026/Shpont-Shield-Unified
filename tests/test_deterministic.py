"""Deterministic (non-AI) content controls: PII and secrets. Positive = allowed, negative = redacted/blocked."""

import pytest

from .conftest import guard

ALLOWED = [
    "Summarise the Q3 roadmap for the platform team.",
    "Order number 1234 5678 9012 3456 shipped",  # 16 digits, fails Luhn: not a card
    "PL00 1234 5678 9012 3456 7890 1234 is a placeholder",  # fails IBAN checksum
    "Version 12345678901 of the build",  # 11 digits, fails PESEL checksum
    "The password policy requires 12 characters.",  # mentions passwords, assigns none
]


@pytest.mark.parametrize("text", ALLOWED)
def test_benign_text_passes_untouched(client, text):
    body = guard(client, text).json()
    assert body["action"] == "allow", body
    assert body["text"] == text


REDACTED = [
    ("Charge 4111 1111 1111 1111 please", "4111 1111 1111 1111", "credit_card"),
    ("Wire to PL61 1090 1014 0000 0712 1981 2874 today", "PL61 1090 1014 0000 0712 1981 2874", "iban"),
    ("Patient PESEL 44051401359 needs a visit", "44051401359", "pesel"),
    ("SSN on file: 123-45-6789", "123-45-6789", "us_ssn"),
    ("db: postgres://admin:hunter22@db.internal:5432/prod", "hunter22", "credentials_in_url"),
    ("config: password = Sup3rS3cret!", "Sup3rS3cret!", "password_assignment"),
]


@pytest.mark.parametrize("text,secret,entity", REDACTED)
def test_sensitive_values_are_redacted(client, text, secret, entity):
    body = guard(client, text).json()
    assert body["action"] == "redact", body
    assert secret not in body["text"]
    assert f"[REDACTED:{entity}]" in body["text"]


BLOCKED = [
    ("deploy with AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
    ("token ghp_" + "a" * 36, "github_token"),
    ("use sk-proj-abcdefghijklmnopqrstuvwx", "openai_key"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", "private_key"),
    ("slack xoxb-1234567890-abcdefghij", "slack_token"),
]


@pytest.mark.parametrize("text,entity", BLOCKED)
def test_secrets_are_blocked(client, text, entity):
    r = guard(client, text)
    assert r.status_code == 403
    body = r.json()
    assert body["action"] == "block"
    assert any(f["control"] == "secrets" and f["category"] == entity for f in body["findings"])
    assert body["text"] == ""


def test_email_is_logged_not_redacted(client):
    text = "Ping jan.kowalski@example.com about it"
    body = guard(client, text).json()
    assert body["action"] == "log"
    assert body["text"] == text
    assert [f["category"] for f in body["findings"]] == ["email"]


def test_overlapping_detectors_report_the_higher_priority_entity_once(client):
    # The IBAN's digits also pass Luhn as a card number; the IBAN claims the span first.
    body = guard(client, "Wire to PL61 1090 1014 0000 0712 1981 2874").json()
    assert [f["category"] for f in body["findings"]] == ["iban"]


def test_redact_merges_overlapping_spans():
    from controllayer.controls.patterns import redact
    from controllayer.types import Span

    assert redact("abcdefgh", [Span(1, 4, "x"), Span(3, 6, "y")]) == "a[REDACTED:x]gh"


def test_pii_in_model_output_is_redacted(client):
    body = guard(client, "Customer card: 5500 0000 0000 0004", direction="output").json()
    assert body["action"] == "redact"
    assert "5500" not in body["text"]
