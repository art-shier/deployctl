#!/usr/bin/env bash
set -euo pipefail
test_directory=$(mktemp -d)
trap 'rm -rf -- "$test_directory"' EXIT
version=$(python3 dist/deployctl.pyz --version)
bash install.sh dist/deployctl.pyz --install-dir "$test_directory/bin"
test "$("$test_directory/bin/deployctl" --version)" = "$version"
test "$("$test_directory/bin/ctl" --version)" = "$version"
bash install.sh dist/deployctl.pyz --install-dir "$test_directory/bin"
cp dist/deployctl.pyz "$test_directory/bad.pyz"
printf '%064d\n' 0 > "$test_directory/bad.pyz.sha256"
if bash install.sh "$test_directory/bad.pyz" --install-dir "$test_directory/bin"; then
  echo 'Corrupt artifact incorrectly installed' >&2
  exit 1
fi
test "$("$test_directory/bin/ctl" --version)" = "$version"
mkdir "$test_directory/project"
printf 'FROM python:3.12-slim\n' > "$test_directory/project/Dockerfile"
"$test_directory/bin/ctl" init example --directory "$test_directory/project" \
  --platform-repository art-shier/deployctl --platform-ref "v$version" --private-platform
"$test_directory/bin/ctl" validate "$test_directory/project/deploy/deployment.yaml"
echo 'PASS: real installer, aliases, reinstall, checksum failure and init'
