#!/bin/sh
set -eu

# Xianxia RP transactional local updater (v0.17+).
#
# Usage:
#   ./update.sh                 # look in ./updates for a newer local ZIP (offline)
#   ./update.sh --install       # install newest local ZIP from ./updates
#   ./update.sh --install PATH  # install a specific local ZIP
#   ./update.sh --force PATH    # intentional reinstall/downgrade
#   ./update.sh --check         # ask the release channel (GitHub Releases) - network
#   ./update.sh --fetch         # download + verify the newest release ZIP into ./updates
#   ./update.sh --upgrade       # --fetch, then --install
#   ... --channel stable|beta   # override UPDATE_CHANNEL from .env for this run
#
# Without --check/--fetch/--upgrade no network access is used. .env, data/,
# updates/ and update_backups/ are persistent. A live SQLite backup is created
# through the authoritative Go engine before services are stopped. Failed
# installs restore code + database. The release channel (v0.20.4) is
# UPDATE_REPOSITORY / UPDATE_CHANNEL in .env; a fetched ZIP is verified
# against the release's .sha256 sidecar before it is accepted into ./updates.

# Run from a detached copy so a release can safely replace update.sh itself.
if [ "${XIANXIA_UPDATER_REEXEC:-0}" != "1" ]; then
    ORIGINAL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    RUN_PARENT=$(dirname "${XIANXIA_PROJECT_DIR:-$ORIGINAL_DIR}")
    mkdir -p "$RUN_PARENT"
    RUN_COPY=$(mktemp "$RUN_PARENT/.xianxia-updater-running.XXXXXX")
    cp -p "$0" "$RUN_COPY"
    chmod +x "$RUN_COPY"
    XIANXIA_UPDATER_REEXEC=1 XIANXIA_UPDATER_HOME="$ORIGINAL_DIR" XIANXIA_UPDATER_RUNNING_COPY="$RUN_COPY" \
        exec "$RUN_COPY" "$@"
fi

SCRIPT_DIR=${XIANXIA_UPDATER_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}
PROJECT_DIR=${XIANXIA_PROJECT_DIR:-$SCRIPT_DIR}
UPDATES_DIR=${XIANXIA_UPDATES_DIR:-$PROJECT_DIR/updates}
BACKUP_DIR=${XIANXIA_BACKUP_DIR:-$PROJECT_DIR/update_backups}
DB_PATH=${XIANXIA_DB_PATH:-$PROJECT_DIR/data/xianxia.sqlite3}
VERSION_FILE="$PROJECT_DIR/VERSION"
RUNNING_COPY=${XIANXIA_UPDATER_RUNNING_COPY:-}

MODE=local
REQUESTED_ARCHIVE=""
FORCE=0
CHANNEL_OVERRIDE=""
ROLLBACK_ARMED=0
ROLLBACK_RUNNING=0
STAGING_DIR=""
INSTALL_TREE=""
SNAPSHOT=""
DB_BACKUP_PATH=""
LOCK_DIR=""
NEXT_UPDATER=""

# `--channel X` may follow any of the network modes.
take_channel() {
    if [ "${1:-}" = "--channel" ]; then
        CHANNEL_OVERRIDE=${2:-}
        case "$CHANNEL_OVERRIDE" in stable|beta) ;; *) echo "ERROR: --channel must be stable or beta." >&2; exit 2 ;; esac
        return 2
    fi
    [ $# -eq 0 ] || { echo "ERROR: Unexpected argument: $1" >&2; exit 2; }
    return 0
}

case "${1:-}" in
    "") MODE=local ;;
    --local) MODE=local ;;
    --install)
        MODE=install
        REQUESTED_ARCHIVE=${2:-}
        [ $# -le 2 ] || { echo "ERROR: Too many arguments for --install." >&2; exit 2; }
        ;;
    --force)
        MODE=install; FORCE=1; REQUESTED_ARCHIVE=${2:-}
        [ -n "$REQUESTED_ARCHIVE" ] || { echo "ERROR: --force requires a local ZIP path." >&2; exit 2; }
        [ $# -le 2 ] || { echo "ERROR: Too many arguments for --force." >&2; exit 2; }
        ;;
    --check) MODE=remote_check; shift; take_channel "$@" || true ;;
    --fetch) MODE=fetch; shift; take_channel "$@" || true ;;
    --upgrade) MODE=upgrade; shift; take_channel "$@" || true ;;
    -h|--help)
        sed -n '3,20p' "$0"
        exit 0
        ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
esac

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: Required command '$1' was not found." >&2; exit 1; }; }
for cmd in unzip awk find mktemp cp rm mkdir mv sed tr head basename dirname; do need_cmd "$cmd"; done

