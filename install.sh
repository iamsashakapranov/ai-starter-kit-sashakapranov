#!/usr/bin/env bash
# Install the files shipped with this checkout; never remove user files.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash install.sh [--target DIR] [--dry-run] [--replace]

  --target DIR  Install into DIR instead of ~/.claude.
  --dry-run     Check and show the plan without creating or changing files.
  --replace     Back up conflicting regular files, then replace them.
  --help        Show this help.

Identical files are skipped. Conflicts stop installation before any writes
unless --replace is given. Symlinks in destination paths are rejected.
Other files and previously installed packages are never removed.
EOF
}

fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }

target="${HOME:?HOME is not set}/.claude"
dry_run=false
replace=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail '--target требует путь.'
      target=$2
      shift 2
      ;;
    --dry-run) dry_run=true; shift ;;
    --replace) replace=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Неизвестный аргумент: $1" ;;
  esac
done

source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ "$target" == /* ]] || target="$PWD/$target"
while [[ "$target" != / && "$target" == */ ]]; do target=${target%/}; done
[[ "$target" != / ]] || fail 'Корень файловой системы нельзя использовать как --target.'

# Check every existing ancestor before mkdir/cp. This also catches dangling links.
check_directory_path() {
  local path=$1
  while [[ "$path" != / && -n "$path" ]]; do
    [[ ! -L "$path" ]] || fail "Ссылка в пути назначения: $path. Укажите обычный каталог (физический путь)."
    [[ ! -e "$path" || -d "$path" ]] || fail "Вместо каталога найден файл: $path"
    path=${path%/*}
  done
}
check_directory_path "$target"

for directory in agents skills; do
  [[ -d "$source_root/$directory" && ! -L "$source_root/$directory" ]] || fail "Нет исходного каталога: $directory"
done
[[ -f "$source_root/ROUTING.md" && ! -L "$source_root/ROUTING.md" ]] || fail 'Нет обычного файла ROUTING.md.'
while IFS= read -r -d '' unsupported; do
  fail "В исходном наборе неподдерживаемый тип файла: $unsupported"
done < <(find "$source_root/agents" "$source_root/skills" ! -type d ! -type f -print0)

files=()
actions=()
while IFS= read -r -d '' source_file; do
  files+=("${source_file#"$source_root/"}")
done < <(find "$source_root/agents" "$source_root/skills" -type f -print0)
files+=(ROUTING.md)

new_count=0
replace_count=0
same_count=0
for relative in "${files[@]}"; do
  destination="$target/$relative"
  check_directory_path "${destination%/*}"
  [[ ! -L "$destination" ]] || fail "Ссылка вместо файла назначения: $destination"
  if [[ -e "$destination" ]]; then
    [[ -f "$destination" ]] || fail "Назначение не является обычным файлом: $destination"
    if cmp -s "$source_root/$relative" "$destination"; then
      actions+=(same)
      same_count=$((same_count + 1))
    else
      "$replace" || fail "Конфликт: $destination. Ничего не изменено; для замены с резервной копией используйте --replace."
      actions+=(replace)
      replace_count=$((replace_count + 1))
    fi
  else
    actions+=(new)
    new_count=$((new_count + 1))
  fi
done
if [[ $replace_count -gt 0 ]]; then
  check_directory_path "$target/.starter-kit-backups"
fi

printf 'Каталог: %s\nФайлов: новых %s, заменить %s, без изменений %s.\n' "$target" "$new_count" "$replace_count" "$same_count"
for ((i=0; i<${#files[@]}; i++)); do
  [[ "${actions[i]}" == same ]] || printf '  %s: %s\n' "${actions[i]}" "${files[i]}"
done
if "$dry_run"; then
  printf 'Проверка завершена (--dry-run); файлы не изменены.\n'
  exit 0
fi
if [[ $new_count -eq 0 && $replace_count -eq 0 ]]; then
  printf 'Набор уже установлен; изменений нет.\n'
  exit 0
fi

# Finish all backups before changing any installed file.
if [[ $replace_count -gt 0 ]]; then
  mkdir -p "$target/.starter-kit-backups"
  backup=$(mktemp -d "$target/.starter-kit-backups/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
  printf 'Резервная копия: %s\n' "$backup"
  for ((i=0; i<${#files[@]}; i++)); do
    if [[ "${actions[i]}" == replace ]]; then
      relative=${files[i]}
      mkdir -p "$backup/$(dirname -- "$relative")"
      cp -p "$target/$relative" "$backup/$relative"
    fi
  done
fi

temporary_file=''
trap '[[ -z "$temporary_file" ]] || rm -f -- "$temporary_file"' EXIT
for ((i=0; i<${#files[@]}; i++)); do
  [[ "${actions[i]}" != same ]] || continue
  relative=${files[i]}
  destination="$target/$relative"
  check_directory_path "${destination%/*}"
  [[ ! -L "$destination" ]] || fail "Ссылка вместо файла назначения: $destination"
  mkdir -p "${destination%/*}"
  temporary_file=$(mktemp "${destination%/*}/.starter-kit-install.XXXXXX")
  cp -p "$source_root/$relative" "$temporary_file"
  # Rename a new inode rather than overwrite a possibly hard-linked user file.
  mv -f "$temporary_file" "$destination"
  temporary_file=''
done
printf 'Готово. Чужие файлы и ранее установленные пакеты сохранены.\n'
printf 'Карта выбора инструментов: %s/ROUTING.md\n' "$target"
printf 'Перезапустите Claude Code, чтобы он перечитал набор.\n'
