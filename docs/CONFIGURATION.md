# Configuration reference

`.env.example` holds the keys and their defaults, and nothing else: `startup.sh`
copies it to `.env` on first run, and the explanations live here instead so the
file you edit stays short. The sections below follow the file in order. Five
values must be filled in; everything after them has a working default, and
`startup.sh` refuses to start until all five are present.

## Required — the stack will not start without these

| Key | What it is |
|---|---|
| `DISCORD_TOKEN` | The bot token from the Discord Developer Portal. |
| `GUILD_ID` | The numeric id of the one Discord server the bot serves. |
| `OPENROUTER_API_KEY` | The OpenRouter key every narration route except the optional Google one goes through. |
| `ENGINE_AUTH_TOKEN` | Shared Python ↔ Go engine credential. **Both** processes must carry the same value; it is what stops anything else on the network calling the authoritative engine. At least 20 characters — the engine, the bot and the dashboard all refuse to start with less (v0.29.0). |
| `DASHBOARD_TOKEN` | GM dashboard login, used with `DASHBOARD_USERNAME`. At least 20 characters or the dashboard refuses to start. The dashboard is on by default; set `DASHBOARD_ENABLED=false` further down to run without it. |

Generate either token with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Discord

`MESSAGE_CONTENT_INTENT` — Slash commands do not need Message Content Intent.
Typed play and the chat monitor do, and it must also be enabled in the Discord
Developer Portal.

`RP_CHANNEL_IDS`, `AUTO_NARRATE` — Channels the bot narrates in. `AUTO_NARRATE`
makes it respond to ordinary lines in those channels rather than only to slash
commands. `AUTO_NARRATE_EVENT_THREADS` extends that to event threads;
`EVENT_THREAD_AUTO_ARCHIVE_MINUTES` is the Discord auto-archive setting for
threads the bot opens.

### Typed play (v0.21.1)

In the channels `AUTO_NARRATE` listens to, a line that starts with
`TYPED_PLAY_PREFIX` is an action the engine resolves (`$ I explore the ravine`
runs `/explore`); a line that names an NPC who is present, or @mentions the
bot, is dialogue; every other line is speech and costs nothing.

The prefix is exactly one character, not a letter, digit or space. It is
passed through `env_file` literally, so `$` needs no escaping; if your Compose
version complains about interpolation, quote it: `TYPED_PLAY_PREFIX='$'`.
`>` was the default before v0.25.1 and is still worth considering: Discord
renders `> text` as a blockquote, so action lines look different from speech.

`TYPED_PLAY_BURST` / `TYPED_PLAY_PER_MINUTE` are a per-player token bucket on
every typed line that can reach the engine or the narrator, so one player
cannot drain the shared OpenRouter allowance for everyone. `TYPED_PLAY_HINT`
shows a player, once a day, how to use the prefix the first time an
un-prefixed line of theirs looks like an action.

### Administrator chat monitor (`/admin` → Server → Chat Digest)

Reads Discord message history and summarises it through the **same** free
OpenRouter chain as narration, so it can never spend money. The ceilings
(`MONITOR_MAX_MESSAGES`, `MONITOR_LOOKBACK_HOURS`, `MONITOR_CHUNK_CHARS`,
`MONITOR_MAX_CHUNKS`) exist because both share the
`OPENROUTER_MAX_REQUESTS_PER_MINUTE` limiter. `MESSAGE_CONTENT_INTENT` must be
true **and** Message Content Intent must be enabled in the Discord Developer
Portal, or history comes back empty.

## AI narration — cloud only, no GPU required

`NARRATOR_PROVIDER` is `openrouter` (the only provider since v0.28.0),
`procedural` (template prose only, no model calls) or `disabled`.
`OPENROUTER_BASE_URL` rarely needs changing.

### Optional: direct Google AI Studio route (v0.26.0)

Set `GOOGLE_AI_STUDIO_API_KEY` and narration tries Google **first**, on both
tiers, before any OpenRouter route. It is the single most effective thing you
can do about procedural fallbacks, because it does not touch OpenRouter's
~50-a-day free budget at all — this route is billed against your own AI Studio
key, and on Google's free tier that means not billed.