clean_version() { printf '%s' "$1" | tr -d '[:space:]' | sed 's/^[vV]//'; }
# A version is X.Y.Z (two to four numeric parts) with an optional prerelease
# suffix: 1.0.0, 1.0.0-rc.9, 0.21.0-beta.1.
valid_version() {
    awk -v v="$1" 'BEGIN {
        core = v; pre = "";
        if (index(v, "-")) { pre = substr(v, index(v, "-") + 1); sub(/-.*$/, "", core) }
        n = split(core, a, ".");
        if (n < 2 || n > 4) exit 1;
        for (i = 1; i <= n; i++) if (a[i] !~ /^[0-9]+$/) exit 1;
        if (index(v, "-")) {
            if (pre == "") exit 1;
            if (pre !~ /^[0-9A-Za-z.-]+$/) exit 1;
            if (pre ~ /^\./ || pre ~ /\.$/ || pre ~ /\.\./) exit 1;
        }
        exit 0
    }'
}
# Semver precedence (semver.org §11), because every rc of a version shares its
# numbers: 1.0.0-rc.6 < 1.0.0-rc.9 < 1.0.0. Comparing the numbers alone - which
# is all this did until v1.0.0-rc.10 - made every rc equal to every other, so
# --fetch answered "already the newest" and the beta channel could be read but
# never walked. Mirrored in app/ops/release_channel.py:precedence(); the two are
# held together by tests/python/unit/test_release_channel.py.
version_gt() {
    awk -v a="$1" -v b="$2" '
    function vcore(v) { sub(/-.*$/, "", v); return v }
    function vpre(v)  { return index(v, "-") ? substr(v, index(v, "-") + 1) : "" }
    function corecmp(x, y,   A, B, na, nb, n, i, p, q) {
        na = split(vcore(x), A, "."); nb = split(vcore(y), B, ".");
        n = (na > nb ? na : nb);
        for (i = 1; i <= n; i++) {
            p = (i <= na ? A[i] + 0 : 0); q = (i <= nb ? B[i] + 0 : 0);
            if (p > q) return 1;
            if (p < q) return -1;
        }
        return 0
    }
    # A release outranks any prerelease of the same numbers; among prereleases,
    # identifiers compare left to right, numeric ones numerically and below
    # alphanumeric ones, and a longer run of identifiers wins a shared prefix.
    function precmp(pa, pb,   A, B, na, nb, n, i, x, y, xn, yn) {
        if (pa == "" && pb == "") return 0;
        if (pa == "") return 1;
        if (pb == "") return -1;
        na = split(pa, A, "."); nb = split(pb, B, ".");
        n = (na < nb ? na : nb);
        for (i = 1; i <= n; i++) {
            x = A[i]; y = B[i];
            xn = (x ~ /^[0-9]+$/); yn = (y ~ /^[0-9]+$/);
            if (xn && yn) { if (x + 0 != y + 0) return (x + 0 > y + 0) ? 1 : -1 }
            else if (xn != yn) { return xn ? -1 : 1 }
            else if (x != y) { return (x > y) ? 1 : -1 }
        }
        if (na == nb) return 0;
        return (na > nb) ? 1 : -1
    }
    function vcmp(x, y,   r) { r = corecmp(x, y); if (r) return r; return precmp(vpre(x), vpre(y)) }
    BEGIN { exit (vcmp(a, b) > 0) ? 0 : 1 }'
}
# RELEASE_TAG first: it is the only thing in the tree that names the rc, and the
# release job stamps it. VERSION is the fallback for an archive built before
# v1.0.0-rc.10 and for a hand-made one.
archive_version() {
    archive=$1
    value=$(unzip -p "$archive" 'RELEASE_TAG' 2>/dev/null | head -n 1 || true)
    [ -n "$value" ] || value=$(unzip -p "$archive" '*/RELEASE_TAG' 2>/dev/null | head -n 1 || true)
    [ -n "$value" ] || value=$(unzip -p "$archive" 'VERSION' 2>/dev/null | head -n 1 || true)
    [ -n "$value" ] || value=$(unzip -p "$archive" '*/VERSION' 2>/dev/null | head -n 1 || true)
    clean_version "$value"
}

cleanup() {
    status=$?
    trap - EXIT INT TERM HUP
    if [ "$status" -ne 0 ] && [ "$ROLLBACK_ARMED" -eq 1 ] && [ "$ROLLBACK_RUNNING" -eq 0 ]; then
        rollback_install || true
    fi
    [ -n "$STAGING_DIR" ] && rm -rf "$STAGING_DIR" 2>/dev/null || true
    [ -n "$INSTALL_TREE" ] && rm -rf "$INSTALL_TREE" 2>/dev/null || true
    [ -n "$LOCK_DIR" ] && rm -rf "$LOCK_DIR" 2>/dev/null || true
    [ -n "$RUNNING_COPY" ] && rm -f "$RUNNING_COPY" 2>/dev/null || true
    exit "$status"
}
trap cleanup EXIT INT TERM HUP

[ -f "$VERSION_FILE" ] || { echo "ERROR: VERSION file not found at: $VERSION_FILE" >&2; exit 1; }
CURRENT_VERSION=$(clean_version "$(cat "$VERSION_FILE")")
valid_version "$CURRENT_VERSION" || { echo "ERROR: Invalid installed VERSION: $CURRENT_VERSION" >&2; exit 1; }
# RELEASE_TAG, when the release job stamped one, is the more precise answer:
# VERSION says 1.0.0 for every 1.0.0 rc. It is only believed when its numbers
# agree with VERSION, so a file left behind by a hand-unpacked older tree
# cannot talk the updater into skipping a real release.
if [ -f "$PROJECT_DIR/RELEASE_TAG" ]; then
    INSTALLED_TAG=$(clean_version "$(head -n 1 "$PROJECT_DIR/RELEASE_TAG" 2>/dev/null || true)")
    if [ -n "$INSTALLED_TAG" ] && valid_version "$INSTALLED_TAG" && [ "${INSTALLED_TAG%%-*}" = "$CURRENT_VERSION" ]; then
        CURRENT_VERSION=$INSTALLED_TAG
    fi
fi

