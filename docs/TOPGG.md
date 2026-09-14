# Listing the bot on Top.gg

Top.gg is a Discord bot list. A listing is how people who have never heard of
your server find it, and votes are how a listing climbs. This is the whole
setup, start to finish. It takes about ten minutes, and the bot works perfectly
well without any of it — `/vote` simply goes on taking the claim on trust, the
way it did before this existed.

Everything here is **outbound only**. No port is opened on your NAS, nothing is
published, and Top.gg's inbound vote webhook is deliberately not used.

## 1. Create the listing

Go to <https://top.gg/bot/new> and submit your bot. You will need:

- **the bot's client ID** — Discord Developer Portal → your application →
  *General Information* → Application ID;
- **a short and a long description** — the long one is markdown, and it is the
  page people actually read. Say what the world is, not what the framework is;
- **an invite link**, if you want one that differs from Top.gg's default;
- **the prefix** — this bot is slash-commands-only, so put `/`.

Submission goes into a moderation queue. Approval usually takes a day or two,
and nothing below works until it lands.

## 2. Get the project token

Once the listing is approved, open it and go to **Edit → Webhooks** (the URL is
`https://top.gg/bot/<your bot id>/webhooks`). The **token** on that page is
your project token.

Copy it. It is a secret — it is the credential for your listing's API, and
anyone holding it can post to your page.

> **You do not need OAuth.** Top.gg also documents an OAuth 2.1 + PKCE flow
> with a client ID, a client secret and a redirect URI. That exists so a
> *third-party application* can manage listings belonging to other people — a
> dashboard service, say. It needs a public `https` callback URL, which is
> exactly the thing this deployment does not have. For your own listing, the
> project token is the correct and simpler credential.

## 3. Put it in `.env`

```ini
VOTE_SITE_NAME=Top.gg
VOTE_SITE_URL=https://top.gg/bot/<your bot id>/vote
TOPGG_TOKEN=<the token from step 2>
TOPGG_VERIFY_VOTES=true
TOPGG_POST_METRICS=true
```

Two notes:

- `VOTE_SITE_URL` should be the **`/vote` page**, not the listing root — it is
  the link `/vote` prints, and it should land people on the button.
- `TOPGG_TOKEN` may be pasted with the leading `Bearer ` Top.gg's curl examples
  show; it is stripped for you. A value with a space anywhere else in it will
  stop the bot at boot, on purpose — the alternative is discovering the typo as
  a silent `401` on every vote, hours later.

Restart the bot (`./stop.sh && ./startup.sh`, or `./migrate_env.sh` first if
you are upgrading an existing `.env`).

## 4. Check it worked

- `/vote` should print the link, and — when verification is live — a line
  saying the vote is confirmed before the gift is paid.
- Vote on your own listing, then press the button. You should get the gift.
- Press it again within twelve hours: the engine refuses on its cooldown.
- Look at `/admin` → the `topgg_metrics` health check. `ok` means Top.gg
  accepted your server count. It is posted a minute after startup and every
  thirty minutes after that.

If the button refuses a vote you know you cast, wait a few seconds and press
again — Top.gg takes a moment to record one, and the button is deliberately
left live so a player can retry.

## What is actually checked

`GET https://top.gg/api/v1/projects/@me/votes/<user id>`, with
`Authorization: Bearer <TOPGG_TOKEN>`. The project is addressed as `@me` — the
token names it — which is why there is no bot ID to configure and no way to ask
about a listing that is not yours. The answer carries `expires_at`; Top.gg
expires a vote twelve hours after it is cast, which is the same cadence the
gift already renewed on, so that one field decides the verdict.

**The check fails open.** Only a Top.gg that answers and says *no live vote*
refuses a claim. An outage, a rate limit, a timeout or a token you have just
rotated all pay the gift on trust, exactly as the bot did before any of this
existed — a third party's bad day is not a reason to take something away from a
player who did nothing wrong, and the engine's twelve-hour cooldown still
bounds what an outage can cost. A refused token is logged as an error and
raised as a health check, because it is the one failure you have to act on.

The receipt records which it was: `support.vote_claim` carries a `verified`
flag into `admin_audit_log`'s sibling event ledger, so a claim that was checked
can be told later from one paid through an outage. It changes nothing about
what is paid — the cooldown bounds a dishonest claim and a verified one
identically.

## Turning it off

Clear `TOPGG_TOKEN` and both features stop; `/vote` returns to the
trust-based claim. Set `TOPGG_VERIFY_VOTES=false` to keep posting your server
count while paying every claim on trust, or `TOPGG_POST_METRICS=false` to
verify votes without publishing a server count.

## Why there is no SDK in `requirements.txt`

Top.gg publishes `topggpy` for Python, and it is not used here. The reasons are
worth writing down, because the question comes back every time someone reads
the Top.gg docs:

- **It targets the previous API.** `topggpy` 1.4.0 sends the bare token as
  `Authorization` and calls `/bots/<id>/check` and `/bots/stats`. The current
  API — the one Top.gg's own actively maintained Go SDK uses, and the one this
  bot calls — is `Bearer` auth against `/v1/projects/@me/...`. Adopting the
  Python SDK would mean moving *backwards* one API version.
- **Its last stable release was November 2021.** That is a long time for
  something sitting in the path that decides whether a player gets paid.
- **It depends on `discord.py` without a bound.** This project pins
  `discord.py` exactly and installs `requirements.lock` under
  `--require-hashes`; an unbounded transitive pin on the Discord library is
  the one dependency shape that policy exists to prevent.
- **Its headline feature is the one thing this deployment cannot use.** The
  built-in webhook server needs an inbound port open on the host. That is the
  door `/vote` has always been written to keep shut, and the whole reason the
  check here is an outbound call.

What the SDKs *are* good for is telling you what the API actually does.
`app/ops/topgg.py` was written against the Go SDK's source for exactly that
reason, and `tests/python/unit/test_topgg.py` pins the request shape — the URL
and the `Bearer` header — so that if Top.gg moves again, a test says so rather
than a `401` in production.

## Reference

- Configuration keys: [CONFIGURATION.md](CONFIGURATION.md)
- The client: `app/ops/topgg.py`
- The claim path: `app/bot/commands/support.py`
- The gift itself (size, cadence, receipt): `go_core/internal/game/support_actions.go`

The API surface above was taken from Top.gg's own Go SDK rather than from
their docs site. Note that the published install line, `go get
github.com/top-gg/go-dbl`, does not work as written — that repository declares
its module path as `github.com/top-gg-community/go-sdk`, which is what you have
to `go get`. This bot does not use the SDK (the Go engine has no third-party
dependencies and makes no outbound calls; the client is the ~200 lines of
Python in `app/ops/topgg.py`), but the SDK is the authoritative record of what
the v1 endpoints and auth header actually are.
