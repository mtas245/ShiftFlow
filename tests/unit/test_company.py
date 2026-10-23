import pytest

from app.domain.company import Company, CompanyValidationError


def valid_company(**overrides):
    values = {
        "company_name": "North Star Cafe",
        "business_email": "  SCHEDULE@EXAMPLE.COM  ",
        "business_phone": "+41791234567",
        "business_address": "1 Main Street",
        "industry": "  Food   and\tHospitality  ",
        "joining_code": "AB12CD34",
    }
    values.update(overrides)
    return Company.create(**values)


def test_company_normalizes_email_and_industry():
    company = valid_company()

    assert company.business_email == "SCHEDULE@example.com"
    assert company.industry == "Food and Hospitality"


@pytest.mark.parametrize(
    "business_email",
    [
        "missing-at.example.com",
        "local@@example.com",
        "@example.com",
        "local@",
        "local part@example.com",
        "local@example..com",
        "local@-example.com",
        "local@example-.com",
    ],
)
def test_company_rejects_invalid_business_email(business_email):
    with pytest.raises(CompanyValidationError):
        valid_company(business_email=business_email)


@pytest.mark.parametrize(
    "business_phone",
    [
        "41791234567",
        "+4179 1234567",
        "+4179-1234567",
        "+0123456789",
        "+1",
        "+1234567890123456",
    ],
)
def test_company_rejects_phone_numbers_outside_e164(business_phone):
    with pytest.raises(CompanyValidationError):
        valid_company(business_phone=business_phone)


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_name", "  "),
        ("business_address", "\t "),
        ("industry", " \t "),
        ("joining_code", "abc12345"),
        ("joining_code", "AB12CD3"),
        ("joining_code", "AB12-CD4"),
    ],
)
def test_company_rejects_empty_required_text_or_invalid_joining_code(field, value):
    with pytest.raises(CompanyValidationError):
        valid_company(**{field: value})


def test_company_accepts_e164_phone_and_uppercase_joining_code():
    company = valid_company(
        business_phone="+41791234567",
        joining_code="A1B2C3D4",
    )

    assert company.business_phone == "+41791234567"
    assert company.joining_code == "A1B2C3D4"
