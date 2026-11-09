#!/usr/bin/env python3
"""Install the shared sources as native Claude Code and/or Codex helpers."""
import sys

if sys.version_info < (3, 11):
    print("Нужен Python 3.11 или новее. Используйте bash install.sh или python3.11+.", file=sys.stderr)
    raise SystemExit(2)

sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone


class InstallError(Exception):
    pass


def fail(message):
    raise InstallError(message)


def plain_files(directory):
    """Validate the source tree without following symlinks."""
    if directory.is_symlink() or not directory.is_dir():
        fail(f"Нет обычного исходного каталога: {directory}")
    result = []
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            result.append(path)
        elif not stat.S_ISDIR(mode):
            fail(f"Неподдерживаемый тип исходника (включая ссылки): {path}")
    return result


def scalar(value, path):
    """Only the repository's flat, one-line YAML string subset is supported."""
    value = value.strip()
    if not value or value in {"|", ">"} or value.startswith(("[", "{", "&", "*", "!")):
        fail(f"Ожидалась однострочная строка frontmatter: {path}")
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except ValueError:
            fail(f"Некорректная строка в двойных кавычках: {path}")
    elif value.startswith("'"):
        if not value.endswith("'"):
            fail(f"Незакрытая строка frontmatter: {path}")
        value = value[1:-1].replace("''", "'")
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        fail(f"Ожидалась непустая однострочная строка: {path}")
    return value