# Normalize persistent directories before deletion logic so custom locations are preserved.
mkdir -p "$UPDATES_DIR" "$BACKUP_DIR"
PROJECT_DIR=$(CDPATH= cd -- "$PROJECT_DIR" && pwd)
UPDATES_DIR=$(CDPATH= cd -- "$UPDATES_DIR" && pwd)
BACKUP_DIR=$(CDPATH= cd -- "$BACKUP_DIR" && pwd)
VERSION_FILE="$PROJECT_DIR/VERSION"
DB_PATH=${XIANXIA_DB_PATH:-$PROJECT_DIR/data/xianxia.sqlite3}
PARENT_DIR=$(dirname "$PROJECT_DIR")

find_latest_update() {
    LATEST_ARCHIVE=""; LATEST_VERSION="$CURRENT_VERSION"
    candidates=$(mktemp "$UPDATES_DIR/.xianxia-update-candidates.XXXXXX")
    find "$UPDATES_DIR" -maxdepth 1 -type f -name '*.zip' -print | while IFS= read -r archive; do
        version=$(archive_version "$archive")
        valid_version "$version" || continue
        printf '%s\t%s\n' "$version" "$archive"
    done > "$candidates"
    tab=$(printf '\t')
    while IFS="$tab" read -r version archive; do
        [ -n "$version" ] || continue
        if version_gt "$version" "$LATEST_VERSION"; then LATEST_VERSION=$version; LATEST_ARCHIVE=$archive; fi
    done < "$candidates"
    rm -f "$candidates"
}

