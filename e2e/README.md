# Selenium end-to-end tests

These tests drive a real Firefox browser through the real frontend and backend.
They complement the fast component tests in `frontend/src/**/*.test.jsx`, which
use a fake API: here every click goes through routing, the Vite proxy, the
FastAPI backend and PostgreSQL.

## Run

```bash
bash bin/e2e-test.sh              # all tests, headless (about 1–2 minutes)
bash bin/e2e-test.sh -k signup -v # only sign-up tests, one line per test
E2E_HEADED=1 bash bin/e2e-test.sh # watch the browser
```

Needs Firefox and geckodriver, Node (for Vite), the backend virtualenv
(`backend/facility-service/.venv`) and the local PostgreSQL used by
`bin/facility-local.sh`. The first run creates `e2e/.venv` with Selenium.

### What a run does

1. Creates a throwaway database `acme_facility_e2e` on the local cluster, runs the
   migrations and loads the showcase seed (41 accounts, ~127 incidents).
2. Starts the API on port 8029 and the frontend (Vite) on port 3029.
3. Runs the tests in one headless Firefox, signed out before each test.
4. Stops both servers and drops the database (`E2E_KEEP=1` keeps it).

Your dev database (`acme_facility_dev`) and dev servers (8019/3000) are not touched.
When a test fails, a screenshot is saved to `e2e/artifacts/<test>.png` and server
logs are in `e2e/artifacts/logs/`.

### Options

| Variable | Effect |
|---|---|
| `E2E_HEADED=1` | Show the browser window |
| `E2E_KEEP=1` | Keep the e2e database after the run |
| `E2E_PASSWORD` | Password of the seeded accounts (default `E2e-Passphrase-2026`) |
| `E2E_BASE_URL` | Test a site that is already running instead of starting one |

`E2E_BASE_URL` can point at the deployed site, with `E2E_PASSWORD` set to its demo
password. Be aware that the sign-up and report tests then create real accounts
and tickets there.

## Test plan

| ID | File | What is checked |
|---|---|---|
| TC-01 | test_auth.py | A signed-out visitor opening a protected page is sent to sign-in |
| TC-02 | test_auth.py | A wrong password shows an error and stays signed out |
| TC-03 | test_auth.py | An employee lands on My tickets; no admin menu items |
| TC-04 | test_auth.py | An admin sees Triage, Workload, Overview and Users |
| TC-05 | test_auth.py | Sign out ends the session, even after a reload |
| TC-06 | test_auth.py | An employee typing an admin URL goes back to My tickets |
| TC-07 | test_auth.py | Signing in returns to the page that was asked for |
| TC-08 | test_signup.py | Names like `12345`, `John123`, `user_1` are refused |
| TC-09 | test_signup.py | Mismatched passwords are refused |
| TC-10 | test_signup.py | A proper name creates the account; the person can sign in; spaces are tidied |
| TC-11 | test_signup.py | An email that already has an account is refused |
| TC-12 | test_report.py | Submitting a blank report lists the missing fields |
| TC-13 | test_report.py | A valid report gets a number, opens its page and shows in My tickets |
| TC-14 | test_admin_paging.py | Overview "Needs a decision" pages 10 at a time |
| TC-15 | test_admin_paging.py | Changing the period restarts "Needs a decision" at page 1 |
| TC-16 | test_admin_paging.py | Workload "Waiting for an engineer" pages 10 at a time; header shows the full count |
| TC-17 | test_admin_paging.py | Workload "Blocked" pages 10 at a time |
| TC-18 | test_admin_paging.py | Workload "All team tickets" pages 20 at a time |
| TC-19 | test_admin_paging.py | Picking an engineer, and going back, restarts at page 1 |

The paging tests read each list's total from the page and walk every page, so
they hold for any data set; with the showcase seed, "Waiting for an engineer" is
required to have more than one page.

## Layout

- `conftest.py`: starts the stack and Firefox; `App` helper with navigation,
  waits and shared flows (`sign_in`, `sign_out`); screenshots on failure.
- `stack.py`: creates, serves and drops the e2e database (runs with the backend's Python).
- `test_*.py`: the test cases above.
