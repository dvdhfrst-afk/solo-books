"use strict";
const ROW_WARNINGS_SHOWN = 10;
let pending = null;

function outcomeParts(result) {
  const span = result.earliest ? " (" + result.earliest + " to " + result.latest + ")" : "";
  const parts = [facts([
    ["File", result.file_name + " into " + result.account],
    ["Rows read", result.rows_read + span],
    ["New", String(result.rows_new)],
    ["Already in the books", String(result.rows_duplicate)],
    ["Deleted earlier", result.rows_deleted_before ? result.rows_deleted_before + " (left out; you removed these on purpose)" : ""],
    ["Skipped", result.rows_skipped + " (pending, zero-amount or footer rows)"],
    ["Unreadable", result.rows_bad ? String(result.rows_bad) : ""],
    ["Booked by rules", String(result.booked_by_rules)],
    ["Waiting for review", String(result.waiting_for_review)],
  ])];
  if (result.bad_rows.length) {
    parts.push(el("h2", { textContent: "Rows that could not be read" }), list(result.bad_rows.slice(0, 20)));
    if (result.dry_run) parts.push(el("p", { className: "result bad", textContent: "A real import stops on these rows and saves nothing, unless you tick the box to import the readable rows." }));
  }
  if (result.samples.length) {
    parts.push(table(
      [{ title: "Line", className: "num" }, { title: "Date", className: "date" }, { title: "Amount", className: "num" }, { title: "Description", className: "text" }],
      result.samples.map((row) => [String(row.line), row.date, row.amount, row.description]),
      "How the first rows read. Money in is positive, money out is negative.",
    ));
  }
  const warnings = result.warnings.concat(result.row_warnings.slice(0, ROW_WARNINGS_SHOWN));
  if (result.row_warnings.length > ROW_WARNINGS_SHOWN) warnings.push("...and " + (result.row_warnings.length - ROW_WARNINGS_SHOWN) + " more like these.");
  if (warnings.length) parts.push(el("h2", { textContent: "Warnings" }), list(warnings));
  return parts;
}

async function send(dryRun) {
  return api("POST", "imports", {
    query: { account: pending.account, filename: pending.name, dry_run: dryRun, skip_bad: pending.skipBad },
    file: pending.bytes,
  });
}

byId("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  const file = byId("u-file").files[0];
  if (!file) { toast("Choose a file first.", true); return; }
  busy(event.submitter, async () => {
    pending = { account: byId("u-account").value, name: file.name, skipBad: byId("u-skip-bad").checked, bytes: await file.arrayBuffer() };
    const result = await send(true);
    byId("outcome-title").textContent = "What would happen (nothing is saved yet)";
    byId("outcome-body").replaceChildren(...outcomeParts(result));
    byId("confirm").hidden = false;
    byId("confirm").disabled = result.rows_bad > 0 && !pending.skipBad;
    byId("cancel").textContent = "Not now";
    byId("outcome").hidden = false;
    byId("outcome-title").scrollIntoView({ block: "start" });
  });
});

byId("confirm").addEventListener("click", (event) => busy(event.currentTarget, async () => {
  const result = await send(false);
  pending = null;
  byId("outcome-title").textContent = "Imported as import " + result.import_id;
  const parts = outcomeParts(result);
  if (result.waiting_for_review) parts.push(el("p", {}, el("a", { href: pageHref("/"), textContent: "Review the " + result.waiting_for_review + " rows waiting" })));
  byId("outcome-body").replaceChildren(...parts);
  byId("confirm").hidden = true;
  byId("cancel").textContent = "Done";
  byId("upload").reset();
  toast("Imported " + result.rows_new + " new rows from " + result.file_name + ".");
  await drawPast();
}));

byId("cancel").addEventListener("click", () => {
  pending = null;
  byId("outcome").hidden = true;
});

async function undo(item, button) {
  if (!confirm("Undo import " + item.id + " (" + item.file_name + ")? Rows only this file put in are removed.")) return;
  await busy(button, async () => {
    let outcome;
    try {
      outcome = await api("POST", "imports/" + item.id + "/undo", { body: { force: false } });
    } catch (error) {
      // Rows booked by hand would lose that work, so asking again is the person's call.
      if (error.status !== 409 || !/by hand/.test(error.message)) throw error;
      if (!confirm(error.message + "\n\nUndo anyway?")) return;
      outcome = await api("POST", "imports/" + item.id + "/undo", { body: { force: true } });
    }
    let message = "Removed " + outcome.removed + " rows from import " + item.id + ".";
    if (outcome.kept) message += " Kept " + outcome.kept + " that import " + outcome.kept_in.join(", ") + " also held.";
    toast(message);
    await drawPast();
  });
}

async function drawPast() {
  const result = await api("GET", "imports");
  if (!result.imports.length) {
    byId("past").replaceChildren(empty("No files imported yet."));
    return;
  }
  const rows = result.imports.slice().reverse().map((item) => {
    const button = el("button", { type: "button", textContent: "Undo", "aria-label": "Undo import " + item.id });
    button.addEventListener("click", () => undo(item, button));
    return [
      String(item.id), item.account, item.file_name, item.imported_at, String(item.rows_read), String(item.rows_new),
      String(item.rows_duplicate), item.undone_at ? tag("undone") : button,
    ];
  });
  byId("past").replaceChildren(table(
    [
      { title: "Id", className: "num" }, { title: "Account" }, { title: "File", className: "text" }, { title: "When (UTC)", className: "date" },
      { title: "Read", className: "num" }, { title: "New", className: "num" }, { title: "Already in", className: "num" }, { title: "" },
    ],
    rows,
  ));
}

async function start() {
  const accountList = await accounts();
  fillSelect(byId("u-account"), accountList, (a) => a.id, (a) => a.name + " (" + a.business + ")", null);
  const wanted = pageParams().get("account");
  if (wanted) byId("u-account").value = wanted;
  if (!accountList.length) byId("upload").replaceChildren(empty("Add an account with the 'account add' command first."));
  await drawPast();
}

start().catch(failed);
