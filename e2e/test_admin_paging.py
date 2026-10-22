"""Admin lists page through their items: 10 per page, or 20 for the team table."""

import re

import pytest
from selenium.webdriver.common.by import By

RANGE = re.compile(r"(\d+)–(\d+) of (\d+)")


@pytest.fixture
def admin(app):
    app.sign_in("admin@acme.inc")
    return app


def section(app, heading_id):
    return app.find(f'section[aria-labelledby="{heading_id}"]')


def pager_text(app, box):
    """The "first–last of total" text, or None when the list fits on one page."""
    feet = box.find_elements(By.CSS_SELECTOR, ".table-foot span")
    return feet[0].text if feet else None


def wait_rows(app, box, css):
    """Rows once the list has loaded (it may briefly be empty)."""
    app.wait.until(lambda d: box.find_elements(By.CSS_SELECTOR, css) or "Nothing" in box.text
                   or "Every open ticket" in box.text or "No active" in box.text, "list never loaded")
    return box.find_elements(By.CSS_SELECTOR, css)


def check_paging(app, box, row_css, size):
    """Walk every page: each full page shows `size` rows and the last shows the rest."""
    rows = wait_rows(app, box, row_css)
    text = pager_text(app, box)
    if text is None:
        assert len(rows) <= size, f"{len(rows)} rows but no pager"
        return len(rows)
    total = int(RANGE.search(text).group(3))
    assert total > size
    pages = -(-total // size)
    previous = app.button("Previous", within=box)
    assert not previous.is_enabled()
    for page in range(1, pages + 1):
        first, last = (page - 1) * size + 1, min(page * size, total)
        expected = f"{first}–{last} of {total}"
        app.wait.until(lambda d, want=expected: pager_text(app, box) == want, f"expected {expected}")
        assert len(box.find_elements(By.CSS_SELECTOR, row_css)) == last - first + 1
        next_button = app.button("Next", within=box)
        if page < pages:
            next_button.click()
        else:
            assert not next_button.is_enabled()
    return total


def test_overview_needs_a_decision_shows_10_at_a_time(admin):
    """TC-14: "Needs a decision" pages 10 items at a time."""
    admin.open("/overview")
    admin.heading("Facility overview")
    box = section(admin, "attn-h")
    check_paging(admin, box, "a[href^='/incidents/']", 10)


def test_overview_period_change_goes_back_to_page_1(admin):
    """TC-15: picking another period restarts "Needs a decision" at page 1."""
    admin.open("/overview")
    box = section(admin, "attn-h")
    wait_rows(admin, box, "a[href^='/incidents/']")
    if pager_text(admin, box) is None:
        pytest.skip("Needs a decision fits on one page with this data")
    admin.button("Next", within=box).click()
    admin.wait.until(lambda d: pager_text(admin, box).startswith("11–"))
    admin.button("Last 7 days").click()
    admin.wait.until(lambda d: (pager_text(admin, section(admin, "attn-h")) or "1–").startswith("1–"))


def test_workload_waiting_for_an_engineer_shows_10_at_a_time(admin):
    """TC-16: the unassigned queue pages 10 at a time (the showcase has more than 10)."""
    admin.open("/workload")
    admin.heading("Workload")
    box = section(admin, "un-h")
    total = check_paging(admin, box, ".list-row", 10)
    assert total > 10, "showcase data should have more than 10 waiting tickets"
    # The header count is the full total, not one page.
    assert f"{total} tickets" in box.find_element(By.CSS_SELECTOR, ".card-head").text


def test_workload_blocked_shows_10_at_a_time(admin):
    """TC-17: the Blocked list pages 10 at a time."""
    admin.open("/workload")
    check_paging(admin, section(admin, "blk-h"), ".list-row", 10)


def test_workload_team_tickets_show_20_at_a_time(admin):
    """TC-18: "All team tickets" pages 20 rows at a time."""
    admin.open("/workload")
    check_paging(admin, section(admin, "tt-h"), "tbody tr", 20)


def test_workload_picking_an_engineer_restarts_at_page_1(admin):
    """TC-19: filtering to one engineer, and back, always starts at page 1."""
    admin.open("/workload")
    box = section(admin, "tt-h")
    wait_rows(admin, box, "tbody tr")
    if pager_text(admin, box):
        admin.button("Next", within=box).click()
        admin.wait.until(lambda d: pager_text(admin, box).startswith("21–"))
    busiest = max(admin.all("button.eng"), key=lambda b: int(b.find_element(By.CSS_SELECTOR, ".big").text))
    name = busiest.find_element(By.CSS_SELECTOR, "span span").text
    busiest.click()
    admin.wait.until(lambda d: f"{name}’s tickets" in section(admin, "tt-h").text)
    text = pager_text(admin, section(admin, "tt-h"))
    assert text is None or text.startswith("1–")
    admin.button("Show whole team").click()
    admin.wait.until(lambda d: "All team tickets" in section(admin, "tt-h").text)
    text = pager_text(admin, section(admin, "tt-h"))
    assert text is None or text.startswith("1–")
