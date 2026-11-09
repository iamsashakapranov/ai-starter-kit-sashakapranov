#!/usr/bin/env bash
# Isolated installer checks. Every invocation uses --target inside this fixture.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
scratch=$(mktemp -d "${TMPDIR:-/tmp}/starter-kit-test.XXXXXX")
scratch=$(cd -- "$scratch" && pwd -P)
trap 'rm -rf -- "$scratch"' EXIT
fixture="$scratch/source kit"
mkdir -p "$fixture/agents" "$fixture/skills/example/references"
cp "$repo/install.sh" "$fixture/install.sh"
printf 'agent alpha\n' > "$fixture/agents/alpha.md"
printf 'agent beta\n' > "$fixture/agents/beta.md"
printf 'example skill\n' > "$fixture/skills/example/SKILL.md"
printf 'reference\n' > "$fixture/skills/example/references/example.md"
printf 'routing\n' > "$fixture/ROUTING.md"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
same() { cmp -s "$1" "$2" || fail "Files differ: $1 / $2"; }
run() { bash "$fixture/install.sh" --target "$target" "$@" > "$scratch/output" 2>&1; }
must_fail() { if run "$@"; then fail "Expected failure: $*"; fi; }
snapshot() { find "$1" -type f -exec cksum {} \; | LC_ALL=C sort; }

# Fresh install and dynamic discovery, including nested references and spaces.
target="$scratch/fresh target"
run
for relative in agents/alpha.md agents/beta.md skills/example/SKILL.md skills/example/references/example.md ROUTING.md; do
  same "$fixture/$relative" "$target/$relative"
done
printf 'PASS: fresh install and dynamic file discovery\n'

# A repeat must not touch existing files or create backups; foreign files survive.
printf 'user notes\n' > "$target/agents/custom.md"
mkdir -p "$target/skills/old-package"
printf 'old package\n' > "$target/skills/old-package/SKILL.md"
snapshot "$target" > "$scratch/before"
run
snapshot "$target" > "$scratch/after"
same "$scratch/before" "$scratch/after"
[[ ! -e "$target/.starter-kit-backups" ]] || fail 'Repeat created backups'
printf 'PASS: repeat install and preservation of user files/old packages\n'

# Conflict preflight must not install even an unrelated missing file.
printf 'locally changed\n' > "$target/ROUTING.md"
printf 'new agent\n' > "$fixture/agents/new.md"
snapshot "$target" > "$scratch/before"
must_fail
snapshot "$target" > "$scratch/after"
same "$scratch/before" "$scratch/after"
[[ ! -e "$target/agents/new.md" ]] || fail 'Conflict preflight wrote a new file'
printf 'PASS: conflict stops before all writes\n'

# Dry-run with replacement neither creates backups nor changes existing files.
run --replace --dry-run
snapshot "$target" > "$scratch/after"
same "$scratch/before" "$scratch/after"
[[ ! -e "$target/.starter-kit-backups" ]] || fail 'Dry-run created backups'
fresh_target="$scratch/absent/target"
target_saved=$target
target=$fresh_target
run --dry-run
[[ ! -e "$scratch/absent" ]] || fail 'Dry-run created parent directories'
target=$target_saved
printf 'PASS: dry-run writes nothing for existing and absent targets\n'

# Explicit replacement saves exact previous content, keeps unrelated files.
cp "$target/ROUTING.md" "$scratch/old-routing"
run --replace
same "$fixture/ROUTING.md" "$target/ROUTING.md"
backup_files=("$target"/.starter-kit-backups/*/ROUTING.md)
[[ ${#backup_files[@]} -eq 1 ]] || fail 'Expected one replacement backup'
same "$scratch/old-routing" "${backup_files[0]}"
[[ -f "$target/agents/custom.md" && -f "$target/skills/old-package/SKILL.md" ]] || fail 'Replacement removed user files'
same "$fixture/agents/new.md" "$target/agents/new.md"
printf 'PASS: explicit replacement preserves backup and user files\n'

# A hard-linked old file must not cause overwrite of its external sibling.
printf 'hard-linked original\n' > "$scratch/external-hardlink"
rm "$target/ROUTING.md"
ln "$scratch/external-hardlink" "$target/ROUTING.md"
cp "$scratch/external-hardlink" "$scratch/hardlink-before"
run --replace
same "$scratch/hardlink-before" "$scratch/external-hardlink"
same "$fixture/ROUTING.md" "$target/ROUTING.md"
printf 'PASS: replacement does not modify hard-link siblings\n'

# Reject symlink files, symlink directories and a symlink target, even --replace.
mkdir -p "$scratch/outside"
printf 'outside\n' > "$scratch/outside/original.md"
cp "$scratch/outside/original.md" "$scratch/outside-before"
target="$scratch/symlink-file"
mkdir -p "$target/agents"
ln -s "$scratch/outside/original.md" "$target/agents/alpha.md"
must_fail --replace
[[ ! -e "$target/ROUTING.md" ]] || fail 'Symlink preflight wrote files'
same "$scratch/outside-before" "$scratch/outside/original.md"
target="$scratch/symlink-directory"
mkdir -p "$target"
ln -s "$scratch/outside" "$target/agents"
must_fail --replace
[[ ! -e "$scratch/outside/alpha.md" ]] || fail 'Followed symlink directory'
target="$scratch/symlink-target"
ln -s "$scratch/outside" "$target"
must_fail --replace
target="$scratch/dangling-link"
ln -s "$scratch/missing" "$target"
must_fail
[[ ! -e "$scratch/missing" ]] || fail 'Followed dangling symlink'
printf 'PASS: file, directory, root and dangling symlinks rejected\n'

# A backup-directory symlink is also a conflict before any replacement writes.
target="$scratch/backup-link"
run
printf 'local routing\n' > "$target/ROUTING.md"
cp "$target/ROUTING.md" "$scratch/backup-link-before"
ln -s "$scratch/outside" "$target/.starter-kit-backups"
must_fail --replace
same "$scratch/backup-link-before" "$target/ROUTING.md"
printf 'PASS: backup-directory symlink rejected\n'

# Incompatible destination types and unsupported source links fail preflight.
target="$scratch/directory-conflict"
mkdir -p "$target/agents/alpha.md"
must_fail --replace
[[ ! -e "$target/ROUTING.md" ]] || fail 'Type conflict wrote files'
target="$scratch/source-link"
ln -s "$scratch/outside/original.md" "$fixture/skills/example/link.md"
must_fail
[[ ! -e "$target" ]] || fail 'Source symlink wrote target'
printf 'PASS: incompatible types and source symlinks rejected\n'

printf 'All installer checks passed.\n'
