"use strict";

function stat(label, value, extra, small) {
  return el("div", { className: "stat" },
    el("div", { className: "label", textContent: label }),
    el("div", { className: "value" + (small ? " small" : "") }, value),
    extra ? el("div", { className: "muted" }, extra) : null,
  );
}

function balanceText(account) {
  if (account.balance === null) return "no opening balance";
  return account.balance_printed + (account.printed_as_owed ? " owed" : "");
}

async function drawDashboard() {
  const [status, accountList] = await Promise.all([api("GET", "status"), accounts()]);
  const waiting = el("a", { href: pageHref("/"), textContent: String(status.waiting) });
  const perBusiness = status.businesses.map((b) => b.name + ": " + b.waiting).join(", ");
  byId("stats").replaceChildren(
    stat("Waiting for review", waiting, perBusiness),
    stat("Last backup", status.last_backup || "Never. Make one with the backup command.", null, !status.last_backup),
    stat("Books file", status.books, null, true),
  );
  const next = byId("next");
  next.hidden = !status.next;
  next.textContent = status.next || "";

  if (!accountList.length) {
    byId("accounts").replaceChildren(empty("No accounts yet."));
    return;
  }
  const rows = accountList.map((account) => [
    el("a", { href: pageHref("/transactions", { account: account.id }), textContent: account.name }),
    account.business,
    balanceText(account),
    account.balance_on || "",
    String(account.rows),
    account.latest_row || "none",
    account.reconciled_through || "never",
    el("a", { href: pageHref("/reconcile", { account: account.id }), textContent: "Reconcile" }),
  ]);
  byId("accounts").replaceChildren(table(
    [
      { title: "Account" }, { title: "Business" }, { title: "Balance", className: "num" }, { title: "On", className: "date" },
      { title: "Rows", className: "num" }, { title: "Latest row", className: "date" }, { title: "Reconciled through", className: "date" },
      { title: "" },
    ],
    rows,
  ));
}

byId("run-check").addEventListener("click", (event) => busy(event.currentTarget, async () => {
  const report = await api("GET", "check");
  const counts = report.counts || {};
  const parts = [
    el("p", { className: "result " + (report.healthy ? "good" : "bad"), textContent: report.healthy ? "No problems found." : "Problems found. Nothing was changed." }),
    el("p", { className: "muted", textContent:
      (counts.rows || 0) + " rows, " + (counts.waiting || 0) + " waiting for review, " + (counts.rules || 0) + " rules, " +
      (counts.finished || 0) + " finished reconciliations. Schema version " + report.schema_version + "." }),
  ];
  if (report.problems.length) parts.push(list(report.problems), el("p", { textContent: "Keep a copy of the file as it is, then follow docs/RECOVERY.md." }));
  if (report.notes.length) parts.push(list(report.notes));
  byId("check").replaceChildren(...parts);
}));

drawDashboard().catch(failed);
