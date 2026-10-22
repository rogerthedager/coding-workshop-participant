"""Self-service sign-up, including the proper-name rule."""

from uuid import uuid4

import pytest

PASSWORD = "Signup-Passphrase-42"


def fill_signup(app, name, email, password=PASSWORD, confirm=None):
    app.open("/register")
    app.type("#reg-name", name)
    app.type("#reg-email", email)
    app.type("#reg-pass", password)
    app.type("#reg-confirm", confirm if confirm is not None else password)
    app.button("Create account").click()


@pytest.mark.parametrize("name", ["12345", "John123", "user_1"])
def test_numeric_names_are_refused_on_the_form(app, name):
    """TC-08: names with digits or symbols are stopped before the account is created."""
    fill_signup(app, name, f"e2e.{uuid4().hex[:8]}@acme.inc")
    app.text_appears("Use letters only")
    assert "Account created" not in app.driver.page_source


def test_mismatched_passwords_are_refused(app):
    """TC-09: the confirm field must match."""
    fill_signup(app, "Casey Morgan", f"e2e.{uuid4().hex[:8]}@acme.inc", confirm="Different-Pass-99")
    app.text_appears("Passwords don’t match.")


def test_new_employee_signs_up_and_signs_in(app):
    """TC-10: a proper name creates the account; the new person can sign in at once."""
    email = f"e2e.{uuid4().hex[:8]}@acme.inc"
    fill_signup(app, "  Anne-Marie   O'Neil ", email)
    app.text_appears("Account created")
    app.find('a[href="/login"].btn').click()
    app.sign_in(email, PASSWORD)
    app.heading("My tickets")
    # The stored name is tidied: extra spaces removed.
    app.text_appears("Anne-Marie O'Neil")


def test_existing_email_is_refused(app):
    """TC-11: signing up twice with one email points the person to sign in."""
    fill_signup(app, "Jamie Chen", "employee@acme.inc")
    app.text_appears("An account with this email already exists")
