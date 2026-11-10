# Security

solo-books holds financial records on your own computer. It has no accounts, makes no network requests of its own, sends
no telemetry and has no dependencies. Nothing here has had an outside security audit.

## What it defends against

**Hostile files.** A bank file is treated as untrusted input.

- Control characters, terminal escape codes and text-direction overrides are removed at import and again at every print,
  so a row description cannot rewrite your screen or disguise itself.
- A cell starting with `=`, `+`, `-` or `@` is prefixed with `'` in CSV output, so a merchant name cannot run as a
  spreadsheet formula. Real numbers stay numbers.
- OFX files are read with plain text matching, never an XML parser, so entity tricks do nothing.
- SQL is always parameterized. Names and descriptions never become part of a query.
- Limits: 500 MB per file, 2,000 characters per cell, and amounts up to 100 billion dollars. Over the limit, the row or
  file is refused with a message.
- Regular expressions in rules are length-limited, and nested repeats that could hang are refused.
- Names cannot escape the folder you chose: `../../x` as a business name becomes `x` in a file name.

**The web pages and the API.** `web` listens on 127.0.0.1 only, and that address cannot be changed by a flag.

- Each run makes a new random key. The page needs it in its address, and every data request needs it in a header, so
  other pages and other programs on your computer cannot read or change your books through it.
- It checks the `Host` header, which blocks DNS rebinding, and sends a strict Content Security Policy.
- Descriptions are placed on the page as text, never as HTML.
- The address printed at startup holds the key. Treat it like a password while the server is running.

The API under `/api/v1/` trusts a request only when all of these hold, and refuses it otherwise:

- The `X-Solo-Books-Token` header holds this run's key, compared in constant time. Reads need it as much as writes do.
- The `Host` header is `127.0.0.1` or `localhost` at this server's port.
- A write uses `POST` or `DELETE`. `GET` never changes anything.
- A write is not marked as coming from another site: an `Origin` header, if sent, must be this server's own address,
  and a `Sec-Fetch-Site` header, if sent, must be `same-origin` or `none`.
- A body is `application/json`, or `application/octet-stream` for an uploaded export. A web page cannot send either type
  to another site without the browser asking that site first, and this server never says yes.
- A body is at most 8 MB. A larger one is refused before it is read.
- A query parameter or body field it does not know is refused rather than ignored.

Every write goes through the same functions as the command line, with the same checks. An uploaded file is written to a
new temporary folder under a cleaned name, which keeps only the last part of the name and replaces anything but
letters, digits, spaces and `._-()`, and the folder is removed when the import ends. Errors never include a stack trace.

**Your own files.** Reports and backups are written to a temporary file first and moved into place, so a failure leaves
the old file alone. Nothing writes over a SQLite file, and a backup never overwrites anything. Another program's
database is refused and left untouched. New books get owner-only permissions on Linux and macOS.

## What it does not defend against

- **The file is not encrypted.** Anyone who can read `books.sqlite3` can read your books. Use disk encryption, and keep
  backups somewhere as private as the books.
- **Other programs running as you.** They can read the file directly.
- **Report files.** The files for your preparer contain your rows. They are yours to protect when you send them.
- **Rows that are false.** A file can contain made-up transactions. Tying each account to a statement is how you find them.

## Reporting a problem

If you find a security problem, use the private reporting feature of the place you got this code, if it has one.
Otherwise open an issue that says a problem exists, without the details, and ask how to send them. Never attach real
bank files or real books to a report; make a small invented file that shows the same thing.
