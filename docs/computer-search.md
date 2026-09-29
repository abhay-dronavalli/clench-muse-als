# Computer search

## Try it without a headband

Use the isolated core on **8001** and board on **5174** from AGENTS.md. First-time setup:

```powershell
uv sync --extra sensor
uv run playwright install chromium
```

1. Open the board at `http://localhost:5174`, unlock audio, and choose Computer / Computadora.
2. Scan to the YouTube launcher button and clench. On YouTube, clench the group containing the
   search field, then the field itself. In Chromium, Space is clench, B is back and holding Space
   starts help. The foreground Dev panel/backtick and pointing modes are still follow-up work.
3. The search panel shows up to five queries, Other / Otro, Keyboard / Teclado and Cancel.
   Pick a query to echo it, fill the chosen field and press Enter. The results return to group
   scanning. No extra confirm is required: picking the query confirms the search.
4. Other shows new queries, excludes earlier choices and loops after three pages. With no key or
   network, local bilingual choices work immediately. A late AI reply does not rearrange a panel.
5. Keyboard scans rows first and letters second. Choose `c`, then `e`, then a matching completion
   such as Celia. Completions replace only the current word. The last row has Space, Delete and
   Done. Done submits the visible text. An empty, URL-like or blocked query stays in the keyboard.
6. B from either keyboard level returns to suggestions and keeps the draft. B or Cancel from
   suggestions closes the panel. LONG_CLENCH pauses the current panel/draft for the normal help
   countdown. Test help only on an isolated core with dry-run actions; the ordinary help path can
   contact the configured caregiver.

Search requires an editable control identified as a search field by its type, role, label, form
or conventional query name. Generic comment and chat editors show a message instead. A removed
field, changed page or newly blocked label requires choosing the field again. The site allowlist
and action denylist remain in `data/computer.yaml`.

## Learning and privacy

Only an acknowledged fill-and-Enter writes a search: site, query, language, local hour and time.
The existing ranking score uses the last 30 days of that site's searches in the current language.
Searches never become spoken-message shortcuts. Day 1 ignores search history for suggestions but
keeps recording confirmed searches for later learning.

Gemini receives only site, language, local hour, patient first name, five recent searches, twenty
top spoken phrases and already-shown queries for exclusion. It receives no page contents, URL,
cookies or field value. Completions run locally. Suggestions share the existing provider timeout,
cache and pause behavior; the local launcher and Google account/mail pages do not request them.

To seed an **isolated** demo database without changing the shared patient database:

```powershell
uv run python scripts/seed_demo.py --load --db .venv/computer-search-demo.db
```

The simulated searches include Celia Cruz in the evening, in both languages. Point a separately
configured test app's `db_path` at that file to see persistent learning. The in-memory launch
example in AGENTS.md deliberately forgets searches on restart. `--reset` also clears search history.

## Automated checks

Stop only your own 8001 test core before the local Chromium tests; they bind that port. Leave
8000/5173 alone. Tests use fake providers and temporary or in-memory databases and profiles.

```powershell
uv run pytest
uv run python -c "import core.contracts"
npm.cmd --prefix web test
npm.cmd --prefix web run build
npm.cmd --prefix web run lint
```

Coverage includes paging and cancellation, cached/failed suggestions, privacy, URL rejection,
history ranking and seed searches, row-column entry, completions, help interruption and real
Chromium fill/Enter on a local page. Real-network tests remain opt-in. Spotify playback still
depends on the installed Chromium's media/DRM support.
