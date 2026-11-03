#!/usr/bin/env bash
# CI-only Linux client setup for workspace integration restore checks.
set -euo pipefail
if [ ! -x /usr/lib/postgresql/17/bin/pg_dump ] || [ ! -x /usr/lib/postgresql/17/bin/pg_restore ]; then
  sudo apt-get update
  sudo apt-get install -y curl ca-certificates gpg
  sudo install -d /usr/share/postgresql-common/pgdg
  sudo curl --fail --silent --show-error -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
  test "$(gpg --show-keys --with-colons /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc | awk -F: '$1=="fpr"{print $10;exit}')" = B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
  . /etc/os-release
  printf 'Types: deb\nURIs: https://apt.postgresql.org/pub/repos/apt\nSuites: %s-pgdg\nComponents: main\nSigned-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc\n' "$VERSION_CODENAME" | sudo tee /etc/apt/sources.list.d/pgdg.sources
  sudo apt-get update
  sudo apt-get install -y postgresql-client-17
fi
test -x /usr/lib/postgresql/17/bin/pg_dump
test -x /usr/lib/postgresql/17/bin/pg_restore
