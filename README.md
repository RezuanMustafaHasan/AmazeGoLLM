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

## Human and future LLM API

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

The site has not been publicly deployed. A single Docker container can serve both
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