Get the key from https://aistudio.google.com/api-keys — AI Studio makes a
project and a key for a new account automatically, so it is usually already
there to copy; otherwise click "Create API key". It works on Google's free tier
as it is; nothing here needs Cloud Billing. You can give the same key to
OpenRouter (below), or here, or both — they are independent paths.
`GEMINI_API_KEY` is accepted as an alias, because that is the name Google's own
quickstart tells you to export.

Needs the SDK: `pip install -U google-genai` (it is in `requirements.txt`, so
Docker already has it). If the package is missing the route is simply left out
of the chain and narration carries on through OpenRouter — check
`/admin server ai_status`, which says so in as many words. Leave the key
**empty** to keep the OpenRouter-only behaviour of v0.25.x.

`GOOGLE_AI_STUDIO_MODEL` must start with `aistudio/` so the router knows which
transport serves it.

### Narration chains

**Routine chain** (v0.26.1 defaults, after the Google route above if one is
configured): Gemma 4 31B Free -> OpenRouter Free Models Router.

There is no named second hop any more. GLM 5.2 held it until v0.26.1, when
OpenRouter withdrew the free variant and started answering it with "404 - This
model is unavailable for free"; the paid slug cannot replace it because
`OPENROUTER_REQUIRE_FREE` rejects paid IDs. Rather than name a third free model
that may be withdrawn in its turn, the slot is empty and `openrouter/free` —
the dynamic router, which resolves to whatever is actually free when the call
is made — carries the tier. Put a slug in `OPENROUTER_ROUTINE_FALLBACK_MODEL`
to add a named hop, or choose one from the dashboard's Narration Routes panel
(v0.28.0), which lists OpenRouter's live free catalogue.

Gemma's `:free` route is served by Google AI Studio **alone**, and Google's
shared free pool rate-limits per user (in production: 429 on 30 of 30
attempts). It is only a good primary if you have added your own Google AI
Studio key to OpenRouter, in two steps:

1. Get the key from https://aistudio.google.com/api-keys (see above). Nothing
   here needs the Gemini SDK — OpenRouter makes the calls.
2. Paste it at https://openrouter.ai/settings/integrations under the
   "Google AI Studio" integration — **not** Vertex — and save it.

Then Gemma runs on your own AI Studio quota, which is far larger. Check
`/admin server ai_status`: each route says which upstream served it and whether
it went "via your own provider key" or via the shared pool. On that key, set
"shared capacity fallback" to "never use shared capacity for models this key
applies to", so a failure of **your** key shows up in `ai_status` as Google's
error instead of the pool's 429. Without a key, put `openrouter/free` first:
`OPENROUTER_ROUTINE_MODEL=openrouter/free` (any route at "NEVER SUCCEEDED" is
costing a daily free-tier slot per retry).

**Epic chain** (v0.26.1 defaults): Gemma 4 31B Free -> OpenRouter Free Models
Router. Nemotron 3 Super narrated its own instructions 3 of 3 times and was
dropped; MiniMax M3 was dropped in v0.25.3 for returning its reasoning
scratchpad as the narration (it is reasoning-native and ignores the switch
below); GLM 5.2 was dropped in v0.26.1 when its free variant left the
catalogue.

`OPENROUTER_DISABLE_REASONING` — Reasoning ("thinking") is switched off on
every narration request. Both production failures of the free chain were
reasoning: models spending the whole token budget thinking and returning
nothing, and a model returning its thinking **as** the narration. OpenRouter
turns it off on models that have it and ignores the setting on models that do
not. Set `false` to let models think.

`OPENROUTER_DYNAMIC_FREE_FALLBACK` — the dynamic last cloud fallback; it
selects from currently available free models. `OPENROUTER_REQUIRE_FREE`
rejects any paid model id in any slot, from `.env` and from the dashboard
alike.

### Budgets, timeouts and context

