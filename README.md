# classify-lists

A command-line app for named lists. You type a sentence, [GLiNER2.5-Decide](https://huggingface.co/fastino/GLiNER2.5-Decide) picks a command, and the app runs the matching SQL in Postgres.

## What this demonstrates

The model chooses a command. The program runs it. [GLiNER2.5-Decide](https://huggingface.co/fastino/GLiNER2.5-Decide) returns one label from the menu you pass in: `create`, `add`, `complete`, `comment`, `list`, `remove`, `rename`. A line can hold more than one command, split on `and` before the next one. Each command runs the same SQL.

Lists live in Postgres. The model does not remember them. The program reads the new list name, the item, the note, and a rename's new name from the sentence. On every command except create, that list counts only when it already exists.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Apply [`schema.sql`](schema.sql) on your Postgres database. It needs PostgreSQL 13 or newer, and it is safe to run again.

Set `DATABASE_URL` to a Postgres connection string (`sslmode=require` is fine):

```bash
export DATABASE_URL='postgresql://...'
python app.py
```

Do not commit `.env`. The first run downloads the model. Later runs use the cache.

## Commands

Each clause is one of: `create`, `add`, `complete`, `comment`, `list`, `remove`, `rename`. The model picks which. Tab completes list names.

Name the list in the line. Short forms work (`add milk to groceries`), and longer ones do too (`add an item to yard work called pellet fert`). `to`, `on`, and `from` before a list name are not part of the item. `list` with no name prints every list.

A note follows the word `says`. A later line replaces it. A comment with no `says` clears the note on that item or list.

| Example | What happens |
| --- | --- |
| `create groceries` | Creates the list |
| `create a list called yard work` | Creates the list `yard work` |
| `create` | Creates a list named with the current time, such as `2026-10-02 20:17` |
| `list` | Prints every list name and its note |
| `list groceries` | Shows that list’s name and note, then open items, then completed items marked done. An empty list still shows its name |
| `add milk to groceries` | Adds an item to `groceries` |
| `add an item to yard work called pellet fert` | Adds `pellet fert` to `yard work` |
| `complete eggs on groceries` | Crosses the item off. It stays on the list as done |
| `comment pellet preventer on yard work that says 2 bags` | Sets a note on the open item |
| `add a comment on yard work to pellet preventer that says 2 bags` | Same as above |
| `comment pellet preventer on yard work` | Clears the note on that item |
| `add a comment on yard work that says for the weekend` | Sets a note on that list |
| `rename groceries to pantry` | Renames that list |
| `remove milk from groceries` | Deletes that item, done or not |
| `remove pellet fert from yard work` | Deletes that item |
| `remove groceries` | Deletes that list and its items |
