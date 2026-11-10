# Settings and profiles

Every command works without any settings. Settings change the defaults: which books file to open, which business
to work on, how reports and dates print, and a few more. They live in one JSON file that you can read and edit by hand,
or change with `config set`.

## Where the settings file is

| System | Place |
|---|---|
| Windows | `%APPDATA%\solo-books\config.json` |
| Everything else | `$XDG_CONFIG_HOME/solo-books/config.json`, or `~/.config/solo-books/config.json` when `XDG_CONFIG_HOME` is not set |

The environment variable `SOLO_BOOKS_CONFIG` names another file instead, for example a settings file kept beside a
set of books. The file does not exist until you set something. This prints where it is, or would be:

```bash
python -m solo_books config path
```

## Which value wins

Each setting is looked up in this order, and the first place that has a value decides:

1. A flag on the command line, such as `--db` or `--format`.
2. The setting's environment variable, such as `SOLO_BOOKS_DB`.
3. The active profile, if one is in use.
4. The settings file.
5. The setting's default.

`config list` shows every setting, its value, and which of those five places it came from.

```bash
python -m solo_books config list
```

## Every setting

| Setting | Type | Default | Environment variable | Flag | What it does |
|---|---|---|---|---|---|
| `books` | file path | `books.sqlite3` | `SOLO_BOOKS_DB` | `--db` | The books file commands work on. A relative path is read from the current folder. |
| `business` | text | not set | `SOLO_BOOKS_BUSINESS` | `--business` | The business a command works on when it needs one and you leave out `--business`. Not set means the only business, if there is just one. Commands that cover every business when `--business` is left out, such as the reports, `review`, `rules apply` and `txn list`, still cover every business. |
| `report_format` | `table` or `csv` | `table` | `SOLO_BOOKS_REPORT_FORMAT` | `--format` | How `report pnl`, `report balances` and `report schedule-c` print. |
| `date_format` | `iso` or `us` | `iso` | `SOLO_BOOKS_DATE_FORMAT` | none | How row dates show on screen in `txn list`, `txn show`, `review`, `import`, `reconcile list`, `rules explain` and `status`. `iso` is 2025-03-31 and `us` is 03/31/2025. Dates you type, and every file written, stay ISO. |
| `web_port` | port number, 0 to 65535 | `8765` | `SOLO_BOOKS_WEB_PORT` | `--port` | The port the review page listens on, at 127.0.0.1. 0 picks any free port. |
| `import_runs_rules` | true or false | `true` | `SOLO_BOOKS_IMPORT_RUNS_RULES` | `--rules`, `--no-rules` | Whether `import` books new rows with the rules as they arrive. With false, every new row waits in the queue until `rules apply`. |
| `backup_folder` | folder path | not set | `SOLO_BOOKS_BACKUP_FOLDER` | the FILE argument | Where `backup` puts its dated copy when you name no file. Not set means beside the books. The folder must exist. |
| `backup_keep` | whole number, 1 to 1000 | `10` | `SOLO_BOOKS_BACKUP_KEEP` | `--keep` | How many dated backups `backup --prune` keeps. |

An environment variable that is set but empty counts as not set. `true` and `false` can also be written `yes` and
`no`, `on` and `off`, or `1` and `0`, in an environment variable or with `config set`.

## Changing settings

```bash
python -m solo_books config set date_format us
```

```bash
python -m solo_books config get date_format
```

```bash
python -m solo_books config unset date_format
```

`config set` checks the value before it saves anything, and writes the file in one step, so a crash leaves the old file
or the new one, never half of each. A path is saved in full, so it works from any folder. With `--profile NAME`,
`config set` and `config unset` change that profile instead of the top of the file.

## The file

It is one JSON object. Settings sit at the top. `profiles` holds one object per profile, and `profile` names the profile
used when no other one is asked for.

```json
{
  "date_format": "us",
  "report_format": "table",
  "profile": "2025",
  "profiles": {
    "2025": {"books": "C:\\Books\\books-2025.sqlite3", "business": "Business A"},
    "test-copy": {"books": "C:\\Books\\test-copy.sqlite3", "import_runs_rules": false}
  }
}
```

Write paths with forward slashes or doubled backslashes, as JSON needs. A value of `null` counts as not set.

### When the file is wrong

If the file is not valid JSON or cannot be read, every command stops with one sentence saying what is wrong and where,
and exit status 2. So does a bad value at the top of the file or in the profile in use, and a profile in use that does
not exist. Two commands always run, so you can find the problem: `config path` and `config check`. When the file is
valid JSON, `config set` and `config unset` also run, so a bad value can be fixed in place.

```bash
python -m solo_books config check
```

`config check` lists every problem it finds, without stopping at the first: unknown keys, bad values, a default profile
that does not exist, and bad values in the `SOLO_BOOKS_` environment variables. It exits with status 1 if it found
anything and 0 if not. An unknown key alone does not stop other commands, but `config check` reports it, since it is
usually a misspelled setting that is quietly doing nothing.

## Profiles

A profile is a named set of settings. It always names a books file, and it can hold any other setting. Use one when you
keep more than one set of books: a file per year, a test copy to try things on, or books for a business you keep apart.

```bash
python -m solo_books profile add test-copy --db test-copy.sqlite3 --set date_format=us
```

Use it for one command with `--profile`, which every command accepts:

```bash
python -m solo_books --profile test-copy init
```

Or make it the default, so every command uses it until you say otherwise:

```bash
python -m solo_books profile use test-copy
```

```bash
python -m solo_books status
```

`status` names the profile in use and the books file. The profile in use is the one `--profile` names, else the one in
`SOLO_BOOKS_PROFILE`, else the file's default. A setting in the profile still loses to a flag or an environment
variable, as the order above says. That includes `SOLO_BOOKS_DB`: unset it if you want profiles to choose the books.

```bash
python -m solo_books profile list
```

```bash
python -m solo_books profile show test-copy
```

```bash
python -m solo_books profile use --none
```

```bash
python -m solo_books profile remove test-copy
```

Removing a profile never touches its books file.

A profile name has up to 40 letters, digits, dots, dashes and underscores, and starts with a letter or digit.

## Backups and pruning

`backup` with no file name writes a copy named for the date and time, such as `books-backup-20251231-235959.sqlite3`,
in `backup_folder` or beside the books. `--prune` then removes all but the newest dated backups of those books in that
folder, keeping `backup_keep` of them, or as many as `--keep` says:

```bash
python -m solo_books backup --prune --keep 5
```

Only files named the way `backup` names them, for the books being backed up, are removed, and only when they hold a
database. A backup you named yourself is never removed.