`OPENROUTER_MAX_REQUESTS_PER_MINUTE` — fail over instead of making Discord
users wait through long retries.

`OPENROUTER_MAX_REQUESTS_PER_DAY` — OpenRouter's free tier is 20
requests/**minute** and 50 requests/**day** while the account has under $10 of
lifetime credits, 1000/day at $10 or more. Every failed route walks to the next
one and each walk spends a daily slot, so the allowance drains faster than the
narration count suggests. Leave it unset and use the switch below.

`OPENROUTER_CREDITS_TOPPED_UP` — the ten-dollar switch (v0.31.0). `false`
means the 50-a-day allowance, `true` means the account has bought ten dollars
of credit and the daily cap is 1000. The dashboard's **Narration Routes**
panel has the same switch and its choice is stored by the engine and applied
live, so this key is the baseline for a fresh install rather than the last
word. An explicit `OPENROUTER_MAX_REQUESTS_PER_DAY` overrides both.

`TYPED_PLAY_BURST` / `TYPED_PLAY_PER_MINUTE` (above) are, since v0.31.0, the
budget on **every** door - typed lines, slash commands and hub buttons, and
the *Narrate it* asks - one bucket per player, however they reach the engine.

`OPENROUTER_ROUTE_REQUESTS_PER_MINUTE` / `OPENROUTER_ROUTE_REQUESTS_PER_DAY` —
**per-route** ceilings. The two above are account-wide (OpenRouter's own);
these are what a **provider** enforces per **model**. Google allows Gemma 4
about 15 requests/minute and 1500/day per model, so one global 20/min ceiling
was at once too high for a single route and too low for two of them. A route
at its own ceiling is **skipped** rather than called and refused. The defaults
are right in both regimes: on the shared free pool the account daily cap binds
first and these never fire; with your own provider key
(openrouter.ai/settings/integrations) they are the real one.

`OPENROUTER_TIMEOUT_SECONDS`, `OPENROUTER_EPIC_TIMEOUT_SECONDS`,
`OPENROUTER_FAILURE_COOLDOWN_SECONDS` — per-call timeouts for the two tiers and
how long a route rests after a failure before it is tried again.

`OPENROUTER_APP_URL`, `OPENROUTER_APP_NAME` — optional OpenRouter app
attribution headers.

`NARRATOR_CONTEXT_MAX_CHARS`, `RAG_CONTEXT_CACHE_SECONDS`,
`RAG_CANON_CACHE_SECONDS` — small canonical/RAG packets are intentional; large
model contexts are unnecessary.

## Gameplay tuning

The cooldowns (`CULTIVATE_COOLDOWN_MINUTES`, `EXPLORE_COOLDOWN_MINUTES`,
`HUNT_COOLDOWN_MINUTES`, `PERFECT_QUEST_COOLDOWN_MINUTES`,
`PERFECT_TRIAL_COOLDOWN_MINUTES`, `SECRET_REALM_COOLDOWN_MINUTES`) are real
minutes between uses of the corresponding action. `UNEXPECTED_EVENT_CHANCE_PERCENT`
is the chance an explore rolls an unexpected event. `WORLD_TIME_SCALE` is how
many game minutes pass per real minute. `REINCARNATION_BASE_SAMSARA_YEARS` and
`REINCARNATION_MAX_WAIT_SECONDS` shape how long a dead character waits in
samsara before rebirth.

### Quest Forge (v0.20.6)

`/admin world questforge <story>` asks the narrator's free model chain to draft
a quest from your text; it is held as a draft until you approve it on the
dashboard's Quests page (or `/admin world quests`). With `QUEST_FORGE_AUTO=true`
the bot also drafts one quest per notable world-history event (significance ≥
`QUEST_FORGE_MIN_SIGNIFICANCE`) every `QUEST_FORGE_INTERVAL_HOURS` — drafts
only, never auto-approved; each costs one routine-tier request. Rewards a
forged quest may declare are capped by `QUEST_REWARD_MAX_XP`,
`QUEST_REWARD_MAX_STONES` and `QUEST_REWARD_MAX_ITEMS` and granted through the
engine on completion. The same budget applies to quests written by hand.

## Authoritative Go engine / SQLite WAL

`DATABASE_PATH` — the SQLite file the Go engine owns. Python never opens it.
`GAME_ENGINE_TIMEOUT_SECONDS` — how long a Python call to the engine waits.

`ENGINE_SHUTDOWN_GRACE_SECONDS` (commented out in the file; unset means 20) —
how long the Go engine waits for in-flight requests when it is asked to stop
(v0.22.4). It drains rather than severing connections, so a mutation that has
already committed still gets its response written instead of leaving the
caller unable to tell whether it happened. Keep this below the orchestrator's
own kill timeout — `docker-compose.yml` gives the engine a 30s stop grace — or
the wait is a fiction.

## Health, limits and alerting

`HEALTH_HOST` — The application binds loopback (`127.0.0.1`) when this is
unset; the shipped file binds all interfaces (`0.0.0.0`) on purpose so a
bare-metal run behaves like the Docker one, where `docker-compose.yml` sets
`0.0.0.0` inside the bot container regardless. The listener carries `/metrics`
and the dashboard's control channel; set `127.0.0.1` or the NAS's LAN address
to narrow it.

`HEALTH_PORT` — `8082`, not 8080: on a QNAP that is the QTS web admin, and with
`HEALTH_HOST` on all interfaces the two collided. Any free port works;
`docker-compose.yml` pins 8082 inside the bot container and points the
dashboard at it.

`HTTP_MAX_REQUEST_LINE_BYTES`, `HTTP_MAX_HEADER_LINES`, `HTTP_MAX_HEADER_BYTES`,
`HTTP_HEADER_DEADLINE_SECONDS`, `HTTP_HEADER_LINE_TIMEOUT_SECONDS`,
`HTTP_MAX_CONNECTIONS` — HTTP request-head limits, for the health listener and
the GM dashboard. Both parse HTTP by hand and read the request head **before**
any authentication. A per-line timeout alone is not a limit: a client sending
one header just under it holds the connection open forever (Slowloris), and a
client sending them fast grows the header dict without bound. These are
generous for a browser and mean for an attacker.

`SLOW_QUERY_MS` — queries slower than this are logged. `ALERT_WEBHOOK_URL` and
`ALERT_COOLDOWN_SECONDS` — an optional webhook for operational alerts and the
minimum gap between repeats of the same alert.

## GM dashboard

`DASHBOARD_TOKEN` is in the required block at the top of the file.

Five keys look interchangeable and are not:

| Key | Meaning |
|---|---|
| `DASHBOARD_ENABLED` | read by `startup.sh` only; the application ignores it |
| `DASHBOARD_BIND_ADDRESS` | which NAS interface the port is published on |
| `DASHBOARD_PORT` | the port you open in a browser on the NAS |
| `DASHBOARD_INTERNAL_PORT` | the port the process listens on inside the container |
| `DASHBOARD_HOST` | the interface it binds inside the container |

`docker-compose.yml` publishes `<BIND_ADDRESS>:<PORT>:8090` and sets `HOST` to
`0.0.0.0` inside the container itself, so under Docker change only the first
two. `DASHBOARD_BIND_ADDRESS=0.0.0.0` publishes the dashboard on every
interface of the NAS, which is what makes it reachable from your PC on the
LAN; the lockout, the Origin check and the token are what protect it there,
and a TLS reverse proxy is still the right front door (README, "Configure
dashboard access"). Set it to the NAS's LAN address to narrow it, or to
`127.0.0.1` for the NAS itself only. Running outside Docker, `INTERNAL_PORT`
is the port you connect to; `DASHBOARD_HOST` is loopback (`127.0.0.1`) when
unset and all interfaces in the shipped file.

`DASHBOARD_USERNAME` pairs with `DASHBOARD_TOKEN` for Basic Auth.
`DASHBOARD_ADMIN_WRITES` — set `false` for a read-only dashboard; when true,
`GAME_ENGINE_URL` must be set (compose supplies it).

`DASHBOARD_LOGIN_MAX_FAILURES`, `DASHBOARD_LOGIN_WINDOW_SECONDS`,
`DASHBOARD_LOGIN_LOCKOUT_SECONDS` — failed-login lockout (v0.29.0). Basic Auth
has no session, so every request is a login attempt; after `MAX_FAILURES` wrong
passwords from one source address inside `WINDOW` seconds, that address is
answered `429` for `LOCKOUT` seconds before its credentials are even read.
Behind a reverse proxy every request shares the proxy's address and so shares
the lock.

`DASHBOARD_ALLOWED_ORIGINS` — Browser mutations must come from the dashboard's
own origin: a `POST` whose `Origin` header does not match the `Host` it was
sent to is refused, which is what stops another tab from posting admin actions
with your session. If a reverse proxy rewrites `Host` on the way in, list the
public origin(s) here, comma-separated, e.g. `https://gm.example.lan`.

`DASHBOARD_ACTOR_ID` — Attributes every dashboard-originated admin write in
`admin_audit_log` to this actor id instead of the unattributed default.
Defaults to 1, a small integer that can never collide with a real Discord
snowflake (17–19 digits), which is what the bot's slash-command path already
uses as `ActorID`. Set it to your own Discord user id instead if you would
rather dashboard actions and bot-command actions attribute to the same
identity in the audit log.

`BOT_CONTROL_TOKEN` — Private dashboard → Discord bot control. Leave blank to
reuse `DASHBOARD_TOKEN`. Docker Compose supplies `BOT_CONTROL_URL` internally;
for non-Docker local runs use `http://127.0.0.1:8082` (or whatever
`HEALTH_PORT` you chose above).

## Release channel (v0.20.4)

Releases are GitHub Releases on `UPDATE_REPOSITORY`. The bot checks the channel
once after startup and then every `UPDATE_CHECK_HOURS`, and posts "Update
available" to the bot log channel once per newer release. Nothing is
downloaded or installed by the bot; that stays with `./update.sh` on the NAS:

```bash
./update.sh --check      # ask the channel (network)
./update.sh --fetch      # download + verify the newest ZIP into ./updates
./update.sh --upgrade    # fetch, then install
```

`UPDATE_CHANNEL=stable` sees full releases only; `beta` also sees pre-releases
(tags like `v0.21.0-beta.1`). `update.sh` reads the same three variables.
`UPDATE_CHECK_ENABLED=false` turns the check off.

## Daily route check (v0.27.0)

Every `ROUTE_AUDIT_HOURS` the bot pings each configured narration route with
the cheapest request the API accepts — one character in, one token out — and
never reads the reply. The point is to find a route that has **died** before a
player does: a slug OpenRouter withdrew (404), a key it rejects (401), a route
behind a proxy that refuses it (403). Those three retire the route until the
next pass, so narration stops spending a daily free-tier slot being told the
same thing. A 429 or a timeout never retires anything — that is congestion,
and the per-route cooldown already handles it.

A 400 is the reasoning-mandatory case: every narration request disables
reasoning, so a provider that **rejects** that parameter can never narrate
here. Because a 400 is also what some providers return for `max_tokens=1`, it
is confirmed by changing one variable at a time before anything is retired
(v0.28.0), and the verdict is shown per route on the dashboard.

It costs one slot per OpenRouter route per pass (the AI Studio route is free of
this budget, as always), and it stands down entirely when less than half the
daily budget is left: narration is what the budget is for. If every route
fails at once that reads as a local fault rather than an empty catalogue, so
none are retired and `/admin server ai_status` says so.

The Google AI Studio route is never retired no matter what it answers: it is
your key, your quota, outside this budget entirely, and losing it for a day
would put every narration back on the ~50-a-day allowance it exists to avoid.

It answers "is this route reachable", never "is it any good" — a model that
returns its reasoning instead of prose passes this and is still caught at
narration time by the scratchpad guard. `0` turns it off.
