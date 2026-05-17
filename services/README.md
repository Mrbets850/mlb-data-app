# Real-time lineup service

`services/lineup_service.py` provides confirmed and projected MLB lineups,
probable pitchers, and live in-game substitutions through a single
provider-agnostic interface.

## Provider priority

1. **Sportradar** (`SPORTRADAR_MLB_API_KEY`) — premium. Game Summary feed.
2. **SportsDataIO** (`SPORTSDATAIO_MLB_API_KEY`) — premium. StartingLineupsByDate.
3. **MLB StatsAPI** — always-on free fallback (`statsapi.mlb.com`).

Providers are tried in order; the first one to return a non-empty result
wins. No keys are required — the app runs entirely on the free MLB
StatsAPI source out of the box.

## Enabling premium providers on Streamlit Cloud

Add either or both of these to your Streamlit Cloud "Secrets" panel — they
are read via `os.environ` so they work locally as plain env vars too:

```toml
# .streamlit/secrets.toml  (or the Streamlit Cloud secrets editor)
SPORTRADAR_MLB_API_KEY = "your-sportradar-key"
SPORTSDATAIO_MLB_API_KEY = "your-sportsdataio-key"
```

Restart the Streamlit app after adding a key. Missing keys leave the
premium adapters dormant — they are never called and never log warnings.

## Cache TTLs

The service caches per-game results with a status-aware TTL so pre-game
freshness is high without hammering the API once games go final:

| Status        | TTL  |
|---------------|------|
| not_posted    | 60s  |
| expected      | 90s  |
| confirmed     | 120s |
| live          | 45s  |
| final         | 1h   |
| postponed     | 15m  |

## Output shape

`get_daily_lineups(date)` / `get_game_lineups(game_pk)` return
`GameLineups` dataclasses with `away`, `home`, `lineup_status`,
`provider`, and `last_updated`. The UI uses `format_freshness()` to
render the provider · status · age chip next to each lineup banner.
