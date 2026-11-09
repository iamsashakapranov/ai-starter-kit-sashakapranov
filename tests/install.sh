#!/usr/bin/env bash
# Run isolated integration tests with Python 3.11+.
set -euo pipefail
test_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$candidate" "$test_root/test_install.py" "$@"
  fi
done
printf 'Tests require Python 3.11 or newer.\n' >&2
exit 2
