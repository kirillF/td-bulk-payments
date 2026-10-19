import pytest
from pydantic import ValidationError

from app.schemas.payments import MAX_CENTS, BulkPayment, Payment

from .conftest import PAYEE, PAYER, payload


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Invoice\x00reference", "Invoicereference"),
        ("\x00\x00", ""),
        ("a\ud800b\udfff", "ab"),
        (" \t😀é\n", " \t😀é\n"),
        ("x" * 2000 + "\x00", "x" * 2000),
    ],
)
def test_description_sanitized_before_validation_and_fingerprint(raw, clean):
    dirty = payload()
    dirty["payments"][0]["description"] = raw
    normalized = payload()
    normalized["payments"][0]["description"] = clean
    model = BulkPayment.model_validate(dirty)
    assert model.payments[0].description == clean
    assert model.fingerprint() == BulkPayment.model_validate(normalized).fingerprint()


@pytest.mark.parametrize("description", [None, 1, False, {}, [], "x" * 2001])
def test_sanitization_preserves_type_and_length_validation(description):
    with pytest.raises(ValidationError):
        Payment(amount="1", payee_firm_uuid=PAYEE, description=description)


@pytest.mark.parametrize(
    ("amount", "cents"), [("300", 30000), ("0.01", 1), ("1.2", 120), ("0001.00", 100)]
)
def test_exact_cents(amount, cents):
    assert Payment(amount=amount, payee_firm_uuid=PAYEE, description="").amount_cents == cents


@pytest.mark.parametrize(
    "amount",
    ["0", "-1", "+1", ".1", "1.", "1.001", "1e2", "NaN", " 1", "1\n", 1, 1.2, True, "２"],
)
def test_invalid_money(amount):
    with pytest.raises(ValidationError):
        Payment(amount=amount, payee_firm_uuid=PAYEE, description="")


def test_integer_limit():
    assert (
        Payment(amount="21474836.47", payee_firm_uuid=PAYEE, description="").amount_cents
        == MAX_CENTS
    )
    with pytest.raises(ValidationError):
        Payment(amount="21474836.48", payee_firm_uuid=PAYEE, description="")


def test_fingerprint_canonical_money_and_uuid():
    first = BulkPayment.model_validate(payload("01.0", payer=PAYER.upper(), payee=PAYEE.upper()))
    second = BulkPayment.model_validate(payload("1.00"))
    assert first.fingerprint() == second.fingerprint()
    changed = payload("1")
    changed["payments"][0]["description"] += " "
    assert first.fingerprint() != BulkPayment.model_validate(changed).fingerprint()


def test_fingerprint_preserves_payment_order():
    body = payload("1")
    body["payments"].append({**body["payments"][0], "amount": "2"})
    first = BulkPayment.model_validate(body)
    body["payments"].reverse()
    assert first.fingerprint() != BulkPayment.model_validate(body).fingerprint()
