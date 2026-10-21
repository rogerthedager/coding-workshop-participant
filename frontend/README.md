# Coding Workshop - Frontend Code

## Overview

React (Vite) frontend for the ACME facility incident platform, built from the
"ACME Incident Platform Preview" design canvas. It talks to
`backend/facility-service` at `/api/v1`.

| Route | Who | Screen |
|---|---|---|
| `/login`, `/register`, `/change-password` | Everyone | Sign in, create account, replace a temporary password |
| `/` | Everyone | My tickets: counts, fixes awaiting confirmation, status filter |
| `/report` | Everyone | Report an incident (floor and seat are optional) |
| `/incidents` | Everyone | Search and filtered lists (dashboard numbers link here) |
| `/incidents/:id` | Everyone with access | Detail: progress, escalation, notes, timeline and the workflow actions the viewer may take |
| `/dashboard` | Everyone | Employee, engineer and admin tabs, by role |
| `/queue` | Engineers | Tickets assigned to me |
| `/triage` | Admins | Unassigned open tickets with one-click assignment |
| `/team` | Admins | Engineer lead view: load per engineer, hand out work, blockers |
| `/overview` | Admins | Leader view: counts, weekly trend, decisions needed, hotspots |
| `/engineers` | Admins | Add engineers; set availability and up to 2 skills |

The backend has no separate "leader" or "engineer lead" role, so those two
views are shown to admins.

## Structure

```
frontend/src/
├── api.js              # API client: in-memory access token, refresh cookie, If-Match versions
├── auth.jsx            # Session state (restored from the refresh cookie on load)
├── directory.jsx       # Names for the IDs the API returns (categories, locations, people)
├── format.js, lib.js   # Formatting and shared workflow helpers
├── hooks.js            # useLoad data hook
├── components/         # Layout (top bar, side nav, mobile bottom nav), table, chips
└── pages/              # One file per screen
```

## Usage

### Local Development

1. Start the backend (from `backend/facility-service`):
   `python scripts/local.py migrate`, `python scripts/local.py seed --demo`,
   then `python scripts/local.py serve` (port 8019).
2. Start the frontend: `npm install` then `npm run dev`, and open `http://localhost:3000`.

In development, Vite forwards `/api` to the backend, so the browser stays on one
origin and the `SameSite=Strict` refresh cookie works. Set `FACILITY_API_TARGET`
to forward somewhere other than `http://localhost:8019`. Use `localhost`, not
`127.0.0.1`, in the browser.

Demo accounts from the seed: `employee@acme.inc`, `engineer@acme.inc`,
`admin@acme.inc` (password: the backend's `DEMO_PASSWORD`).

For a deployed build, set `VITE_API_URL` to the API origin (without `/api/v1`)
and add the frontend origin to the backend's `FACILITY_ALLOWED_ORIGINS`.

### Cloud Deployment

To deploy your frontend to AWS:

```sh
./bin/deploy-frontend.sh
```

To view your application, open the browser and navigate to CloudFront URL.

## Clean Up

To remove all deployed resources (including frontend):

```sh
./bin/cleanup-environment.sh
```

**Warning**: This removes all infra resources. Cannot be undone.
