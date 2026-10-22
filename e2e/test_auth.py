"""Sign-in, sign-out and who can open which pages."""

import pytest

from conftest import PASSWORD


def test_signed_out_visitor_is_sent_to_login(app):
    """TC-01: a protected page redirects to the sign-in page."""
    app.open("/incidents")
    app.wait_path("/login")
    app.button("Sign in")


def test_wrong_password_shows_an_error_and_stays_signed_out(app):
    """TC-02: bad credentials keep the visitor on the sign-in page with a clear message."""
    app.sign_in("nobody.e2e@acme.inc", "Wrong-Password-123", expect_path=None)
    app.text_appears("Email or password is incorrect")
    assert app.path.startswith("/login")


def test_employee_signs_in_to_my_tickets_without_admin_menu(app):
    """TC-03: an employee lands on My tickets and sees no admin navigation."""
    app.sign_in("employee@acme.inc")
    app.heading("My tickets")
    nav = app.find("nav").text
    assert "Report an incident" in nav
    for admin_only in ("Workload", "Overview", "Users"):
        assert admin_only not in nav


def test_admin_sees_admin_menu(app):
    """TC-04: an admin gets the admin pages in the navigation."""
    app.sign_in("admin@acme.inc")
    nav = app.find("nav").text
    for item in ("Triage", "Workload", "Overview", "Users"):
        assert item in nav


def test_sign_out_ends_the_session(app):
    """TC-05: after signing out, protected pages need a new sign-in, even after reload."""
    app.sign_in("employee@acme.inc")
    app.sign_out()
    app.open("/")
    app.wait_path("/login")


@pytest.mark.parametrize("path", ["/overview", "/workload", "/admin/users"])
def test_employee_cannot_open_admin_pages(app, path):
    """TC-06: typing an admin URL as an employee goes back to My tickets."""
    app.sign_in("employee@acme.inc")
    app.open(path)
    app.wait_path("/")
    app.heading("My tickets")


def test_sign_in_returns_to_the_page_asked_for(app):
    """TC-07: a deep link survives the sign-in detour."""
    app.open("/report")
    app.wait_path("/login")
    app.type("#login-email", "employee@acme.inc")
    app.type("#login-pass", PASSWORD)
    app.button("Sign in").click()
    app.wait_path("/report")
    app.heading("Report an incident")
