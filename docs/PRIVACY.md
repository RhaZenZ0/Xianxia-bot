# Privacy Policy

**Last updated:** 14 September 2026

This policy covers the Xianxia cultivation RPG Discord bot ("the bot"). It is
written for the people who play it, and it describes what the software actually
does — every claim below corresponds to code in this repository, and the
file is named so you can check.

> **The bot is self-hosted.** Each operator runs their own copy on their own
> hardware, with their own database and their own API keys. That means *the
> operator of the server you play on* is responsible for your data, not the
> authors of this software. This policy describes what the software does; ask
> your server's operator if you need to know who is holding it.

## What is collected

**Your Discord user ID.** A numeric ID, not your email or your password. It is
the key everything else is filed under. At the current schema it appears in 103
of the 182 tables.

**What you do in the game.** Your character's name, path and attributes,
inventory, currency, location, sect and family membership, quests, cultivation
progress, combat and trade records, auction bids, and the cooldowns that meter
all of it.

**What you type in game channels.** The bot keeps a rolling window of the most
recent 60 messages per channel (`scene_history` in `app/database/core.py`) so
the narrator knows what just happened. Older messages are deleted automatically
as new ones arrive — this is a context window, not a chat archive.

**Summaries derived from your play.** The retrieval memory
(`rag_memories`) holds short summaries of events involving your character, so
NPCs can remember you. These are generated from game events, not copied from
your messages wholesale.

**Administrative records.** Actions a GM takes are written to
`admin_audit_log`, including who did what and why.

The bot does **not** collect your email address, your password, your IP
address, your payment details, or anything Discord does not hand to a bot.
Reading message content at all requires the `MESSAGE_CONTENT_INTENT` setting,
which is **off by default**.

## What leaves the operator's machine

This is the part most worth reading.

**AI narration providers.** The bot describes outcomes using a language model.
When narration runs, scene context — your character's name, the location, what
just happened, and in typed play the text you wrote — is sent to whichever
provider is configured: OpenRouter (which routes on to a model provider, and
whose free tier may use requests to improve their models), and optionally
Google AI Studio. **Your Discord user ID is not part of that request** — it is
used inside the bot as a lookup key and a random seed, never placed in the
prompt. Narration can be turned off entirely, and the bot falls back to
pre-written template text.

**The administrator chat digest.** If a GM runs the chat monitor, a transcript
of recent channel messages is sent to the same AI provider to be summarised.

**Top.gg.** If the operator has configured vote rewards, your Discord user ID
is sent to Top.gg to check whether you voted, so the reward can be paid. See
[TOPGG.md](TOPGG.md). Nothing else is sent; if this is not configured, Top.gg
is never contacted.

**GitHub.** The bot checks for its own updates. No player data is involved.

Nothing else leaves. There are no analytics, no advertising, no trackers, and
your data is never sold or shared with anyone else.

## How long it is kept

Game data is kept for as long as your character exists and the operator runs
the server. Messages in the narrator's context window are deleted automatically
once 60 newer messages have arrived in that channel. There is no automatic
expiry for the rest — it is the state of a persistent world.

## Deleting your data

**You can ask for everything to be deleted, and it will be.** Open an issue at
[github.com/RhaZenZ0/Xianxia-bot/issues](https://github.com/RhaZenZ0/Xianxia-bot/issues),
or ask a GM on your server directly.

This is a real deletion, not a flag. The operator runs `/admin player erase`,
which removes your rows from every table in the schema that holds a Discord ID
— your character, your inventory, your messages, the memories NPCs hold of you.
It finds those tables by reading the database schema, so it cannot go stale as
the game grows. The code is `go_core/internal/game/privacy_actions.go`.

Two deliberate exceptions, because deleting them would be wrong:

- **Shared world state keeps its row and loses the link to you.** The history
  of a battle that happened, a sect or family you founded that other players
  still belong to, a quest you authored that others are part-way through. These
  stop naming you; they do not vanish, because they are other people's world
  too.
- **The record that your deletion happened is kept.** One row in
  `admin_audit_log` saying an erasure was performed for your ID, when, and how
  many rows it touched. It contains none of your game data. It exists so the
  operator can show your request was honoured.

Your erasure does mean some shared records disappear entirely — a trade between
you and another player, a duel you fought — because those rows are as much
yours as theirs.

**Deletion cannot be undone.** There is no restore.

## Your other rights

Depending on where you live (the GDPR, if you are in the EU/EEA or UK), you may
also have the right to ask what is held about you, to have it corrected, or to
object to it being processed. Use the same contact link above. A GM can read
your stored state back to you with `/admin player inspect`.

## Children

Discord requires users to be at least 13, or older where local law says so. The
bot is not directed at children and does not knowingly hold data about anyone
below Discord's minimum age.

## Security

The database is a local SQLite file on the operator's machine. The engine is
the only component that opens it, and the GM dashboard requires authentication.
How well that machine is secured is the operator's responsibility.

No transmission over the internet is ever completely secure, and no operator
can guarantee absolute security.

## Changes

Changes are committed to this file in the repository, so its full history is
public and you can see exactly what changed and when. Material changes will be
announced on the Discord server.

## Contact

[github.com/RhaZenZ0/Xianxia-bot/issues](https://github.com/RhaZenZ0/Xianxia-bot/issues)

---

*This document describes the behaviour of an open-source hobby project. It is
provided in good faith and is not legal advice. Operators deploying this bot in
a jurisdiction with specific requirements should take their own advice.*
