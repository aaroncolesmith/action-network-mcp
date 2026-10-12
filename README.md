# action-network-mcp

Live sports odds from Action Network for any agent. One core client, exposed four ways:
REST + OpenAPI, MCP (HTTP or stdio), a CLI, and a Python import.

Any agent that can make an HTTP call, speak MCP, run a shell command, or import Python gets the same data:

```
            ┌── REST /v1/*  + /openapi.json ── OpenAI/LangChain/n8n/scripts
OddsService ┼── MCP  /mcp (HTTP) or stdio ──── Claude, Cursor, any MCP client
 (cache +   ├── CLI  python -m action_network ─ agents with a shell
  history)  └── import action_network ──────── your own Python agents
```

## What an agent gets

`get_odds(league="nfl", team="chiefs")` returns, per game:

- **consensus**: median moneyline / spread / total across books, plus **no-vig win probability**
- **best**: best available price per side and which book has it (best spread = most favorable point, then price; best over = lowest total, best under = highest)
- **books** (optional, `include_books=true`): the full per-book table
- **as_of** (UTC) and **cached** on every response, so agents can judge freshness

Other tools: `get_games`, `get_public_betting` (ticket % vs money %), `get_line_movement`,
`list_futures` / `get_futures`, `list_leagues`.

Leagues: `nfl nba mlb ncaab soccer` and the soccer competitions `epl laliga bundesliga seriea mls champs` (proven by the archiver), plus `nhl ncaaf wnba` (same pattern, unverified).
Team lookup is fuzzy: `chiefs`, `KC`, `Kansas City`, `niners` all work. With no date, a team lookup searches forward 7 days for football and 2 days otherwise.

## Published snapshots (for chat apps)

ChatGPT, Grok and claude.ai can't call a private server, but they can open a public URL. A scheduled
workflow (`.github/workflows/publish_odds.yaml`) runs about every 30 minutes and publishes DraftKings lines to GitHub Pages:

```
https://aaroncolesmith.github.io/action-network-mcp/odds/<league>/<YYYY-MM-DD>.json
https://aaroncolesmith.github.io/action-network-mcp/odds/index.json
```

- Leagues: `mlb nfl ncaaf nba wnba ncaab nhl epl laliga mls bundesliga seriea champs`
- Dates are Pacific. Today through +7 days for NFL/NCAAF, +4 for everything else.
- Each game lists `options`: every allowed pick with its exact text (`"Kansas City Chiefs -3.5"`, `"Over 44.5"`, `"Arsenal ML"`, `"Draw"`) and American odds.
- Only games that hadn't started when the snapshot ran are listed. `retrieved_at` says when that was.
- A league that failed to load still gets its files, with an `error` field and no games.

Run it locally with `python -m action_network.snapshot --out site`, or `--leagues NFL,EPL` for a subset.
Change the book with `AN_SNAPSHOT_BOOK_ID`. `--show-books` prints Action Network's own book ids and names.

GitHub turns off scheduled workflows in public repos after 60 days without commits; re-enable it from the Actions tab if that happens.

## Run it

```bash
pip install ".[server]"

# REST + MCP on one port
AN_API_TOKEN=change-me uvicorn action_network.api:app --host 0.0.0.0 --port 8000

# or with Docker (keep history on a volume)
docker build -t action-network-mcp .
docker run -p 8000:8000 -e AN_API_TOKEN=change-me -v an_data:/data action-network-mcp
```

Interactive docs at `/docs`; machine-readable spec at `/openapi.json`.

## Connect agents

**Any HTTP agent / OpenAPI tool-loader**

```bash
curl -H "Authorization: Bearer change-me" \
  "http://localhost:8000/v1/odds?league=nfl&team=chiefs&books=DraftKings,FanDuel"
```

Point OpenAPI-aware frameworks (OpenAI Agents SDK, LangChain `OpenAPIToolkit`, n8n, Zapier) at `/openapi.json`. Operation ids match the MCP tool names.

**MCP over HTTP** (remote; works from any machine)

```bash
claude mcp add --transport http action-network https://YOUR-HOST/mcp \
  --header "Authorization: Bearer change-me"
```

Other MCP clients: URL `https://YOUR-HOST/mcp`, header `Authorization: Bearer <token>`.

**MCP over stdio** (local; no server to run)

```json
{
  "mcpServers": {
    "action-network": { "command": "python", "args": ["-m", "action_network", "mcp"], "cwd": "/path/to/action-network-mcp" }
  }
}
```

**CLI** (for agents with a shell)

```bash
python -m action_network odds nfl --team chiefs --markets spread,total
python -m action_network games nba --date tomorrow
python -m action_network public nfl --team raiders
python -m action_network futures nfl --market mvp
```

**Python**

```python
from action_network import OddsService
odds = OddsService().get_odds("nfl", team="chiefs")
```

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `AN_API_TOKEN` | *(unset = no auth)* | Bearer token for REST and `/mcp`. Set this when deployed. |
| `AN_HOST` | `0.0.0.0` | Set `127.0.0.1` for local-only use (enables MCP DNS-rebinding protection). |
| `AN_SCOREBOARD_TTL` | `45` | Seconds a scoreboard response is cached. |
| `AN_FUTURES_TTL` | `600` | Seconds futures are cached. |
| `AN_MIN_REQUEST_INTERVAL` | `1.0` | Minimum seconds between upstream requests. |
| `AN_HISTORY_DB` | `data/odds_history.sqlite` | Line-movement store. Empty disables it. |
| `AN_POLL_LEAGUES` | *(unset)* | e.g. `nfl,nba`: poll in the background so movement accumulates without traffic. |
| `AN_POLL_INTERVAL` | `300` | Poll interval in seconds. |
| `AN_BOOK_NAMES` | built-in map | JSON override, e.g. `{"68":"DraftKings"}`. |
| `AN_NON_MARKET_BOOK_IDS` | `15,30` | Rows excluded from best/consensus math (consensus and open). |

## Caveats

- **Unofficial API.** This uses actionnetwork.com's undocumented web endpoints. They can change without notice, and heavy automated use may break their terms. All endpoint knowledge lives in `action_network/config.py` and `client.py`, and requests are cached and rate-limited.
- **Book names are best-effort.** Book 15 is treated as Action Network's consensus row because it is the row carrying public-betting %. Other ids follow community mappings; verify against the site and override with `AN_BOOK_NAMES` if any are wrong.
- **Line movement starts when the service first sees a game.** "open" is the earliest snapshot, not necessarily the true opening line (book 30 is likely the opener and is included in movement output). Use `AN_POLL_LEAGUES` to build history continuously.
- **Not yet covered:** player props. The endpoints need to be identified from the site's network tab, then added as one more client method + tool.

## Tests

```bash
pip install ".[server,dev]" && pytest -q
```

Tests run against recorded-shape fixtures in `tests/fixtures/`, so they don't hit Action Network.
