import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from gliner2 import AutoExtractor
from psycopg import Connection
from psycopg.errors import UniqueViolation

BASE_MODEL = "fastino/GLiNER2.5-Decide"
COMMANDS = ["create", "check out", "check in", "lists", "add", "complete", "list"]


@dataclass
class Session:
    checked_out_id: Optional[UUID] = None


def load_model():
    return AutoExtractor.from_pretrained(BASE_MODEL)


def open_db() -> Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL to your Neon connection string.")
    return Connection.connect(url)


def load_list_names(conn: Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM todos.lists ORDER BY lower(name)")
        return [row[0] for row in cur.fetchall()]


def classification_tasks(list_names: list[str]) -> dict:
    tasks = {"command": COMMANDS}
    if list_names:
        tasks["list"] = list_names
    return tasks


def line_contains_list_name(text: str, list_name: str) -> bool:
    target = list_name.casefold()
    return any(word.casefold() == target for word in text.split())


def remainder_after_command(text: str, command: str) -> str:
    skip = {word.casefold() for word in command.split()}
    words = [word for word in text.split() if word.casefold() not in skip]
    return " ".join(words).strip()


def item_body(text: str, command: str, list_name: Optional[str]) -> str:
    words = text.split()
    lowered = [word.casefold() for word in words]
    command_l = command.casefold()
    list_l = list_name.casefold() if list_name else None

    skip = set()
    for index, word in enumerate(lowered):
        if word == command_l:
            skip.add(index)
        if list_l and word == list_l:
            skip.add(index)
            if index > 0 and lowered[index - 1] == "to":
                skip.add(index - 1)

    return " ".join(words[i] for i in range(len(words)) if i not in skip).strip()


def list_name_by_id(conn: Connection, list_id: UUID) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM todos.lists WHERE id = %s", (list_id,))
        row = cur.fetchone()
        return row[0] if row else None


def list_id_by_name(conn: Connection, name: str) -> Optional[UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM todos.lists WHERE lower(name) = lower(%s)",
            (name,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def resolve_list_id(
    conn: Connection,
    text: str,
    classified_list: Optional[str],
    checked_out_id: Optional[UUID],
) -> Optional[UUID]:
    if classified_list and line_contains_list_name(text, classified_list):
        return list_id_by_name(conn, classified_list)
    return checked_out_id


def apply_command(
    conn: Connection,
    session: Session,
    command: Optional[str],
    text: str,
    classified_list: Optional[str],
) -> str:
    if not command or command not in COMMANDS:
        return f"unknown command: {command}"

    if command == "lists":
        names = load_list_names(conn)
        if not names:
            return "(empty)"
        return "\n".join(f"- {name}" for name in names)

    if command == "create":
        name = remainder_after_command(text, command)
        if not name:
            return "Say what to create."
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO todos.lists (name) VALUES (%s) RETURNING id",
                    (name,),
                )
                session.checked_out_id = cur.fetchone()[0]
        except UniqueViolation:
            conn.rollback()
            return f"already a list: {name}"
        return f"created: {name}"

    if command == "check out":
        name = remainder_after_command(text, command)
        if not name:
            return "Say which list to check out."
        list_id = list_id_by_name(conn, name)
        if not list_id:
            return f"no list: {name}"
        session.checked_out_id = list_id
        return f"checked out: {name}"

    if command == "check in":
        if session.checked_out_id is None:
            return "nothing is checked out"
        name = list_name_by_id(conn, session.checked_out_id)
        session.checked_out_id = None
        if not name:
            return "checked in"
        return f"checked in: {name}"

    list_id = resolve_list_id(conn, text, classified_list, session.checked_out_id)
    if command == "list" and not list_id:
        names = load_list_names(conn)
        if not names:
            return "(empty)"
        return "\n".join(f"- {name}" for name in names)

    if not list_id:
        return "check out a list first"

    if command == "list":
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT body FROM todos.items
                WHERE list_id = %s AND completed_at IS NULL
                ORDER BY created_at
                """,
                (list_id,),
            )
            rows = cur.fetchall()
        if not rows:
            return "(empty)"
        return "\n".join(f"- {row[0]}" for row in rows)

    matched_list = (
        classified_list
        if classified_list and line_contains_list_name(text, classified_list)
        else None
    )
    body = item_body(text, command, matched_list)
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
            return f"not on the list: {body}"
        return f"completed: {row[0]}"


def main():
    conn = open_db()
    model = load_model()
    session = Session()
    print(
        "Commands: create, check out, check in, lists, add, complete, list. "
        "Ctrl-D to quit."
    )
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        list_names = load_list_names(conn)
        result = model.classify_text(text, classification_tasks(list_names))
        command = result.get("command")
        classified_list = result.get("list")
        print(apply_command(conn, session, command, text, classified_list))
        conn.commit()


if __name__ == "__main__":
    main()
