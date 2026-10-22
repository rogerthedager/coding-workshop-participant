"""People's names must be real names: letters, not numbers or symbols."""

import pytest

from facility.security import validate_full_name

PASSWORD = "Valid-Passphrase-734!"

GOOD = [
    ("Jamie Chen", "Jamie Chen"),
    ("  Mary   Jane ", "Mary Jane"),
    ("Anne-Marie O'Neil", "Anne-Marie O'Neil"),
    ("D’Angelo Ruiz", "D’Angelo Ruiz"),
    ("Dr. J. R. Smith", "Dr. J. R. Smith"),
    ("José Álvarez", "José Álvarez"),
    ("李小龙", "李小龙"),
    ("Ab", "Ab"),
]
BAD = ["12345", "John123", "J0hn Smith", "user_1", "R2D2", "Mr 5", "Bob!", "a@b", "A", "-Bob", "   "]


@pytest.mark.parametrize(("raw", "stored"), GOOD)
def test_real_names_are_accepted_and_tidied(raw, stored):
    assert validate_full_name(raw) == stored


@pytest.mark.parametrize("raw", BAD)
def test_numbers_symbols_and_too_short_names_are_rejected(raw):
    with pytest.raises(ValueError):
        validate_full_name(raw)


@pytest.mark.integration
async def test_sign_up_rejects_numeric_names(client):
    """Sign-up refuses digits with a readable reason and stores tidy names."""
    for index, name in enumerate(["12345", "John123", "user_1"]):
        result = await client.post(
            "/api/v1/auth/register",
            json={"email": f"bad{index}@acme.inc", "full_name": name, "password": PASSWORD},
        )
        assert result.status_code == 422, name
        detail = result.json()["error"]["details"][0]
        assert detail["location"][-1] == "full_name"
        assert "letters only" in detail["message"]

    created = await client.post(
        "/api/v1/auth/register",
        json={"email": "mary.jane@acme.inc", "full_name": "  Mary   Jane ", "password": PASSWORD},
    )
    assert created.status_code == 201, created.text
    assert created.json()["full_name"] == "Mary Jane"


@pytest.mark.integration
async def test_profile_and_admin_edits_use_the_same_rule(
    client, employee, admin, auth_headers
):
    """Renaming yourself, or an admin renaming a user, cannot set a numeric name."""
    mine = await auth_headers(employee)
    assert (
        await client.patch("/api/v1/users/me", headers=mine, json={"full_name": "Jamie 2"})
    ).status_code == 422
    manager = await auth_headers(admin)
    assert (
        await client.patch(
            f"/api/v1/users/{employee.id}", headers=manager, json={"full_name": "0000"}
        )
    ).status_code == 422
    renamed = await client.patch(
        f"/api/v1/users/{employee.id}", headers=manager, json={"full_name": "Jamie O'Brien"}
    )
    assert renamed.status_code == 200 and renamed.json()["full_name"] == "Jamie O'Brien"
