#!/bin/sh
set -eu

# Rebuild .env on a new release's .env.example, keeping the values you already
# set (v1.0.0).
#
# A release adds keys, moves them between sections and rewrites the comments,
# but it must never touch your .env - that is where your tokens live, and
# update.sh preserves it for exactly that reason. The two then drift, and
# `startup.sh --check-env` starts refusing an install over a key the operator
# has never seen. Until now the only way through was to diff .env against the
# new .env.example by hand and copy the tokens across, at the point in an
# upgrade where a mistyped DISCORD_TOKEN costs another restart cycle.
#
# This does that copy. The NEW .env.example is the shape - its keys, its order,
# its comments and section headers - and the OLD .env is the source of every
# value it already had. New keys arrive at the release's default. Keys you have
# that the release no longer ships are kept, at the end, under a header saying
# so: dropping a value an operator set is the one outcome there is no undo for.
#
#   ./migrate_env.sh              # rewrite ./.env, keeping a timestamped backup
#   ./migrate_env.sh --dry-run    # say what would change, write nothing
#   ./migrate_env.sh --env PATH --template PATH
#
# It reads values, it never evaluates them: `TYPED_PLAY_PREFIX=$` and
# `OPENROUTER_APP_NAME=Xianxia RP` survive verbatim. No value is ever printed -
# the report names keys only, so it is safe to paste into an issue.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$SCRIPT_DIR/.env"
TEMPLATE="$SCRIPT_DIR/.env.example"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --env) [ $# -ge 2 ] || { echo "ERROR: --env needs a path." >&2; exit 2; }; ENV_FILE=$2; shift ;;
        --template) [ $# -ge 2 ] || { echo "ERROR: --template needs a path." >&2; exit 2; }; TEMPLATE=$2; shift ;;
        -h|--help) sed -n '3,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "ERROR: unknown option $1 (try --help)." >&2; exit 2 ;;
    esac
    shift
done

[ -f "$TEMPLATE" ] || { echo "ERROR: no template at $TEMPLATE." >&2; exit 1; }
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: no .env at $ENV_FILE." >&2
    echo "       There is nothing to carry over; startup.sh creates one from .env.example." >&2
    exit 1
fi

# Secrets are about to pass through these. 077 covers the new file, the backup
# and every scratch file below, whatever the operator's umask happens to be.
umask 077
WORK=$(mktemp -d "${TMPDIR:-/tmp}/xianxia-env.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM

# keys_of FILE - every key the file assigns, one per line, in file order.
keys_of() {
    tr -d '\r' < "$1" | sed -n 's/^[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)=.*$/\1/p'
}

# value_of FILE KEY - the raw text after the first "=", exactly as written.
# Nothing is stripped, unquoted or expanded: the value is config, not shell.
# The last assignment wins, which is the rule every reader in this repo uses.
value_of() {
    tr -d '\r' < "$1" | sed -n "s/^[[:space:]]*$2=//p" | tail -n 1
}

has_key() { grep -q "^[[:space:]]*$2=" "$1"; }

keys_of "$TEMPLATE" | sort -u > "$WORK/template-keys"
keys_of "$ENV_FILE" | sort -u > "$WORK/env-keys"
: > "$WORK/carried" ; : > "$WORK/added" ; : > "$WORK/blank"

# Walk the template. Every line is reproduced as it stands except an assignment
# whose key the old .env already had, which takes the old value.
#
# printf, never echo: BusyBox's and dash's echo both expand backslash escapes,
# so a value containing one would arrive in the new .env changed.
: > "$WORK/new-env"
while IFS= read -r line || [ -n "$line" ]; do
    key=$(printf '%s\n' "$line" | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*$/\1/p')
    if [ -z "$key" ] || ! has_key "$ENV_FILE" "$key"; then
        printf '%s\n' "$line" >> "$WORK/new-env"
        [ -z "$key" ] || printf '%s\n' "$key" >> "$WORK/added"
        continue
    fi
    value=$(value_of "$ENV_FILE" "$key")
    printf '%s=%s\n' "$key" "$value" >> "$WORK/new-env"
    printf '%s\n' "$key" >> "$WORK/carried"
    # Worth a look, not worth a refusal: the operator blanked something the
    # release ships a default for, so the default will not apply either.
    template_default=$(value_of "$TEMPLATE" "$key")
    if [ -z "$value" ] && [ -n "$template_default" ]; then
        printf '%s\n' "$key" >> "$WORK/blank"
    fi
done < "$TEMPLATE"

# Anything the operator has that this release no longer ships. Kept, and said
# out loud - a key removed from .env.example is usually a key the code stopped
# reading, but "usually" is not a reason to delete somebody's token.
comm -23 "$WORK/env-keys" "$WORK/template-keys" > "$WORK/orphans"
if [ -s "$WORK/orphans" ]; then
    {
        printf '\n'
        printf '# =============================================================================\n'
        printf '# Kept from your previous .env\n'
        printf '#\n'
        printf '# These keys are not in this release'"'"'s .env.example. They were carried over\n'
        printf '# rather than dropped. Check docs/CONFIGURATION.md: a key that has gone from\n'
        printf '# the template is usually one nothing reads any more, and is safe to delete.\n'
        printf '# =============================================================================\n'
    } >> "$WORK/new-env"
    while IFS= read -r key; do
        printf '%s=%s\n' "$key" "$(value_of "$ENV_FILE" "$key")" >> "$WORK/new-env"
    done < "$WORK/orphans"
fi

count() { [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0; }
list() { sed 's/^/    /' "$1"; }

echo "Template: $TEMPLATE"
echo "Existing: $ENV_FILE"
echo
echo "Carried over from your .env:  $(count "$WORK/carried") key(s)"
if [ -s "$WORK/added" ]; then
    echo "New in this release (left at the shipped default):"
    list "$WORK/added"
else
    echo "New in this release: none - your .env already has every key."
fi
if [ -s "$WORK/orphans" ]; then
    echo "No longer in .env.example (kept at the end of the file):"
    list "$WORK/orphans"
fi
if [ -s "$WORK/blank" ]; then
    echo "Empty in your .env, though the release ships a default - worth a look:"
    list "$WORK/blank"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "--dry-run: nothing was written."
    exit 0
fi

if cmp -s "$WORK/new-env" "$ENV_FILE"; then
    echo
    echo "$ENV_FILE already matches this release's template. Nothing written."
    exit 0
fi

BACKUP="$ENV_FILE.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$ENV_FILE" "$BACKUP"
cat "$WORK/new-env" > "$ENV_FILE.new.$$"
chmod 600 "$ENV_FILE.new.$$"
mv -f "$ENV_FILE.new.$$" "$ENV_FILE"
echo
echo "Wrote $ENV_FILE (mode 600). Previous file kept at $BACKUP."

# The last word belongs to the check the install itself will run, so a missing
# required value is found here rather than three steps into an upgrade.
if [ -f "$SCRIPT_DIR/startup.sh" ]; then
    echo
    if sh "$SCRIPT_DIR/startup.sh" --check-env "$ENV_FILE"; then
        exit 0
    fi
    echo "ERROR: the rebuilt $ENV_FILE is still missing something - fill in the value named above." >&2
    echo "       Your previous file is at $BACKUP." >&2
    exit 1
fi