# ---------------------------------------------------------------------------
# Release channel (network only in --check / --fetch / --upgrade)
# ---------------------------------------------------------------------------
# Mirrors app/ops/release_channel.py: stable = GitHub's "latest" (full releases
# only); beta = the newest release in the listing, pre-releases included. The
# archive is xianxia_rp_v<version>.zip with a .sha256 sidecar; both are
# release assets attached by the release job in .github/workflows/ci.yml.
env_value() {
    # $1 = key; from $PROJECT_DIR/.env, ignoring comments, quotes stripped.
    [ -f "$PROJECT_DIR/.env" ] || return 0
    sed -n "s/^[[:space:]]*$1=//p" "$PROJECT_DIR/.env" | tail -n 1 | tr -d '"'"'" | tr -d '[:space:]'
}
UPDATE_REPOSITORY=${XIANXIA_UPDATE_REPOSITORY:-$(env_value UPDATE_REPOSITORY)}
[ -n "$UPDATE_REPOSITORY" ] || UPDATE_REPOSITORY="RhaZenZ0/Xianxia-bot"
UPDATE_CHANNEL=${CHANNEL_OVERRIDE:-${XIANXIA_UPDATE_CHANNEL:-$(env_value UPDATE_CHANNEL)}}
[ -n "$UPDATE_CHANNEL" ] || UPDATE_CHANNEL=stable
case "$UPDATE_CHANNEL" in stable|beta) ;; *) echo "ERROR: UPDATE_CHANNEL must be stable or beta (got: $UPDATE_CHANNEL)." >&2; exit 2 ;; esac
API_BASE=${XIANXIA_UPDATE_API_BASE:-https://api.github.com}

http_get() {
    # $1 = url, $2 = output file. wget is what the NAS has; curl if present.
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 60 -H 'Accept: application/vnd.github+json' -H "User-Agent: xianxia-rp-updater/$CURRENT_VERSION" -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --timeout=60 --header='Accept: application/vnd.github+json' --header="User-Agent: xianxia-rp-updater/$CURRENT_VERSION" -O "$2" "$1"
    else
        echo "ERROR: Neither curl nor wget is available; cannot reach the release channel." >&2
        return 1
    fi
}
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | awk '{print $NF}'
    else echo "ERROR: No sha256 tool (sha256sum/shasum/openssl) is available." >&2; return 1; fi
}
json_field() {
    # $1 = file, $2 = key: the FIRST string value for "key" in the document.
    tr -d '\n' < "$1" | sed -n "s/.*\"$2\":[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}
# The GitHub listing is newest-first; the first release object is the one we
# want for beta. For stable we ask /releases/latest, which GitHub defines as the
# newest non-draft, non-prerelease release - the same rule the bot applies.
resolve_release() {
    RELEASE_TAG=""; RELEASE_VERSION=""; RELEASE_ARCHIVE_URL=""; RELEASE_SHA_URL=""; RELEASE_PAGE=""
    listing=$(mktemp "${TMPDIR:-/tmp}/.xianxia-release.XXXXXX")
    if [ "$UPDATE_CHANNEL" = stable ]; then
        http_get "$API_BASE/repos/$UPDATE_REPOSITORY/releases/latest" "$listing" || { rm -f "$listing"; return 1; }
        # /releases/latest returns a single object; the two-step below turns it into a one-element listing.
        one=$(mktemp "${TMPDIR:-/tmp}/.xianxia-release.XXXXXX"); { printf '['; cat "$listing"; printf ']'; } > "$one"; mv -f "$one" "$listing"
    else
        http_get "$API_BASE/repos/$UPDATE_REPOSITORY/releases?per_page=10" "$listing" || { rm -f "$listing"; return 1; }
    fi
    # Isolate the first release object (up to and including its assets).
    first=$(mktemp "${TMPDIR:-/tmp}/.xianxia-release.XXXXXX")
    # (a sed that deletes from the second release object onward - no newline tricks, so
    # BusyBox sed is fine. Each asset in the release's "assets" array also starts with
    # its own "url" field, so matching bare {"url" would truncate mid-array and drop
    # later assets, incl. the .sha256 sidecar; "assets_url" is the release object's
    # second key and never appears on an asset, so anchor on that pair instead.)
    tr -d '\n' < "$listing" | sed 's/},[[:space:]]*{"url":"[^"]*","assets_url".*$//' > "$first"
    RELEASE_TAG=$(json_field "$first" tag_name)
    RELEASE_PAGE=$(json_field "$first" html_url)
    # The suffix is kept: it is the only thing that tells one rc from another.
    RELEASE_VERSION=$(printf '%s' "$RELEASE_TAG" | sed 's/^[vV]//')
    RELEASE_ARCHIVE_URL=$(tr -d '\n' < "$first" | grep -o '"browser_download_url":[[:space:]]*"[^"]*xianxia_rp_v[0-9.]*\.zip"' | head -n 1 | sed 's/.*"\(http[^"]*\)"/\1/')
    RELEASE_SHA_URL=$(tr -d '\n' < "$first" | grep -o '"browser_download_url":[[:space:]]*"[^"]*xianxia_rp_v[0-9.]*\.zip\.sha256"' | head -n 1 | sed 's/.*"\(http[^"]*\)"/\1/')
    rm -f "$listing" "$first"
    [ -n "$RELEASE_TAG" ] || { echo "ERROR: The release channel returned no release (repository $UPDATE_REPOSITORY, channel $UPDATE_CHANNEL)." >&2; return 1; }
    valid_version "$RELEASE_VERSION" || { echo "ERROR: Release tag '$RELEASE_TAG' is not a version." >&2; return 1; }
    [ -n "$RELEASE_ARCHIVE_URL" ] || { echo "ERROR: Release $RELEASE_TAG has no xianxia_rp_v<version>.zip asset (still building, or hand-made)." >&2; return 1; }
    return 0
}
fetch_release() {
    resolve_release || return 1
    if ! version_gt "$RELEASE_VERSION" "$CURRENT_VERSION"; then
        echo "Installed $CURRENT_VERSION is already the newest on the $UPDATE_CHANNEL channel ($RELEASE_VERSION)."
        return 3
    fi
    # Named for the full tag so two rcs of one version do not overwrite each
    # other in ./updates; the release asset itself keeps its numeric name.
    target="$UPDATES_DIR/xianxia_rp_v$RELEASE_VERSION.zip"
    partial="$target.part"
    echo "Downloading $RELEASE_TAG from $UPDATE_REPOSITORY ($UPDATE_CHANNEL) ..."
    http_get "$RELEASE_ARCHIVE_URL" "$partial" || { rm -f "$partial"; echo "ERROR: Download failed." >&2; return 1; }
    if [ -n "$RELEASE_SHA_URL" ]; then
        sidecar="$target.sha256"
        http_get "$RELEASE_SHA_URL" "$sidecar" || { rm -f "$partial" "$sidecar"; echo "ERROR: Could not download the .sha256 sidecar." >&2; return 1; }
        expected=$(awk '{print $1}' "$sidecar" | head -n 1 | tr 'A-F' 'a-f')
        actual=$(sha256_of "$partial" | tr 'A-F' 'a-f')
        if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
            rm -f "$partial" "$sidecar"
            echo "ERROR: SHA-256 mismatch for $RELEASE_TAG - the download was discarded." >&2
            echo "       expected $expected" >&2; echo "       actual   $actual" >&2
            return 1
        fi
        echo "SHA-256 verified: $actual"
    else
        rm -f "$partial"
        echo "ERROR: Release $RELEASE_TAG has no .sha256 sidecar; refusing an unverifiable archive." >&2
        return 1
    fi
    inner=$(archive_version "$partial")
    # A stamped archive answers with the full tag; one built before
    # v1.0.0-rc.10 has only VERSION and answers with the numbers alone.
    if [ "$inner" != "$RELEASE_VERSION" ] && [ "$inner" != "${RELEASE_VERSION%%-*}" ]; then
        rm -f "$partial"
        echo "ERROR: Archive version ($inner) does not match release tag ($RELEASE_TAG)." >&2
        return 1
    fi
    mv -f "$partial" "$target"
    echo "Fetched: $target"
    FETCHED_ARCHIVE=$target
    return 0
}

if [ "$MODE" = remote_check ]; then
    echo "Xianxia RP installed version: $CURRENT_VERSION"
    echo "Release channel: $UPDATE_REPOSITORY ($UPDATE_CHANNEL)"
    resolve_release || exit 1
    if version_gt "$RELEASE_VERSION" "$CURRENT_VERSION"; then
        echo "UPDATE AVAILABLE: $RELEASE_VERSION ($RELEASE_TAG)"
        [ -n "$RELEASE_PAGE" ] && echo "Release page: $RELEASE_PAGE"
        echo "Fetch with: ./update.sh --fetch    (then ./update.sh --install, or ./update.sh --upgrade in one go)"
    else
        echo "Installed $CURRENT_VERSION is the newest on the $UPDATE_CHANNEL channel ($RELEASE_VERSION)."
    fi
    exit 0
fi

if [ "$MODE" = fetch ] || [ "$MODE" = upgrade ]; then
    FETCHED_ARCHIVE=""
    fetch_release; rc=$?
    [ "$rc" -eq 3 ] && exit 0
    [ "$rc" -eq 0 ] || exit 1
    if [ "$MODE" = fetch ]; then
        echo "Install with: ./update.sh --install"
        exit 0
    fi
    MODE=install; REQUESTED_ARCHIVE=$FETCHED_ARCHIVE
fi

if [ "$MODE" = local ]; then
    find_latest_update
    echo "Xianxia RP installed version: $CURRENT_VERSION"
    echo "Local update folder: $UPDATES_DIR"
    if [ -n "$LATEST_ARCHIVE" ]; then
        echo "UPDATE AVAILABLE: $LATEST_VERSION"
        echo "Package: $LATEST_ARCHIVE"
        echo "Install with: ./update.sh --install"
    else
        echo "No newer local Xianxia RP update was found."
        echo "Place a newer Xianxia RP .zip in ./updates/ and run this script again,"
        echo "or ask the release channel: ./update.sh --check  /  ./update.sh --fetch"
    fi
    exit 0
fi

need_cmd docker
docker compose version >/dev/null 2>&1 || { echo "ERROR: Docker Compose V2 is required." >&2; exit 1; }

LOCK_DIR="$PARENT_DIR/.xianxia-update.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "ERROR: Another Xianxia RP update appears to be running: $LOCK_DIR" >&2
    exit 1
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid" 2>/dev/null || true

if [ -n "$REQUESTED_ARCHIVE" ]; then
    case "$REQUESTED_ARCHIVE" in /*) INSTALL_ARCHIVE=$REQUESTED_ARCHIVE ;; *) INSTALL_ARCHIVE="$PROJECT_DIR/$REQUESTED_ARCHIVE" ;; esac
else
    find_latest_update; INSTALL_ARCHIVE=$LATEST_ARCHIVE
fi
[ -n "${INSTALL_ARCHIVE:-}" ] && [ -f "$INSTALL_ARCHIVE" ] || { echo "ERROR: No local update package is available to install." >&2; exit 1; }

TARGET_VERSION=$(archive_version "$INSTALL_ARCHIVE")
valid_version "$TARGET_VERSION" || { echo "ERROR: ZIP VERSION is missing or malformed: ${TARGET_VERSION:-<empty>}" >&2; exit 1; }
if [ "$FORCE" -ne 1 ] && ! version_gt "$TARGET_VERSION" "$CURRENT_VERSION"; then
    echo "Nothing to install: package $TARGET_VERSION is not newer than installed $CURRENT_VERSION."
    exit 0
fi

echo "Preparing local Xianxia RP update: $CURRENT_VERSION -> $TARGET_VERSION"
echo "Package: $INSTALL_ARCHIVE"

# Reject traversal/absolute-path archives before extraction.
entries=$(mktemp "$PARENT_DIR/.xianxia-zip-entries.XXXXXX")
unzip -Z1 "$INSTALL_ARCHIVE" > "$entries" || { rm -f "$entries"; echo "ERROR: Could not list ZIP contents." >&2; exit 1; }
bad_entry=""
while IFS= read -r entry; do
    case "$entry" in
        /*|../*|*/../*|*/..|..|*\\*) bad_entry=$entry; break ;;
    esac
