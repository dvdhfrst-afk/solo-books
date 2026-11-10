# When something goes wrong with your books

Your books are one SQLite file, `books.sqlite3` unless you chose another name. Every change happens inside a database
transaction, so a crash, a power cut or Ctrl+C leaves the file as it was before that change. The usual causes of real
damage are a failing disk, a cloud sync program that wrote over the file while it was open, or copying the file while
it was being written.

**The first step in every case: copy the file somewhere safe before trying anything.** No command here changes a
damaged file, but a copy costs nothing.

## Habits that prevent it

```bash
python -m solo_books backup
```

That writes a dated copy beside the books. It checks the copy before keeping it, never overwrites anything, and `status`
tells you when the last one was made. Back up before a big import and at each month's end, and keep some copies on
another disk. A sync folder is fine for backups. For the live books it is risky, so keep those on a local disk.

## Is anything wrong?

```bash
python -m solo_books check
```

| Exit status | Meaning |
|---|---|
| 0 | No problems. It may still print notes, such as a finished reconciliation that a later row put out of balance. |
| 1 | Problems were found and listed. Nothing was changed. |
| 2 | It could not look: the file is missing, or another program is holding it. |

`check` looks at the file's structure with SQLite's own integrity check, then at the bookkeeping: links between rows, a
category from another business, dates that are not dates, rows changed behind the program's back, and mappings that
cannot be read. It also checks the history (a change of a kind this program does not know, details that cannot be
read, a change taken back by one that is not there), tags on another business's rows, payee merges that go round in a
circle, and budgets for categories that never reach profit and loss.

## You have a backup

```bash
python -m solo_books restore books-backup-20251231-120000.sqlite3
```

The backup is checked first, and a bad one changes nothing. The books as they were are kept beside it, named
`books.before-restore-<time>.sqlite3`. Anything imported since the backup is gone from the books, so import those files
again. Rows already there are recognized and are not doubled. Rows you categorized by hand since the backup do need
categorizing again, unless a rule covers them.

Close any other program using the books first. `restore` refuses while they are in use.

## You have no backup, or it is too old

```bash
python -m solo_books salvage saved.sqlite3
```

This reads the damaged books without changing them and copies every row it can read into a new file. It reports what
it could not read. If a row's category or rule was lost, the row keeps its money and goes back to the queue. The
history, tags, payees and budgets come across too, as far as they can be read.

Then work in the new file:

```bash
python -m solo_books --db saved.sqlite3 check
```

```bash
python -m solo_books --db saved.sqlite3 status
```

Import your bank files again to fill any gaps. Rows that came through are not doubled. When you are satisfied, either
keep using `--db saved.sqlite3`, or rename it to `books.sqlite3` once the damaged file is safely stored elsewhere.

Reconciliations are the record of what you already proved, so run `reconcile list` and rerun any that no longer tie.

A file cut short, as by an interrupted copy, is handled: `salvage` reads what is there and says the end is gone.

If `salvage` says it cannot read the table list, the part of the file that says where everything is has been damaged, and
this program cannot read it. Restore a backup. Tools built for SQLite repair may get further, on a copy.

## Messages you may see

**"are busy: another program has them open."** Another solo-books command in the middle of its work, a database viewer
or a sync program has the file. Close it and try again. Nothing was changed.

**"are read-only."** Your account cannot write to the file or its folder. Reading still works when no upgrade is
needed. Check the permissions.

**"the disk ... is full."** Nothing was saved. Free some space and try again.

**"look damaged"** or **"is not a solo-books file."** Follow this page from the top.

**"is a database, but it does not hold solo-books."** `--db` or `SOLO_BOOKS_DB` points at another program's file. It
was left untouched.

**"was written by a newer version."** Install that version. This one will not touch the file.

**"upgrading ... A copy of the old file is at ..."** A newer solo-books is updating the file's layout. Your rows are not
changed, and the old file is kept beside it, named `books.schema-<old version>-<time>.sqlite3`. If the upgrade fails,
the file stays at the old version. You can delete that copy once you have checked things with `check`.

**"This is a bug in solo-books"** (exit status 3). The command did not finish its work. Any change it was making was
rolled back. Setting the environment variable `SOLO_BOOKS_DEBUG=1` and running it again shows the full error.

## Fixing a wrong import

A file imported with the wrong mapping is not damage. Take it back and try again:

```bash
python -m solo_books imports list
```

```bash
python -m solo_books imports undo 1
```
