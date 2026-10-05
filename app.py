import os
import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from gliner2 import AutoExtractor
from psycopg import Connection
from psycopg.errors import InterfaceError, OperationalError, UniqueViolation

BASE_MODEL = "fastino/GLiNER2.5-Decide"
COMMAND_LABELS = {
    "create": "start a new list",
    "add": "put an item on a list",
    "complete": "mark an item done, finished, checked, or complete",
    "comment": "set or change a note on an item or a list",
    "list": "show the lists or the items on a list",
    "remove": "delete an item or a list",
    "rename": "change a list name",
}
COMMANDS = list(COMMAND_LABELS)
FILLER_WORDS = {"a", "an", "the", "item", "that", "to", "on", "from"}
POLITE_PREFIXES = ("i would like to ", "i'd like to ", "id like to ")
CLAUSE_SPLIT = re.compile(
    r"\s+\band\b\s+(?=(?:add|create|comment|complete|remove|rename|list|mark|make|set|put)\b)",
    re.IGNORECASE,
)


def load_model():
    return AutoExtractor.from_pretrained(BASE_MODEL)


def open_db() -> Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL to your postgres connection string.")
    return Connection.connect(url)


class Database:
    def __init__(self) -> None:
        self.conn = open_db()

    def reconnect(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = open_db()

    def call(self, fn):
        try:
            return fn(self.conn)
        except (OperationalError, InterfaceError):
            self.reconnect()
            return fn(self.conn)


def load_list_names(conn: Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM todos.lists ORDER BY lower(name)")
        return [row[0] for row in cur.fetchall()]


def normalize_word(word: str) -> str:
    return word.casefold().strip(".,!?;:")


def drop_polite_prefix(text: str) -> str:
    folded = text.casefold()
    for prefix in POLITE_PREFIXES:
        if folded.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def split_on_last_says(text: str) -> tuple[str, str]:
    words = text.split()
    if not words:
        return "", ""
    lowered = [normalize_word(word) for word in words]
    if "says" not in lowered:
        return text.strip(), ""
    index = len(lowered) - 1 - lowered[::-1].index("says")
    left_words = words[:index]
    if left_words and normalize_word(left_words[-1]) == "that":
        left_words = left_words[:-1]
    note = " ".join(words[index + 1 :]).strip()
    return " ".join(left_words).strip(), note


def list_title(name: str) -> str:
    words = name.split()
    if (
        len(words) >= 2
        and normalize_word(words[0]) in {"a", "an", "the"}
        and normalize_word(words[-1]) == "list"
    ):
        words = words[1:-1]
    trimmed = " ".join(words).strip()
    return trimmed or name


def remainder_after_command(text: str, command: str) -> str:
    skip = {word.casefold() for word in command.split()}
    words = [word for word in text.split() if word.casefold() not in skip]
    return " ".join(words).strip()


def matching_list_name(text: str, list_names: list[str]) -> Optional[str]:
    words = text.split()
    lowered = [normalize_word(word) for word in words]
    best: Optional[tuple[int, str]] = None
    for name in list_names:
        name_words = [normalize_word(word) for word in name.split()]
        count = len(name_words)
        if count == 0 or count > len(words):
            continue
        for start in range(len(words) - count + 1):
            if lowered[start : start + count] == name_words:
                if best is None or count > best[0]:
                    best = (count, name)
                break
    return best[1] if best else None


def text_without_list_name(text: str, list_name: Optional[str]) -> str:
    if not list_name:
        return text.strip()
    words = text.split()
    lowered = [normalize_word(word) for word in words]
    name_words = [normalize_word(word) for word in list_name.split()]
    count = len(name_words)
    for start in range(len(words) - count + 1):
        if lowered[start : start + count] != name_words:
            continue
        skip = set(range(start, start + count))
        if start > 0 and lowered[start - 1] in {"to", "on", "from"}:
            skip.add(start - 1)
        kept = [words[index] for index in range(len(words)) if index not in skip]
        return " ".join(kept).strip()
    return text.strip()


def item_after_called_or_named(text: str) -> str:
    words = text.split()
    if not words:
        return ""
    lowered = [normalize_word(word) for word in words]
    for cue in ("called", "named"):
        if cue not in lowered:
            continue
        index = len(lowered) - 1 - lowered[::-1].index(cue)
        return " ".join(words[index + 1 :]).strip()
    return ""


def strip_command_and_filler(
    text: str, command: str, *, comment_line: bool
) -> str:
    words = text.split()
    if not words:
        return ""
    lowered = [normalize_word(word) for word in words]
    command_words = {command.casefold()}
    if comment_line:
        command_words.update({"add", "comment"})
    skip = {
        index
        for index, word in enumerate(lowered)
        if word in command_words or word in FILLER_WORDS
    }
    kept = [words[index] for index in range(len(words)) if index not in skip]
    return " ".join(kept).strip()


def parse_rename(left: str, list_names: list[str]) -> tuple[str, str]:
    old_name = matching_list_name(left, list_names) or ""
    new_name = ""
    if old_name:
        words = left.split()
        lowered = [normalize_word(word) for word in words]
        name_words = [normalize_word(word) for word in old_name.split()]
        count = len(name_words)
        for start in range(len(words) - count + 1):
            if lowered[start : start + count] != name_words:
                continue
            rest = words[start + count :]
            if rest and normalize_word(rest[0]) == "to":
                rest = rest[1:]
            new_name = " ".join(rest).strip()
            break
    else:
        remainder = remainder_after_command(left, "rename")
        words = remainder.split()
        if words and normalize_word(words[0]) == "to":
            new_name = " ".join(words[1:]).strip()
        elif words:
            old_name = matching_list_name(remainder, list_names) or ""
            if old_name:
                _, new_name = parse_rename(remainder, list_names)
            else:
                new_name = remainder.strip()
    return old_name, new_name


def parse_create_name(left: str) -> str:
    text = drop_polite_prefix(left)
    named = item_after_called_or_named(text)
    if named:
        return list_title(named)
    trimmed = strip_command_and_filler(text, "create", comment_line=False)
    words = trimmed.split()
    if words and normalize_word(words[0]) == "list":
        trimmed = " ".join(words[1:]).strip()
    if not trimmed:
        trimmed = remainder_after_command(text, "create")
    return list_title(trimmed)


def parse_clause(
    text: str, command: str, list_names: list[str]
) -> dict[str, str]:
    left, note = split_on_last_says(text)
    left = drop_polite_prefix(left)
    comment_line = bool(note) or command == "comment"

    if command == "create":
        return {
            "item": "",
            "list_name": "",
            "comment": note,
            "new_name": "",
            "create_name": parse_create_name(left),
        }

    if command == "rename":
        old_name, new_name = parse_rename(left, list_names)
        return {
            "item": "",
            "list_name": old_name,
            "comment": note,
            "new_name": new_name,
            "create_name": "",
        }

    list_name = matching_list_name(left, list_names) or ""
    without_list = text_without_list_name(left, list_name or None)
    item = item_after_called_or_named(without_list)
    if not item:
        item = strip_command_and_filler(
            without_list, command, comment_line=comment_line
        )

    return {
        "item": item,
        "list_name": list_name,
        "comment": note,
        "new_name": "",
        "create_name": "",
    }


def split_clauses(text: str) -> list[str]:
    clauses = []
    for sentence in re.split(r"(?<=[.?!])\s+", text.strip()):
        for part in CLAUSE_SPLIT.split(sentence):
            cleaned = part.strip(" .")
            if cleaned:
                clauses.append(cleaned)
    return clauses or [text.strip()]


def choose_command(model, text: str) -> Optional[str]:
    result = model.classify_text(text, {"command": {"labels": COMMAND_LABELS}})
    picked = result.get("command")
    if isinstance(picked, list):
        picked = picked[0] if picked else None
    if picked in COMMANDS:
        return picked
    return explicit_command(text)


def timestamp_names(moment: Optional[datetime] = None) -> list[str]:
    current = moment if moment is not None else datetime.now().astimezone()
    minute = current.strftime("%Y-%m-%d %H:%M")
    second = current.strftime("%Y-%m-%d %H:%M:%S")
    return [minute, second, *[f"{second} {extra}" for extra in range(2, 6)]]


def complete_line(buffer: str, name: str) -> Optional[str]:
    folded_buffer = buffer.casefold()
    folded_name = name.casefold()
    best_index = None
    for index in range(len(buffer) + 1):
        if index > 0 and not buffer[index - 1].isspace():
            continue
        suffix = folded_buffer[index:]
        if not suffix or suffix == folded_name or not folded_name.startswith(suffix):
            continue
        best_index = index
        break
    if best_index is not None:
        return buffer[:best_index] + name
    if buffer == "" or buffer[-1].isspace():
        if not buffer.casefold().rstrip().endswith(folded_name):
            return buffer + name
    return None


def line_completions(buffer: str, names: list[str]) -> list[str]:
    options = []
    for name in names:
        completed = complete_line(buffer, name)
        if completed is not None and completed not in options:
            options.append(completed)
    return options


def install_name_completer(db: Database) -> None:
    try:
        import readline
    except ImportError:
        return
    cached: list[str] = []

    def complete(text: str, state: int) -> Optional[str]:
        nonlocal cached
        try:
            if state == 0:
                cached = line_completions(
                    readline.get_line_buffer(),
                    db.call(load_list_names),
                )
            if state < len(cached):
                return cached[state]
        except Exception:
            return None
        return None

    readline.set_completer(complete)
    readline.set_completer_delims("\t\n")
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def explicit_command(text: str) -> Optional[str]:
    folded = text.casefold()
    for command in sorted(COMMANDS, key=len, reverse=True):
        prefix = command.casefold()
        if folded == prefix or folded.startswith(prefix + " "):
            return command
    return None


def format_list_names(rows: list[tuple[str, Optional[str]]]) -> str:
    if not rows:
        return "(empty)"
    lines = []
    for name, comment in rows:
        if comment:
            lines.append(f"- {name} — {comment}")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def load_lists(conn: Connection) -> list[tuple[str, Optional[str]]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, comment FROM todos.lists ORDER BY lower(name)"
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def format_list(
    name: Optional[str],
    list_comment: Optional[str],
    rows: list[tuple[str, Optional[str], bool]],
) -> str:
    lines = []
    if name and list_comment:
        lines.append(f"{name} — {list_comment}")
    elif name:
        lines.append(name)
    elif list_comment:
        lines.append(list_comment)
    if not rows:
        lines.append("(empty)")
    for body, comment, done in rows:
        label = f"{body} (done)" if done else body
        if comment:
            lines.append(f"- {label} — {comment}")
        else:
            lines.append(f"- {label}")
    return "\n".join(lines)


def apply_remove(
    conn: Connection, action: dict[str, str], carried: str = ""
) -> str:
    named = resolve_list_name(conn, action["list_name"], carried)
    body = action["item"]
    if named and not body:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM todos.lists
                WHERE lower(name) = lower(%s)
                RETURNING name
                """,
                (named,),
            )
            row = cur.fetchone()
        if not row:
            return f"no list: {named}"
        return f"removed: {row[0]}"
    if not body:
        return "Say what to remove."
    if not named:
        return "Name a list."
    list_id = list_id_by_name(conn, named)
    if not list_id:
        return f"no list: {named}"
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM todos.items
            WHERE id = (
                SELECT id FROM todos.items
                WHERE list_id = %s AND lower(body) = lower(%s)
                ORDER BY (completed_at IS NULL) DESC, created_at
                LIMIT 1
            )
            RETURNING body
            """,
            (list_id, body),
        )
        row = cur.fetchone()
    if not row:
        return f"not on the list: {body}"
    return f"removed: {row[0]}"


def comment_message(name: str, comment: Optional[str]) -> str:
    if comment:
        return f"comment on {name}: {comment}"
    return f"cleared comment on {name}"


def apply_comment(conn: Connection, action: dict[str, str], carried: str = "") -> str:
    comment_text = action["comment"].strip() or None
    matched_list = resolve_list_name(conn, action["list_name"], carried)
    if not matched_list:
        return "Name a list."
    list_id = list_id_by_name(conn, matched_list)
    if not list_id:
        return f"no list: {matched_list}"
    target = action["item"]
    if not target:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE todos.lists
                SET comment = %s
                WHERE id = %s
                RETURNING name
                """,
                (comment_text, list_id),
            )
            row = cur.fetchone()
        if not row:
            return "no list"
        return comment_message(row[0], comment_text)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE todos.items
            SET comment = %s
            WHERE list_id = %s
              AND lower(body) = lower(%s)
              AND completed_at IS NULL
            RETURNING body
            """,
            (comment_text, list_id, target),
        )
        row = cur.fetchone()
    if not row:
        return f"not on the list: {target}"
    return comment_message(row[0], comment_text)


def list_id_by_name(conn: Connection, name: str) -> Optional[UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM todos.lists WHERE lower(name) = lower(%s)",
            (name,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def canonical_list_name(conn: Connection, name: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name FROM todos.lists WHERE lower(name) = lower(%s)",
            (name,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def resolve_list_name(
    conn: Connection, parsed_name: str, carried: str = ""
) -> Optional[str]:
    if parsed_name:
        known = canonical_list_name(conn, parsed_name)
        if known:
            return known
    if carried:
        return canonical_list_name(conn, carried)
    return None


def insert_list(conn: Connection, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO todos.lists (name) VALUES (%s)", (name,))


def create_list(conn: Connection, supplied: str) -> str:
    if supplied:
        try:
            insert_list(conn, supplied)
        except UniqueViolation:
            conn.rollback()
            return f"already a list: {supplied}"
        return f"created: {supplied}"
    names = timestamp_names()
    for index, name in enumerate(names):
        try:
            insert_list(conn, name)
        except UniqueViolation:
            conn.rollback()
            if index == len(names) - 1:
                return f"already a list: {name}"
            continue
        return f"created: {name}"


def apply_rename(conn: Connection, action: dict[str, str], carried: str = "") -> str:
    old_name = resolve_list_name(conn, action["list_name"], carried)
    new_name = action["new_name"]
    if not old_name:
        return "Say which list to rename."
    list_id = list_id_by_name(conn, old_name)
    if list_id is None:
        return f"no list: {old_name}"
    if not new_name:
        return "Say the new name."
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE todos.lists
                SET name = %s
                WHERE id = %s
                RETURNING name
                """,
                (new_name, list_id),
            )
            row = cur.fetchone()
    except UniqueViolation:
        conn.rollback()
        return f"already a list: {new_name}"
    if not row:
        return "no list"
    return f"renamed: {old_name} to {row[0]}"


def apply_command(
    conn: Connection,
    command: Optional[str],
    text: str,
    list_names: list[str],
    carried: str = "",
) -> str:
    if not command or command not in COMMANDS:
        return f"unknown command: {command}"

    action = parse_clause(text, command, list_names)
    if command == "add" and action["comment"]:
        command = "comment"

    if command == "remove":
        return apply_remove(conn, action, carried)

    if command == "rename":
        return apply_rename(conn, action, carried)

    if command == "comment":
        return apply_comment(conn, action, carried)

    if command == "list":
        named = resolve_list_name(conn, action["list_name"], carried)
        if not named:
            return format_list_names(load_lists(conn))
        list_id = list_id_by_name(conn, named)
        if not list_id:
            return f"no list: {named}"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, comment FROM todos.lists WHERE id = %s",
                (list_id,),
            )
            list_row = cur.fetchone()
            cur.execute(
                """
                SELECT body, comment, completed_at IS NOT NULL
                FROM todos.items
                WHERE list_id = %s
                ORDER BY (completed_at IS NULL) DESC, created_at
                """,
                (list_id,),
            )
            rows = cur.fetchall()
        list_name = list_row[0] if list_row else None
        list_comment = list_row[1] if list_row else None
        return format_list(list_name, list_comment, rows)

    matched_list = resolve_list_name(conn, action["list_name"], carried)
    list_id = list_id_by_name(conn, matched_list) if matched_list else None
    if not list_id:
        return "Name a list."

    body = action["item"]
    if command == "add":
        if not body:
            return "Say what to add."
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO todos.items (list_id, body) VALUES (%s, %s)",
                    (list_id, body),
                )
        except UniqueViolation:
            conn.rollback()
            return f"already on the list: {body}"
        return f"added: {body}"

    if command == "complete":
        if not body:
            return "Say what to complete."
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE todos.items
                SET completed_at = now()
                WHERE list_id = %s
                  AND lower(body) = lower(%s)
                  AND completed_at IS NULL
                RETURNING body
                """,
                (list_id, body),
            )
            row = cur.fetchone()
        if not row:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT body FROM todos.items
                    WHERE list_id = %s
                      AND lower(body) = lower(%s)
                      AND completed_at IS NOT NULL
                    LIMIT 1
                    """,
                    (list_id, body),
                )
                done = cur.fetchone()
            if done:
                return f"already done: {done[0]}"
            return f"not on the list: {body}"
        return f"completed: {row[0]}"

    return f"unknown command: {command}"


def apply_lines(conn: Connection, model, text: str) -> str:
    messages = []
    carried = ""
    list_names = load_list_names(conn)
    for clause in split_clauses(text):
        command = choose_command(model, clause)
        if command == "create":
            action = parse_clause(clause, "create", list_names)
            name = action["create_name"]
            messages.append(create_list(conn, name))
            if name:
                carried = name
                list_names = load_list_names(conn)
            continue
        messages.append(
            apply_command(conn, command, clause, list_names, carried)
        )
    return "\n".join(messages)


def main():
    db = Database()
    model = load_model()
    install_name_completer(db)
    print(
        "Commands: create, add, complete, comment, list, remove, rename. "
        "Tab completes list names. Ctrl-D to quit."
    )
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue

        def run(conn: Connection) -> str:
            message = apply_lines(conn, model, text)
            conn.commit()
            return message

        print(db.call(run))


if __name__ == "__main__":
    main()