done < "$entries"
rm -f "$entries"
[ -z "$bad_entry" ] || { echo "ERROR: Unsafe ZIP path rejected: $bad_entry" >&2; exit 1; }

STAGING_DIR=$(mktemp -d "$PARENT_DIR/.xianxia-update-stage.XXXXXX")
unzip -q "$INSTALL_ARCHIVE" -d "$STAGING_DIR"
if [ -f "$STAGING_DIR/VERSION" ]; then NEW_ROOT=$STAGING_DIR; else
    NEW_ROOT=""
    for candidate in "$STAGING_DIR"/*; do
        [ -d "$candidate" ] && [ -f "$candidate/VERSION" ] || continue
        [ -z "$NEW_ROOT" ] || { echo "ERROR: Update ZIP contains multiple possible project roots." >&2; exit 1; }
        NEW_ROOT=$candidate
    done
fi
[ -n "$NEW_ROOT" ] && [ -f "$NEW_ROOT/VERSION" ] || { echo "ERROR: Could not locate project root inside ZIP." >&2; exit 1; }
EXTRACTED_VERSION=$(clean_version "$(cat "$NEW_ROOT/VERSION")")
[ "$EXTRACTED_VERSION" = "$TARGET_VERSION" ] || { echo "ERROR: Extracted VERSION mismatch." >&2; exit 1; }
for required in VERSION startup.sh stop.sh docker-compose.yml go_core app content; do [ -e "$NEW_ROOT/$required" ] || { echo "ERROR: Package missing $required" >&2; exit 1; }; done

# Verify the release manifest before anything is installed. Until v0.19.5 the
# archive shipped a RELEASE_MANIFEST.sha256 that nothing ever checked and that
# had drifted badly (dozens of stale hashes, entries pointing at files that had
# since moved), so it would not have passed anyway. It is generated by
# scripts/release_manifest.py now and checked here, which means a truncated
# download or a partially repacked archive is refused while the running install
# is still completely untouched.
# ONE implementation, used by both the pre-install and post-install checks.
#
# There used to be two, and the BusyBox fix below was applied to this one and
# missed the other - so v0.23.0 verified the downloaded archive correctly and
# then failed the identical check against the installed tree, after the files
# were already in place. Two copies of a check is two places to fix a bug in,
# and this one only got fixed in the place it was noticed.
#
# $1 = directory to verify, $2 = where to write the per-file log,
# $3 = what to say if it fails.
verify_manifest_tree() {
    _dir=$1; _log=$2; _context=$3
    if [ ! -f "$_dir/RELEASE_MANIFEST.sha256" ]; then
        echo "WARNING: No RELEASE_MANIFEST.sha256 in $_dir; skipping integrity check." >&2
        return 0
    fi
    # Only "-c" is portable. --quiet and --strict are GNU coreutils extensions
    # and BusyBox (which is what a QNAP NAS actually provides) rejects them with
    # "unrecognized option", exits non-zero, and makes a perfectly good tree look
    # like a failed integrity check. Per-file "OK" output goes to the log file
    # instead of the terminal, which is all --quiet was buying.
    if command -v sha256sum >/dev/null 2>&1; then
        set -- sha256sum -c RELEASE_MANIFEST.sha256
    elif command -v shasum >/dev/null 2>&1; then
        set -- shasum -a 256 -c RELEASE_MANIFEST.sha256
    else
        echo "WARNING: Neither sha256sum nor shasum is available; skipping integrity check." >&2
        return 0
    fi
    # The manifest deliberately omits .env, data/, caches and itself, so a plain
    # -c run from the tree root is exactly the right check. Both streams are
    # captured together because these tools print FAILED lines on stdout, not
    # stderr - reporting only stderr would hide which files mismatched.
    if ( cd "$_dir" && "$@" ) >"$_log" 2>&1; then
        return 0
    fi
    echo "ERROR: $_context" >&2
    grep -v ': OK$' "$_log" 2>/dev/null | head -n 20 >&2 || true
    return 1
}

verify_release_manifest() {
    verify_manifest_tree "$NEW_ROOT" "$STAGING_DIR/manifest.log" \
        "Release integrity check failed - the package does not match its own manifest.
       Nothing was installed; the running release is untouched." || return 1
    echo "Release integrity verified against RELEASE_MANIFEST.sha256."
}
verify_release_manifest

# Preflight the installed .env against the NEW release's requirements, using
# the new release's own startup.sh in check-only mode, before anything is
# stopped. v0.20.4 on a 0.19.20 install failed at `startup.sh` on a missing
# ENGINE_AUTH_TOKEN and rolled back cleanly - correct, but a stop/rollback/
# restart cycle for what is a one-line .env edit. An older release without
# --check-env starts the stack instead, so only a release that knows the
# flag is asked (v0.20.5).
if grep -q -- '--check-env' "$NEW_ROOT/startup.sh" 2>/dev/null; then
    echo "Checking .env against the requirements of $TARGET_VERSION..."
    if ! (cd "$NEW_ROOT" && sh ./startup.sh --check-env "$PROJECT_DIR/.env"); then
        echo "ERROR: The installed .env does not satisfy Xianxia RP $TARGET_VERSION (see above)." >&2
        echo "       Edit $PROJECT_DIR/.env - compare it with the new .env.example in the package - and run the install again." >&2
        echo "       Nothing was stopped or changed." >&2
        exit 1
    fi
fi

# Validate the complete staged release tree before touching the installed tree.
INSTALL_TREE=$(mktemp -d "$PARENT_DIR/.xianxia-release-$TARGET_VERSION.XXXXXX")
for item in "$NEW_ROOT"/* "$NEW_ROOT"/.[!.]* "$NEW_ROOT"/..?*; do
    [ -e "$item" ] || continue
    name=$(basename "$item")
    case "$name" in .env|data|updates|update_backups) continue ;; esac
    cp -a "$item" "$INSTALL_TREE/"
done
[ -f "$INSTALL_TREE/update.sh" ] && chmod +x "$INSTALL_TREE/update.sh" || true

# Validate Compose with the installed configuration without ever packaging or replacing .env.
STAGED_ENV=0
if [ -f "$PROJECT_DIR/.env" ]; then
    cp -p "$PROJECT_DIR/.env" "$INSTALL_TREE/.env"
    STAGED_ENV=1
fi
# Release-time content validation uses the image's Python source without requiring host Python.
# docker compose config also catches malformed Compose before the old service is stopped.
(cd "$INSTALL_TREE" && docker compose config >/dev/null) || { echo "ERROR: New docker-compose.yml is invalid." >&2; exit 1; }
[ "$STAGED_ENV" -eq 0 ] || rm -f "$INSTALL_TREE/.env"

STAMP=$(date '+%Y%m%d_%H%M%S' 2>/dev/null || date '+%s')
SNAPSHOT="$BACKUP_DIR/xianxia_rp_${CURRENT_VERSION}_before_${TARGET_VERSION}_$STAMP"
mkdir -p "$SNAPSHOT"

echo "Creating code snapshot: $SNAPSHOT"
for old_item in "$PROJECT_DIR"/* "$PROJECT_DIR"/.[!.]* "$PROJECT_DIR"/..?*; do
    [ -e "$old_item" ] || continue
    name=$(basename "$old_item")
    case "$name" in .env|data|updates|update_backups) continue ;; esac
    # Preserve custom update/backup trees even when they live under an unusual top-level directory.
    case "$UPDATES_DIR/" in "$old_item"/*) continue ;; esac
    case "$BACKUP_DIR/" in "$old_item"/*) continue ;; esac
    cp -a "$old_item" "$SNAPSHOT/"
done

create_database_backup() {
    [ -f "$DB_PATH" ] || return 0
    echo "Creating transaction-safe SQLite backup through the Go engine..."
    # The engine has required X-Xianxia-Engine-Token on every /v1/ call since
    # 0.20.0; the container holds the token in its own environment, so the
    # request is built inside it (v0.20.9 - before that this was an unauthenticated
    # POST that a tokened engine answered 401, and every update from a 0.20
    # engine stopped here with "Could not create a safe SQLite backup").
    response=$(cd "$PROJECT_DIR" && docker compose exec -T xianxia-engine sh -c \
        'wget -q -O - --header="Content-Type: application/json" --header="X-Xianxia-Engine-Token: $ENGINE_AUTH_TOKEN" --post-data="{}" http://127.0.0.1:8081/v1/db/backups' 2>/dev/null || true)
    name=$(printf '%s' "$response" | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    [ -n "$name" ] || {
        echo "ERROR: Could not create a safe SQLite backup. Start the current stack and retry." >&2
        echo "       Refusing to update a live/possibly-WAL database without the engine backup API." >&2
        return 1
    }
    DB_BACKUP_PATH="$PROJECT_DIR/data/backups/$name"
    [ -s "$DB_BACKUP_PATH" ] || { echo "ERROR: Engine reported backup $name but the file is missing/empty." >&2; return 1; }
    echo "Database backup: $DB_BACKUP_PATH"
}
create_database_backup

# Off-box copy (v0.32.0). A backup on the disk that holds the database
# protects against a bad update, not against the disk. XIANXIA_OFFBOX_BACKUP_DIR
# (environment or .env) names a second place - a mounted share, a USB disk -
# and the pre-update backup is copied there too. It is sealed if the engine
# seals backups (XIANXIA_BACKUP_KEY), so the copy is safe to leave on a
# share; `xianxia-engine decrypt-backup` opens it. A copy that fails is said
# out loud and does not stop the update: the local backup is still there and
# the rollback below still works from it.
copy_backup_offbox() {
    [ -n "$DB_BACKUP_PATH" ] || return 0
    target=${XIANXIA_OFFBOX_BACKUP_DIR:-$(env_value XIANXIA_OFFBOX_BACKUP_DIR)}
    [ -n "$target" ] || return 0
    if mkdir -p "$target" 2>/dev/null && cp -p "$DB_BACKUP_PATH" "$target/" 2>/dev/null; then
        echo "Off-box copy: $target/$(basename "$DB_BACKUP_PATH")"
    else
        echo "WARNING: could not copy the backup to XIANXIA_OFFBOX_BACKUP_DIR=$target (is it mounted and writable?). Continuing with the local copy only." >&2
    fi
}
copy_backup_offbox

restore_code() {
    echo "Restoring code snapshot..." >&2
    for old_item in "$PROJECT_DIR"/* "$PROJECT_DIR"/.[!.]* "$PROJECT_DIR"/..?*; do
        [ -e "$old_item" ] || continue
        name=$(basename "$old_item")
        case "$name" in .env|data|updates|update_backups) continue ;; esac
        case "$UPDATES_DIR/" in "$old_item"/*) continue ;; esac
        case "$BACKUP_DIR/" in "$old_item"/*) continue ;; esac
        rm -rf "$old_item"
    done
    for item in "$SNAPSHOT"/* "$SNAPSHOT"/.[!.]* "$SNAPSHOT"/..?*; do [ -e "$item" ] && cp -a "$item" "$PROJECT_DIR/"; done
}
restore_database() {
    [ -n "$DB_BACKUP_PATH" ] || return 0
    echo "Restoring SQLite backup..." >&2
    case "$DB_BACKUP_PATH" in
        *.enc)
            # A sealed backup (XIANXIA_BACKUP_KEY, v0.32.0) is opened by the
            # engine image's own binary - the stack is down at this point, so
            # it runs as a one-off container over the same ./data mount, with
            # the key from the installed .env.
            echo "The backup is encrypted; opening it with the engine image..." >&2
            if ! (cd "$PROJECT_DIR" && docker compose run --rm --no-deps -T --entrypoint /usr/local/bin/xianxia-engine xianxia-engine \
                    decrypt-backup "/data/backups/$(basename "$DB_BACKUP_PATH")" /data/xianxia.restore.sqlite3 >/dev/null 2>&1) \
               || [ ! -s "$PROJECT_DIR/data/xianxia.restore.sqlite3" ]; then
                echo "ERROR: could not decrypt $DB_BACKUP_PATH. Check XIANXIA_BACKUP_KEY in .env, then run:" >&2
                echo "       docker compose run --rm --no-deps --entrypoint /usr/local/bin/xianxia-engine xianxia-engine decrypt-backup /data/backups/$(basename "$DB_BACKUP_PATH") /data/xianxia.sqlite3" >&2
                return 1
            fi
            mv -f "$PROJECT_DIR/data/xianxia.restore.sqlite3" "$DB_PATH"
            ;;
        *)
            cp -a "$DB_BACKUP_PATH" "$DB_PATH"
            ;;
    esac
    rm -f "$DB_PATH-wal" "$DB_PATH-shm"
}
rollback_install() {
    ROLLBACK_RUNNING=1
    echo "Update failed; rolling back Xianxia RP $TARGET_VERSION -> $CURRENT_VERSION." >&2
    (cd "$PROJECT_DIR" && docker compose --profile dashboard down --remove-orphans >/dev/null 2>&1) || true
    restore_code
    restore_database
    chmod +x "$PROJECT_DIR/startup.sh" "$PROJECT_DIR/stop.sh" "$PROJECT_DIR/update.sh" 2>/dev/null || true
    if [ -x "$PROJECT_DIR/startup.sh" ]; then
        echo "Restarting restored Xianxia RP $CURRENT_VERSION..." >&2
        "$PROJECT_DIR/startup.sh" >/dev/null 2>&1 || echo "WARNING: rollback restored files but restart failed; inspect Docker logs." >&2
    fi
    ROLLBACK_ARMED=0
    ROLLBACK_RUNNING=0
}

# Validation + both backups are complete. From this point, any error rolls back.
ROLLBACK_ARMED=1
if [ -x "$PROJECT_DIR/stop.sh" ]; then echo "Stopping Xianxia RP..."; "$PROJECT_DIR/stop.sh"; else (cd "$PROJECT_DIR" && docker compose --profile dashboard down --remove-orphans); fi

echo "Installing Xianxia RP $TARGET_VERSION..."
for old_item in "$PROJECT_DIR"/* "$PROJECT_DIR"/.[!.]* "$PROJECT_DIR"/..?*; do
    [ -e "$old_item" ] || continue
    name=$(basename "$old_item")
    case "$name" in .env|data|updates|update_backups|update.sh) continue ;; esac
    case "$UPDATES_DIR/" in "$old_item"/*) continue ;; esac
    case "$BACKUP_DIR/" in "$old_item"/*) continue ;; esac
    # A delete or copy that fails (a tree owned by another NAS account is the
    # case seen) must abort into the rollback: before v0.20.9 the loop carried
    # on and left the top-level files of the new release on the old code,
    # with a VERSION that then told the updater there was nothing to install.
    rm -rf "$old_item" || { echo "ERROR: Could not remove $old_item (permissions? the account running update.sh must own the project folder)." >&2; exit 1; }
    [ ! -e "$old_item" ] || { echo "ERROR: $old_item is still present after removal (permissions? the account running update.sh must own the project folder)." >&2; exit 1; }
done
for item in "$INSTALL_TREE"/* "$INSTALL_TREE"/.[!.]* "$INSTALL_TREE"/..?*; do
    [ -e "$item" ] || continue
    name=$(basename "$item")
    case "$name" in .env|data|updates|update_backups|update.sh) continue ;; esac
    cp -a "$item" "$PROJECT_DIR/" || { echo "ERROR: Could not copy $name into $PROJECT_DIR." >&2; exit 1; }
done
if [ -f "$INSTALL_TREE/update.sh" ]; then NEXT_UPDATER="$PROJECT_DIR/.update.sh.next"; cp -a "$INSTALL_TREE/update.sh" "$NEXT_UPDATER"; chmod +x "$NEXT_UPDATER"; fi
chmod +x "$PROJECT_DIR/startup.sh" "$PROJECT_DIR/stop.sh" 2>/dev/null || true

INSTALLED_VERSION=$(clean_version "$(cat "$PROJECT_DIR/VERSION")")
[ "$INSTALLED_VERSION" = "$TARGET_VERSION" ] || { echo "ERROR: Post-install VERSION check failed." >&2; exit 1; }
# ... and the whole tree, not just VERSION: every file the release manifest
# names must be in place, byte for byte, before anything is started.
verify_manifest_tree "$PROJECT_DIR" "${STAGING_DIR:-$PARENT_DIR}/post-install-manifest.log" \
    "Post-install tree does not match RELEASE_MANIFEST.sha256." || exit 1

echo "Starting Xianxia RP $TARGET_VERSION..."
"$PROJECT_DIR/startup.sh"

container_ready() {
    name=$1
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || true)
    [ "$state" = healthy ] || [ "$state" = running ]
}
wait_for_release_health() {
    attempts=${XIANXIA_HEALTH_ATTEMPTS:-60}; delay=${XIANXIA_HEALTH_INTERVAL_SECONDS:-2}; i=0
    while [ "$i" -lt "$attempts" ]; do
        if container_ready xianxia-game-engine && container_ready xianxia-roleplay-bot; then
            dashboard_enabled=$(sed -n 's/^[[:space:]]*DASHBOARD_ENABLED=//p' "$PROJECT_DIR/.env" 2>/dev/null | tail -n1 | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
            case "$dashboard_enabled" in 1|true|yes|on) container_ready xianxia-rp-dashboard || { i=$((i+1)); sleep "$delay"; continue; } ;; esac
            return 0
        fi
        i=$((i+1)); sleep "$delay"
    done
    return 1
}
wait_for_release_health || { echo "ERROR: New release did not become healthy." >&2; exit 1; }

# Commit point. The running script is the detached copy, so replacing update.sh is safe.
ROLLBACK_ARMED=0
if [ -n "$NEXT_UPDATER" ] && [ -f "$NEXT_UPDATER" ]; then mv -f "$NEXT_UPDATER" "$PROJECT_DIR/update.sh"; chmod +x "$PROJECT_DIR/update.sh"; fi

echo "Xianxia RP update complete: $CURRENT_VERSION -> $TARGET_VERSION"
echo "Code rollback snapshot: $SNAPSHOT"
[ -n "$DB_BACKUP_PATH" ] && echo "Database rollback snapshot: $DB_BACKUP_PATH"
echo ".env, data/, updates/ and update_backups/ were preserved."
exit 0
