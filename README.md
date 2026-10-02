# Amaze Go

A playable web version of the arrow-clearing puzzle: React + TypeScript, FastAPI,
and Firebase Cloud Firestore. Includes exactly 1,000 validated, solvable levels,
numbered 1–1000. The local catalog in `data/levels/`
is the authoritative catalog, read by FastAPI directly from the checked-out Git files.
Firebase stores player and session records only.

Tap an arrow whose forward lane is clear. Its body follows the path off the board.
A blocked tap costs a life. Clear all arrows to win. The mechanics follow the
[official Amaze GO! description](https://play.google.com/store/apps/details?id=com.oakever.arrows).

Features: three lives, three hints per attempt, restart, level previews and search,
four arrow colors, optional sound and animations, keyboard controls, zoom and pan,
responsive touch controls, automatic session resume, saved progress and stars.

## Run this workspace

The project is already configured in the ignored `.env` for **amazego-llm-2026**.
Firestore browser access rules are closed; game data is accessed through FastAPI. The local backend
uses a private credential file in `~/.config/amaze-go/`, derived from the existing
Firebase CLI login. Credentials are outside this repository and outside React.
The Google Cloud CLI login was left unchanged.

```sh
git lfs install
git lfs pull
npm install
uv sync --python 3.12
npm run dev
```

- Game: http://127.0.0.1:5173
- API and production build preview: http://127.0.0.1:8000
- Interactive API documentation: http://127.0.0.1:8000/api/docs
- Admin panel: http://127.0.0.1:8000/admin
- Firebase console: https://console.firebase.google.com/project/amazego-llm-2026/firestore

Requires Node 22.12+ (or a Vite-supported Node version), Python 3.12+, and `uv`.
`npm run dev` runs Vite and FastAPI together. FastAPI also serves `dist/` after
`npm run build`. Stop the processes with Ctrl+C.

### Fresh machine: cloud Firestore

Copy `.env.example` to `.env`, set `FIREBASE_PROJECT_ID` to your project, and remove
`FIRESTORE_EMULATOR_HOST`. Supply standard
[Application Default Credentials](https://firebase.google.com/docs/firestore/quickstart-server):

```sh
gcloud auth application-default login
npm run dev
```

The selected account must have Firestore access to that project. A Firebase CLI
login alone is not normally Application Default Credentials. For a deployed
backend, use its platform service identity with `roles/datastore.user`, or set
`GOOGLE_APPLICATION_CREDENTIALS` to a server-only service account JSON file.

### Local Firestore emulator

Use the emulator entries in `.env.example`. It is the same Firestore repository
and transaction code as the cloud database. Install the Java version required by
your Firebase CLI, then use two terminals:

```sh
# Terminal 1
npm run emulators

# Terminal 2
npm run dev
```

The demo project never contacts a cloud database. Emulator data exports to the
ignored `.emulator-data/` folder on clean shutdown. The initial missing import
directory is normal on the first run. For isolated offline testing only,
`STORAGE_BACKEND=memory` is available; the UI identifies it as practice mode and
its data is not persisted across backend restarts.

## Git-backed level catalog and LFS

The 1,000 active level JSON files live in `data/levels/`. Git LFS tracks this dataset
and source image/archive assets under `data/`; application code, lockfiles, and
`data/level.schema.json` remain ordinary Git files. Install Git LFS and run
`git lfs pull` after cloning, including in CI before a Docker build. A checkout
containing only LFS pointers cannot load the game.

The backend validates and loads the catalog at startup. Neither the catalog nor
copies of boards are written to Firebase. Every saved session contains a
`level_id` and SHA-256 `level_hash`, and the server loads that board from the Git
catalog when resuming or validating moves. Attempts whose board version has
changed cannot be resumed against a different puzzle.

To replace local level files with a validated source directory:

```sh
npm run levels:replace -- --source /path/to/new-levels
npm run levels:validate
```

Restart the backend after editing the catalog, then commit and push the JSON files
through Git LFS. To add a level, copy `data/examples/custom-level.json` into
`data/levels/`, give it a unique ID and number, validate, and restart.

For a database created by the older version, run the explicit one-time migration:

```sh
npm run levels:migrate
```

The migration verifies embedded session boards against the Git catalog before
writing, replaces board copies with references and fingerprints, and removes the
legacy Firestore `levels` documents. It preserves player identities, progress,
session state, history, and retry receipts. Stop the old backend before migration.
New application startup never seeds or stores levels in Firebase.

## Administration

```sh
npm run admin:setup
npm run build
npm run dev
```

Open `http://127.0.0.1:8000/admin`, or `/admin` on the Vite development server.
Setup creates the username `admin`, generates a password, stores its scrypt hash
and a session signing secret in the ignored `.env`, and writes the login details
to the private file `~/.config/amaze-go/admin-credentials.txt` with mode 0600.
The password is not printed or committed. Restart the backend after setup.

For your own credentials, use `npm run admin:setup -- --interactive --username YOUR_NAME`.
To change an existing login, add `--rotate`; this also invalidates previous admin
cookies. In deployment, set `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, and
`ADMIN_SESSION_SECRET` through your platform's secret environment configuration.
Serve the admin panel from the backend's own origin and use HTTPS in deployment.

The panel shows player creation times, active-session references and per-level
progress, plus session identities, actor names/types, status, moves, mistakes,
lives, hints, timestamps, and full action history. Lists support pagination and
search within loaded records. Admin responses omit player authentication hashes.
Login uses an eight-hour signed HTTP-only cookie, with Secure on HTTPS,
SameSite=Strict, CSRF protection for deletions/logout, and sign-in rate limiting.
Player bearer tokens cannot access admin endpoints.

Deleting a session removes its action history and clears its active-session
reference. Deleting a winning attempt recalculates that player's progress for the
level from remaining winning attempts. Deleting a player removes their identity,
progress, and all associated sessions. The panel asks for confirmation before
either deletion.

### Version 1 JSON format

See `data/level.schema.json` for the machine-readable JSON Schema. Every level
has its own dimensions, inferred from its matrix; boards need not share a size.

```json
{
  "schema_version": 1,
  "id": "my-first-level",
  "number": 1001,
  "name": "A fresh start",
  "difficulty": "easy",
  "shape": "rectangle",
  "lives": 3,
  "matrix": [
    [0, 0, 0, 0, 0],
    [1, 1, 0, 2, 0],
    [0, 0, 0, 2, 0]
  ],
  "arrows": [
    {
      "id": 1,
      "path": [
        [1, 0],
        [1, 1]
      ]
    },
    {
      "id": 2,
      "path": [
        [1, 3],
        [2, 3]
      ]
    }
  ]
}
```

| Field                 | Meaning                                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------------------------- |
| `matrix[row][column]` | Zero-based row/column coordinates. `-1` outside the silhouette, `0` empty, positive integer = arrow ID. |
| `arrows[].path`       | Every adjacent cell of the arrow, **tail first, head last**. Include straight intermediate cells.       |
| Arrow direction       | Difference between the last two path cells. One of up, down, left, right.                               |
| `shape`               | Descriptive metadata. The matrix defines the actual shape.                                              |
| `difficulty`          | `easy`, `medium`, `hard`, or `expert`.                                                                  |

Arrows must have unique positive integer IDs, contain at least two cells, use
orthogonal adjacent steps, stay inside the matrix, and never overlap or repeat
a cell. Matrix IDs and path cells must agree exactly. Maximum dimensions are
100 × 100, with up to 1,000 arrows; large boards can be zoomed and panned.

A move scans the **straight ray beyond the head** to the rectangular matrix
boundary. Other positive arrow IDs block it; empty cells, silhouette holes, and
the arrow's own body do not. Removing an arrow changes its cells to `0` and
preserves `-1` cells. In this example, clear arrow 2 before arrow 1.

Validation rejects cyclic blocking dependencies and other unsolvable layouts.
The generator builds in reverse removal order, so every generated puzzle has a
solution. Any currently clear arrow can safely be removed: clearing an arrow
never introduces a new blocker.

Firestore collections are namespaced under `amaze_go/v1/{players,sessions}`.
Player and attempt details are stored as JSON payloads, with separate queryable
identity fields. Board matrices and arrow paths exist only in the Git catalog and
in-memory/API observations; they are not persisted to Firestore.

## LLM evaluations in the admin panel

Open `/admin` and choose **LLM agents**. Create a named evaluation with a model preset,
editable model ID, contiguous level range, and 1–100 lives per level. Each run has
its own player identity; each level has a separate attempt and a fresh life budget.
Wins and losses both advance to the next selected level. Runs and transcripts
persist in Firestore; memory mode remains temporary.

All model families use the same UFL OpenAI-compatible Chat Completions API, matching
the supplied `run_navapi.py` format. Set these **server environment variables** in
Vercel (or the local ignored `.env`) and redeploy/restart:

```dotenv
UFL_API_KEY=your-ufl-key
UFL_BASE_URL=https://api.ai.it.ufl.edu
```

The base URL is used as supplied, including any configured path; the app does not
append `/v1`. No separate OpenAI, Anthropic, or Gemini key is needed. The seven
presets are `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-astra`, `opus-5`, `claude-opus-5.5`,
`fable-5.1`, and `gemini-3.8-flash`. These presets do not guarantee that the key
has access to them. Gemini's preset ID is inferred from its
display name; use the exact ID reported by UFL if it differs. Model IDs remain
editable, and existing runs also route through UFL.

The server uses this request structure, with the current PNG in the user message:

```python
from openai import OpenAI
import os

with OpenAI(
    api_key=os.environ["UFL_API_KEY"],
    base_url=os.environ["UFL_BASE_URL"],
    timeout=90,
    max_retries=0,
) as client:
    response = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[{"role": "user", "content": "This is a test request. Write a sentence."}],
    )
    print(response.choices[0].message.content)
```

An optional per-run UFL API key overrides `UFL_API_KEY`.
Run credentials are encrypted with a key derived from `ADMIN_SESSION_SECRET` and
are never returned in admin responses or kept in browser storage. Rotating that
secret requires recreating runs that use the old encrypted credentials.

Open **Admin → API health** to inspect configuration and check one or all models.
Loading the page does not call the gateway. **Discover model IDs** reads UFL's
`/models` endpoint; if listing is unavailable, you can still enter a model ID.
**Text response** sends a short prompt and validates non-empty completion content
and the expected reply. **Image response** attaches a blue PNG and checks the
reported color. Each result shows response text, returned model, finish reason,
latency, token usage when supplied, and safe failure diagnostics, including a
transport error category when available. A 403 can indicate an incorrect alias
or missing model access; discovery flags preset IDs absent from the key's model
list. A warning means
text was returned but the expected reply did not finish correctly. Image checks
confirm a basic image request; they do not measure puzzle-solving quality.

Checks make real calls and can consume API credits. Check-all runs one bounded
request per model, updates results as they finish, and stops scheduling checks
when you leave the page. Text and image results are kept separately for the current
page visit. Upstream errors never expose raw bodies, headers, or keys. These
endpoints use the existing admin cookie and CSRF protection:

| Method | Endpoint                      | Purpose                              |
| ------ | ----------------------------- | ------------------------------------ |
| GET    | `/api/admin/api-health`       | Configuration and requested presets. |
| POST   | `/api/admin/api-health/models` | Discover gateway model IDs.          |
| POST   | `/api/admin/api-health/check`  | Check one model (`model`, `mode`, `timeout_seconds`). |

**Single step** makes one model request. **Play** repeats turns while the panel
stays open. **Pause** saves progress and lets an in-flight request finish;
**Resume session** continues the saved attempt later, including after reopening
the panel or stopping. **Stop** discards an
in-flight decision if it has not already been applied. Autoplay stops when leaving
the panel and requires pressing Continue or Resume again after reopening. There is no background
worker or unattended billing after the panel closes. A submitted request can still
finish after closing the browser. The dashboard displays the current labelled
PNG, lives, moves, mistakes, level outcomes, and paginated turn transcripts with
the exact request, raw response, brief model explanation, token usage, latency,
and before/after images. Delete an evaluation's player from Players to remove
its attempts, encrypted credential, and transcripts together.

Every call supplies the complete rules and current PNG. The default observation
contains the image, remaining IDs, and counters. An optional setting adds the
matrix and tail-to-head paths. Neither mode sends solver-derived legal moves or
hints. The action contract is one JSON object:

```json
{"arrow_id": 12, "explanation": "Its forward lane appears clear."}
```

The engine computes the next state. Only a blocked tap costs a life. Missing,
empty, truncated, malformed, and unknown-arrow responses leave the board, lives,
moves, and mistakes unchanged. They remain visible in the transcript as unscored
response failures. The next request names the last tapped arrow,
its blocker where applicable, the lost life, and lives remaining. Transport,
timeouts, connection errors, HTTP 408/409/429 and server errors use bounded
automatic recovery during autoplay, with exponential backoff and the provider's
`Retry-After` delay when supplied. Three consecutive failures halt autoplay until
the admin resumes. Authentication, access, bad request, and refusal errors require
admin attention immediately. No provider failure scores a mistake. An exhausted
output budget doubles for the next request, up to 16,384 tokens. Finish reasons,
effective output budgets, and safe error categories/HTTP statuses are logged.
Output-token, request-timeout, and per-level turn limits are configurable.
Only image-capable model IDs work; rejected image inputs are never silently
replaced by text-only calls.

**Download performance history** is available after completion or whenever a
session is paused, stopped, or awaiting admin recovery, after its in-flight turn
finishes. The admin-only JSON download includes all transcript pages, config,
level results, per-turn prompts and responses, token usage, latency, diagnostics,
before/after snapshots, game action history, original boards and hashes, plus
overall and per-level performance totals. Historical invalid-action life penalties
are preserved and reported separately. Tokens on failed requests may be unknown.

Each step uses a UUID, a durable lease, and the game's retry receipts. Concurrent
steps are rejected. Completed requests can be retried without another model call
or game action. A saved response can recover after an interrupted game commit;
if a process dies before saving its provider response, recovery after the
ten-minute lease expires may require another provider call.

Gateway request format: [LiteLLM OpenAI-compatible client setup](https://docs.litellm.ai/docs/proxy/user_keys).

## Human and agent API

Both use the same server-side engine. The human UI predicts each move immediately,
so animation, hints, and life-loss feedback do not wait for Firebase. Rapid taps
are queued in order and verified by FastAPI in batches of up to 100 moves, with
one Firestore transaction per batch. The server's response remains authoritative.
Older replies are reconciled with newer queued moves rather than restoring
arrows that were already tapped. Pending actions are kept in browser storage;
reconnecting or reloading retries their original UUIDs and skips acknowledged
actions. Level changes wait until pending moves finish saving.

Each anonymous player receives an opaque bearer token; keep it
private. The browser remembers it locally, and Firestore stores only its hash.
Sessions are scoped to their owner. Give each evaluation agent a separate player
and session. `actor_type` and `actor_name` are recorded for future benchmarking.

| Method | Endpoint                              | Purpose                                              |
| ------ | ------------------------------------- | ---------------------------------------------------- |
| GET    | `/api/health`                         | Storage mode and project.                            |
| GET    | `/api/v1/levels`                      | Catalog and dimensions.                              |
| GET    | `/api/v1/levels/{level_id}`           | Full original level JSON.                            |
| POST   | `/api/v1/players`                     | Create a player; returns `access_token`.             |
| GET    | `/api/v1/players/me`                  | Saved progress and active session.                   |
| POST   | `/api/v1/sessions`                    | Start an independent attempt.                        |
| GET    | `/api/v1/sessions/{id}`               | Observe the current state.                           |
| POST   | `/api/v1/sessions/{id}/actions`       | Tap an arrow.                                        |
| POST   | `/api/v1/sessions/{id}/actions/batch` | Verify and save an ordered group of up to 100 moves. |
| POST   | `/api/v1/sessions/{id}/hints`         | Highlight a legal arrow, up to three per attempt.    |
| GET    | `/api/v1/sessions/{id}/history`       | Ordered action log and actor metadata.               |

All player/session requests require `Authorization: Bearer <access_token>`.
Restarting means creating a new session for the same level; prior history remains
available. Session creation body:

```json
{ "level_id": "level-001", "actor_type": "agent", "actor_name": "my-llm-v1" }
```

Tap body:

```json
{ "arrow_id": 5, "expected_revision": 0, "action_id": "8cb070bc-9650-4b1b-a9dc-1d9199609f6e" }
```

Use the returned state's `revision` for the next action and a new UUID for each
intentional action. Retries use the **same UUID and identical action**. Transactions
make retries idempotent, prevent simultaneous moves from using the same revision,
and save wins together with progress. A stale revision returns HTTP 409; fetch
the session before deciding again. Duplicate receipts return the original outcome
and the current state, without charging a life or recording another win.

Action responses contain `state` and `outcome`. Observations include `matrix`,
remaining `arrows`, `removed_ids`, `legal_arrow_ids`, lives, moves, hints, revision,
and status (`active`, `won`, `lost`). Outcomes are `cleared`, `blocked`, or `hint`,
with `arrow_id`, `blocked_by`, and reward: +1 clear, -1 blocked, +10 final clear,
0 hint. Three mistakes end a standard attempt. Invalid IDs and malformed actions
are rejected without using a life. Progress keeps best moves, best time, and stars
across completed attempts; one mistake reduces the run's three-star score by one.

For a batch, provide the revision **before its first action**, with unique UUIDs:

```json
{
  "expected_revision": 0,
  "actions": [
    { "type": "tap", "arrow_id": 5, "action_id": "8cb070bc-9650-4b1b-a9dc-1d9199609f6e" },
    { "type": "hint", "action_id": "b8462721-6469-4f7d-ab50-d26b49f949c0" }
  ]
}
```

Batch responses contain `state` and ordered `outcomes`. Each action advances the
revision by one. Invalid or conflicting batches roll back all their new actions.
Retrying an identical batch does not repeat moves or count another win, even if
a prefix of its actions was already committed. Observations expose
`processed_action_ids` so the browser can recover an interrupted save safely.

`legal_arrow_ids` is provided for debugging and reference policies. For an LLM
benchmark that must infer legality, remove this field from the model's observation
before passing it to your agent. The server still validates its chosen ID.

An executable reference client is included:

```sh
uv run python -m backend.scripts.example_agent --level level-001
```

Replace its safe-arrow selection with an LLM call that reads the matrix and paths.
It creates its own player and does not affect the human browser's game.

## Verify

```sh
npm run build
npm test
uv run ruff check backend
npm run levels:validate
# Optional real Firestore transaction, concurrency, retry, and persistence test:
AMAZE_RUN_FIRESTORE_TESTS=1 uv run pytest backend/tests/test_firestore.py -v
```

The frontend suite checks prediction against fixtures produced by the Python
engine, immediate play with unresolved requests, ordered batches, stale replies,
network failures, and recovering pending moves after reload. The regular backend
suite uses memory storage to isolate engine/API tests. The opt-in
integration test uses the configured Firebase database and leaves an independent
test player's completed attempt in Firestore.

## Deployment

### Vercel

The repository includes a FastAPI Vercel preset and a build command that builds
React into `dist/`. FastAPI serves the frontend and `/api` from the same origin;
leave `VITE_API_BASE_URL` unset and do not override the Output Directory to `dist`.

Import the GitHub repository into Vercel with the repository root as the Root
Directory and FastAPI as the Framework Preset. Leave the default Python install
step enabled; the configured build command installs the frontend dependencies.
Select a Node version supported by Vite (Node 22.12+ or 24).

Set these server environment variables for each deployment environment you use:

| Variable | Value |
| --- | --- |
| `STORAGE_BACKEND` | `firestore` |
| `FIREBASE_PROJECT_ID` | Your Firebase project ID, currently `amazego-llm-2026`. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Complete JSON from a Firebase service-account private key. |
| `ADMIN_USERNAME` | Your configured admin username. |
| `ADMIN_PASSWORD_HASH` | The configured scrypt hash from your private `.env`. |
| `ADMIN_SESSION_SECRET` | The configured signing secret from your private `.env`. |

Keep credential values in Vercel's secret environment settings. Do not add
`FIRESTORE_EMULATOR_HOST`, a Mac credential path, or a `VITE_`-prefixed credential
variable. If importing a private `.env.vercel` file, it is ignored by Git.

In the Vercel project's **Settings > Git**, enable **Git Large File Storage
(LFS)**, then redeploy. The level catalog uses LFS and must contain the actual
JSON files instead of pointer files. An initial deployment before enabling LFS
can fail to start; the redeployment picks up the complete catalog.

Check `/api/health` on the deployment: it should report `status: "ok"`,
`storage: "firestore"`, and `emulator: false`. Play a level and reload to check
saved progress; `/admin` uses the configured admin login. Local tests use memory
storage; the deployed service-account permissions must be checked on Vercel.

### Docker

A single Docker container can serve both
the built React app and FastAPI on port 8000, avoiding cross-origin configuration:

```sh
docker build -t amaze-go .
docker run --rm -p 8000:8000 \
  -e STORAGE_BACKEND=firestore \
  -e FIREBASE_PROJECT_ID=amazego-llm-2026 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/credentials/server.json \
  -v /absolute/path/to/server.json:/credentials/server.json:ro amaze-go
```

Use a production service identity or server credential with access to Firestore.
The Docker build excludes local credentials and `.env`. On a platform with an
attached service identity, omit the credential file and its mount.

For separate Firebase Hosting + backend deployment, build with
`VITE_API_BASE_URL=https://your-api-host`, add the hosting origin to the backend's
`CORS_ORIGINS` JSON list, then deploy the `dist/` directory using `firebase deploy
--only hosting`. The included hosting configuration serves the React app; API
traffic goes directly to the URL specified at build time. Deploy the FastAPI
service separately.
