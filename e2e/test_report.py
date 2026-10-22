"""An employee reports an incident and follows it."""

from uuid import uuid4

import pytest


@pytest.fixture
def employee(app):
    app.sign_in("employee@acme.inc")
    return app


def test_empty_report_shows_what_is_missing(employee):
    """TC-12: submitting a blank form lists every missing field and creates nothing."""
    app = employee
    app.open("/report")
    app.heading("Report an incident")
    app.button("Submit incident").click()
    for message in ("Title must be 5 to 120 characters.", "Choose a category."):
        app.text_appears(message)
    # No confirmation banner, and the form is still there to fix.
    assert not app.all('section[role="status"]')
    assert app.find("#r-title").is_displayed()


def test_employee_reports_an_incident_and_sees_it(employee):
    """TC-13: a valid report gets a ticket number, opens on its page and is in My tickets."""
    app = employee
    title = f"E2E projector fault {uuid4().hex[:6]}"
    app.open("/report")
    app.type("#r-title", title)
    app.choose_first("#r-cat")
    # Choosing a category fills a template; replace it with real details.
    app.type("#r-desc", "The projector in the meeting room shows no signal from HDMI.")
    app.choose_first("#r-b")
    app.button("Submit incident").click()

    banner = app.find('section[role="status"]')
    app.wait.until(lambda d: " created" in banner.text, "no confirmation banner")
    number = banner.find_element("css selector", "h2").text.split()[0]
    assert number.startswith("INC-")

    app.find('section[role="status"] a.btn.primary').click()  # View ticket
    app.heading(title)
    app.text_appears(number)

    app.open("/")
    app.heading("My tickets")
    app.text_appears(title)
