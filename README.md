# classify-lists

A command-line app for named lists. You type a sentence, [GLiNER2.5-Decide](https://huggingface.co/fastino/GLiNER2.5-Decide) picks a command, and the app runs the matching SQL in Postgres (Neon).

The model returns one label from the menu you pass in. It does not write a reply, and it does not store the lists. List names and items live in the database. The checked-out list lives only in this process.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Apply [`schema.sql`](schema.sql) once on your Neon database.

Set a connection string (a pooled URL with `sslmode=require` is fine):

```bash
export DATABASE_URL='postgresql://...'
python app.py
```

Do not commit `.env`. The first run downloads the model. Later runs use the cache.

## Commands

Each line is classified as one of: `create`, `check out`, `check in`, `lists`, `add`, `complete`, `list`.

When at least one list exists, those names are passed as a second classification task. If the line names a list, `add`, `complete`, and `list` use that list. Otherwise they use the list checked out in this process.

| Example | What happens |
| --- | --- |
| `create groceries` | Creates the list and checks it out |
| `check out groceries` | Sets the session’s active list |
| `check in` | Clears the session’s active list and names it |
| `lists` | Prints every list name |
| `add milk to groceries` | Adds an item to `groceries` |
| `add buy milk` | Adds to the checked-out list |
| `list groceries` | Shows open items on that list |
| `list` | Shows open items on the checked-out list, or every list name when none is checked out |
| `complete buy milk` | Marks the item done on the resolved list |

The checked-out list lasts only for that `python app.py` process. `check in` clears it without changing rows in Neon. Quitting does the same.
