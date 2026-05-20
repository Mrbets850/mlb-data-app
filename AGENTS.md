# AGENTS.md

## Cursor Cloud specific instructions

### Overview

MLB Edge is a Streamlit-based MLB analytics and sports-betting intelligence dashboard. It is a single Python application (`app.py`) with no database, no Docker, and no background workers.

### Running the app

```
streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
```

The app runs entirely on free MLB StatsAPI data and committed Baseball Savant CSVs. No API keys or secrets are required for basic functionality. Optional paid APIs (Odds API, Sportradar, SportsDataIO) degrade gracefully when keys are absent.

### Running tests

```
python3 -m pytest tests/ -v
```

All 242 tests are pure-Python unit tests with canned payloads — no network calls or external services needed.

### Linting

No linter or formatter is configured in the repo. There is no `pyproject.toml`, `setup.cfg`, or linting configuration.

### Key gotchas

- The `python` command is not available by default on the VM; always use `python3`.
- Pip installs to `~/.local/bin` which may not be on `PATH`. Use `export PATH="$HOME/.local/bin:$PATH"` or invoke tools via `python3 -m <tool>`.
- The Streamlit theme is configured in `.streamlit/config.toml` (dark mode, custom colors). Do not modify this unless intentionally changing the UI theme.
- Baseball Savant CSV data files live in `Data:savant_*.csv` (note the `Data:` prefix — the directory is named `Data:savant_...`). These are committed to git and auto-refreshed by GitHub Actions.
- The app uses `zoneinfo` for timezone handling (`America/Chicago`). No additional timezone packages need to be installed on Python 3.9+.