def read_agent(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        fail(f"Нет frontmatter агента: {path}")
    metadata = {}
    end = None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = index
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace() or ":" not in line:
            fail(f"Поддерживается только плоский frontmatter: {path}")
        key, value = line.split(":", 1)
        if key not in {"name", "description", "tools", "model", "effort"} or key in metadata:
            fail(f"Неизвестное или повторное поле frontmatter {key!r}: {path}")
        metadata[key] = scalar(value, path)
    if end is None or not {"name", "description"} <= metadata.keys():
        fail(f"Агенту нужны name, description и закрытый frontmatter: {path}")
    name = metadata["name"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or name != path.stem:
        fail(f"Имя агента должно совпадать с именем файла: {path}")
    body = "".join(lines[end + 1:]).strip()
    if not body:
        fail(f"Пустая инструкция агента: {path}")
    return name, metadata["description"], body


def native_toml(name, description, body):
    # JSON basic strings use escapes that are valid in TOML basic strings, too.
    values = {"name": name, "description": description, "developer_instructions": body}
    text = "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in values.items()) + "\n"
    if tomllib.loads(text) != values:
        fail(f"Ошибка преобразования TOML: {name}")
    return text.encode("utf-8")


def absolute(path):
    # Do not resolve symlinks: they must be detected, not silently followed.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def check_path(path, *, file=False):
    for parent in reversed(path.parents):
        if parent.is_symlink():
            fail(f"Ссылка в пути назначения: {parent}. Укажите физический путь.")
        if parent.exists() and not parent.is_dir():
            fail(f"Вместо каталога найден файл: {parent}")
    if path.is_symlink():
        fail(f"Ссылка в пути назначения: {path}. Укажите физический путь.")
    if path.exists() and not (path.is_file() if file else path.is_dir()):
        fail(f"Несовместимый тип назначения: {path}")


@dataclass
class Entry:
    destination: Path
    backup_name: Path
    data: bytes
    mode: int
    old: bytes | None = None
    old_mode: int | None = None
    action: str = "new"


def make_plan(source, clients, root, explicit_target):
    agents = plain_files(source / "agents")
    if not agents or any(path.parent != source / "agents" or path.suffix != ".md" for path in agents):
        fail("Исходные агенты должны быть обычными файлами agents/*.md.")
    parsed = [(path, read_agent(path)) for path in agents]
    skill_files = plain_files(source / "skills")
    if not skill_files:
        fail("В исходном наборе нет навыков.")
    for path in skill_files:
        relative = path.relative_to(source / "skills")
        if len(relative.parts) < 2 or not (source / "skills" / relative.parts[0] / "SKILL.md").is_file():
            fail(f"Навыку нужен файл SKILL.md в его каталоге: {path}")
    routing = source / "ROUTING.md"
    if routing.is_symlink() or not routing.is_file():
        fail("Нет обычного исходного файла ROUTING.md.")

    def client_root(variable, fallback):
        return absolute(fallback if explicit_target else os.environ.get(variable) or fallback)

    destinations = {
        "claude": client_root("CLAUDE_CONFIG_DIR", root / ".claude"),
        "codex": client_root("CODEX_HOME", root / ".codex"),
    }
    entries = []
    for client in clients:
        base = destinations[client]
        if base == Path(base.anchor):
            fail("Корень файловой системы нельзя использовать как каталог клиента.")
        check_path(base)
        for path, (name, description, body) in parsed:
            suffix = ".md" if client == "claude" else ".toml"
            data = path.read_bytes() if client == "claude" else native_toml(name, description, body)
            mode = stat.S_IMODE(path.stat().st_mode) if client == "claude" else 0o644
            relative = Path("agents") / (name + suffix)
            entries.append(Entry(base / relative, Path(client) / relative, data, mode))
        entries.append(Entry(base / "ROUTING.md", Path(client) / "ROUTING.md", routing.read_bytes(), 0o644))
        skills_base = base / "skills" if client == "claude" else root / ".agents" / "skills"
        label = Path(client) / "skills" if client == "claude" else Path("agents") / "skills"
        for path in skill_files:
            relative = path.relative_to(source / "skills")
            entries.append(Entry(skills_base / relative, label / relative, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)))
    unique = {}
    for entry in entries:
        previous = unique.get(entry.destination)
        if previous and previous.data != entry.data:
            fail(f"Два разных исходника назначены в один файл: {entry.destination}")
        unique.setdefault(entry.destination, entry)
    for destination in unique:
        if any(parent in unique for parent in destination.parents):
            fail(f"Пути клиентов пересекаются: файл используется как каталог для {destination}")
    return list(unique.values())


def preflight(entries, replace):
    for entry in entries:
        check_path(entry.destination, file=True)
        if entry.destination.exists():
            entry.old = entry.destination.read_bytes()
            entry.old_mode = stat.S_IMODE(entry.destination.stat().st_mode)
            entry.action = "same" if entry.old == entry.data else "replace"
            if entry.action == "replace" and not replace:
                fail(f"Конфликт: {entry.destination}. Ничего не изменено; для замены с резервной копией используйте --replace.")


def atomic_write(destination, data, mode):
    check_path(destination, file=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".starter-kit-install-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(entries, backup_root):
    changed = [entry for entry in entries if entry.action != "same"]
    # Recheck the entire plan before making any backup or installation write.
    for entry in entries:
        check_path(entry.destination, file=True)
        current = entry.destination.read_bytes() if entry.destination.exists() else None
        if current != entry.old:
            fail(f"Файл изменился после проверки: {entry.destination}. Повторите запуск.")
    replacements = [entry for entry in changed if entry.action == "replace"]
    if replacements:
        check_path(backup_root)
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        backup = Path(tempfile.mkdtemp(prefix=prefix, dir=backup_root))
        print(f"Резервная копия: {backup}")
        for entry in replacements:
            atomic_write(backup / entry.backup_name, entry.old, entry.old_mode)
    for entry in changed:
        atomic_write(entry.destination, entry.data, entry.mode)


def main():
    parser = argparse.ArgumentParser(description="Установка нативных помощников Claude Code и Codex. Требуется Python 3.11+.")
    parser.add_argument("--client", required=True, choices=("claude", "codex", "both"))
    parser.add_argument("--target", metavar="ROOT", help="Базовый каталог для .claude/.codex/.agents; по умолчанию home. Явный ROOT игнорирует каталоги клиентов из окружения.")
    parser.add_argument("--dry-run", action="store_true", help="Проверить и показать план без записи.")
    parser.add_argument("--replace", action="store_true", help="Сохранить резервные копии конфликтующих файлов и заменить их.")
    args = parser.parse_args()
    try:
        root = absolute(args.target if args.target is not None else Path.home())
        if root == Path(root.anchor):
            fail("Корень файловой системы нельзя использовать как ROOT.")
        check_path(root)
        clients = ("claude", "codex") if args.client == "both" else (args.client,)
        source = Path(__file__).resolve().parent
        entries = make_plan(source, clients, root, args.target is not None)
        preflight(entries, args.replace)
        backup_root = root / ".starter-kit-backups"
        if any(entry.action == "replace" for entry in entries):
            check_path(backup_root)
        print(f"Клиенты: {', '.join(clients)}; базовый каталог: {root}")
        for action in ("new", "replace", "same"):
            print(f"{action}: {sum(entry.action == action for entry in entries)}")
        for entry in entries:
            if entry.action != "same":
                print(f"  {entry.action}: {entry.destination}")
        if args.dry_run:
            print("Проверка завершена (--dry-run); файлы не изменены.")
        elif all(entry.action == "same" for entry in entries):
            print("Набор уже установлен; изменений нет.")
        else:
            install(entries, backup_root)
            print("Готово. Чужие файлы и прежние пакеты сохранены. Перезапустите выбранный клиент.")
        return 0
    except (InstallError, OSError, UnicodeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
