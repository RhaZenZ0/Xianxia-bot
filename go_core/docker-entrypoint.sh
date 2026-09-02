#!/bin/sh
set -eu

# docker-compose's "./data:/data" bind mount is created by the Docker daemon
# on the host - typically root-owned, mode 0755 - the first time the stack
# starts. The engine deliberately runs as a non-root "xianxia" user (see
# go_core/Dockerfile), and that user's UID isn't something a NAS operator can
# predict or chown a host folder to ahead of time. Without this step,
# sqlite3_open_v2 fails with "unable to open database file" the moment the
# engine tries to create the database inside a directory it can't write to.
#
# So: start as root (the image's actual default user - USER is intentionally
# not set in the Dockerfile), make sure the directory that will hold the
# SQLite database (and its -wal/-shm files) is owned by the account the
# engine actually runs as, then drop privileges before exec'ing the real
# binary. This is the only step that ever runs as root, and it never runs
# application code.
DB_DIR=$(dirname "${DATABASE_PATH:-/data/xianxia.sqlite3}")
mkdir -p "$DB_DIR"
chown -R xianxia:xianxia "$DB_DIR"
exec gosu xianxia "$@"
