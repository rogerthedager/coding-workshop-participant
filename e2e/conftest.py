"""Selenium end-to-end fixtures: a real backend, the real frontend, and Firefox.

By default the suite starts its own stack (fresh database with the showcase seed,
API on :8029, Vite on :3029) and removes it afterwards. Environment options:

    E2E_BASE_URL=https://...   test an already running site instead (no stack started)
    E2E_PASSWORD=...           password of the seeded demo accounts
    E2E_HEADED=1               show the browser window
    E2E_KEEP=1                 keep the e2e database after the run
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PYTHON = ROOT / "backend" / "facility-service" / ".venv" / "bin" / "python"
ARTIFACTS = Path(__file__).parent / "artifacts"
API_PORT, WEB_PORT = 8029, 3029
PASSWORD = os.environ.get("E2E_PASSWORD", "E2e-Passphrase-2026")
WAIT = 15


def wait_for_url(url: str, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except Exception:  # noqa: BLE001 - still starting
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not start within {seconds}s")


def stop(process: subprocess.Popen) -> None:
    """Stop a server and any children it started (each runs in its own group)."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.fixture(scope="session")
def base_url():
    """The site under test; starts and stops a private stack unless E2E_BASE_URL is set."""
    external = os.environ.get("E2E_BASE_URL")
    if external:
        yield external.rstrip("/")
        return
    stack = [str(BACKEND_PYTHON), str(Path(__file__).with_name("stack.py"))]
    subprocess.run([*stack, "create"], check=True, timeout=600)
    logs = ARTIFACTS / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    processes = []
    log_files = [open(logs / "api.log", "w"), open(logs / "web.log", "w")]  # noqa: SIM115
    try:
        processes.append(subprocess.Popen(
            [*stack, "serve", str(API_PORT)], stdout=log_files[0], stderr=subprocess.STDOUT,
            start_new_session=True,
        ))
        wait_for_url(f"http://127.0.0.1:{API_PORT}/api/health")
        # Empty VITE_* values override frontend/.env.local, so /api goes through the
        # Vite proxy to this stack's API (same origin, like the dev setup).
        web_env = dict(os.environ, FACILITY_API_TARGET=f"http://localhost:{API_PORT}",
                       VITE_API_URL="", VITE_API_ENDPOINTS="", VITE_LAMBDA_URLS="")
        processes.append(subprocess.Popen(
            ["npx", "vite", "--port", str(WEB_PORT), "--strictPort", "--host", "localhost"],
            cwd=ROOT / "frontend", env=web_env, stdout=log_files[1], stderr=subprocess.STDOUT,
            start_new_session=True,  # npx starts Vite as a child; stop the whole group
        ))
        wait_for_url(f"http://localhost:{WEB_PORT}/login")
        yield f"http://localhost:{WEB_PORT}"
    finally:
        for process in reversed(processes):
            stop(process)
        for log in log_files:
            log.close()
        if not os.environ.get("E2E_KEEP"):
            subprocess.run([*stack, "drop"], check=False, timeout=60)


@pytest.fixture(scope="session")
def driver():
    options = Options()
    if not os.environ.get("E2E_HEADED"):
        options.add_argument("-headless")
    geckodriver = "/snap/bin/geckodriver"
    service = Service(geckodriver) if os.path.exists(geckodriver) else Service()
    browser = webdriver.Firefox(options=options, service=service)
    browser.set_window_size(1440, 1000)
    yield browser
    browser.quit()


class App:
    """Small page helper: navigation, waits and the actions most tests share."""

    def __init__(self, driver, base_url):
        self.driver = driver
        self.base_url = base_url
        self.wait = WebDriverWait(driver, WAIT)

    # -- navigation and lookup -------------------------------------------------
    def open(self, path: str = "/"):
        self.driver.get(self.base_url + path)

    @property
    def path(self) -> str:
        return re.sub(r"^https?://[^/]+", "", self.driver.current_url)

    def wait_path(self, path: str):
        self.wait.until(lambda d: self.path.split("?")[0] == path, f"expected {path}, at {self.path}")

    def find(self, css: str, within=None):
        return self.wait.until(
            lambda d: (within or d).find_element(By.CSS_SELECTOR, css)
            if (within or d).find_elements(By.CSS_SELECTOR, css) else False,
            f"no element {css}",
        )

    def all(self, css: str, within=None):
        return (within or self.driver).find_elements(By.CSS_SELECTOR, css)

    def heading(self, text: str):
        return self.wait.until(
            EC.visibility_of_element_located((By.XPATH, f"//h1[normalize-space()={xpath_text(text)}]")),
            f"no heading {text!r}",
        )

    def text_appears(self, text: str, within=None):
        target = within or self.driver.find_element(By.TAG_NAME, "body")
        self.wait.until(lambda d: text in target.text, f"text {text!r} never appeared")

    def button(self, name: str, within=None):
        xpath = (f".//button[normalize-space()={xpath_text(name)} or @aria-label={xpath_text(name)}]")
        return self.wait.until(
            lambda d: next((b for b in (within or d).find_elements(By.XPATH, xpath) if b.is_displayed()), False),
            f"no button {name!r}",
        )

    def type(self, css: str, value: str):
        field = self.find(css)
        field.clear()
        field.send_keys(value)
        return field

    def choose_first(self, css: str) -> str:
        """Pick the first real option of a <select> and return its label."""
        select = self.find(css)
        self.wait.until(lambda d: len(Select(select).options) > 1, f"{css} has no options")
        Select(select).select_by_index(1)
        return Select(select).first_selected_option.text

    # -- shared flows ----------------------------------------------------------
    def sign_in(self, email: str, password: str = PASSWORD, expect_path: str | None = "/"):
        self.open("/login")
        self.type("#login-email", email)
        self.type("#login-pass", password)
        self.button("Sign in").click()
        if expect_path:
            self.wait_path(expect_path)

    def sign_out(self):
        self.button("Sign out").click()
        self.wait_path("/login")


def xpath_text(value: str) -> str:
    """Quote a string for XPath, including ones with apostrophes."""
    if "'" not in value:
        return f"'{value}'"
    return "concat('" + "', \"'\", '".join(value.split("'")) + "')"


@pytest.fixture
def app(driver, base_url):
    """A signed-out browser on the site for each test."""
    driver.get(base_url + "/login")
    driver.delete_all_cookies()
    driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
    driver.get(base_url + "/login")
    return App(driver, base_url)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Save a screenshot of the browser when a test fails."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and "app" in item.funcargs:
        ARTIFACTS.mkdir(exist_ok=True)
        path = ARTIFACTS / f"{item.name}.png"
        try:
            item.funcargs["app"].driver.save_screenshot(str(path))
            report.sections.append(("screenshot", str(path)))
        except Exception as error:  # noqa: BLE001 - never hide the real failure
            report.sections.append(("screenshot", f"not saved: {error}"))


__all__ = ["App", "TimeoutException"]
