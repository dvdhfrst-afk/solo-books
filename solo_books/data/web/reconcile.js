"use strict";
let current = null;

function drawReport(report) {
  current = report;
  const owed = report.printed_as_owed ? " (amount owed)" : "";
  byId("report-title").textContent = "Reconciliation " + report.id + ": " + report.account + ", " + report.from + " to " + report.to;
  const figures = table(
    [{ title: "" }, { title: "Amount", className: "num" }],
    [
      ["Statement beginning balance" + owed, report.statement_begin_printed],
      ["Money in, " + report.rows + " rows in the period", report.money_in],
      ["Money out", report.money_out_cents ? "-" + report.money_out : "0.00"],
      ["Books say ending balance" + owed, report.books_end_printed],
      ["Statement ending balance" + owed, report.statement_end_printed],
    ],
  );
  const parts = [
    el("p", {}, report.status === "finished" ? tag("finished", "good") : tag("open")),
    figures,
    el("p", { className: "result", textContent: report.difference_says || "Difference: 0.00" }),
  ];
  if (report.begin_gap_cents) {
    parts.push(el("p", { textContent: "Beginning balance: statement " + report.statement_begin_printed + ", books " + report.books_begin_printed + "." }));
  }
  if (report.prior_gap_cents) {
    parts.push(el("p", { textContent: "The beginning balance differs from the ending balance of reconciliation " + report.prior_id + " by " + report.prior_gap.replace("-", "") + "." }));
  }
  if (report.hints.length) parts.push(el("h2", { textContent: "Where to look" }), list(report.hints));
  if (report.rows_added_after_finish.length) {
    parts.push(el("h2", { textContent: "Rows added after this period was finished" }), list(
      report.rows_added_after_finish.map((row) => row.id + "  " + row.date + "  " + row.amount + "  " + row.description),
    ));
  }
  if (report.notes.length) parts.push(list(report.notes));
  parts.push(el("p", {
    className: "result " + (report.balanced ? "good" : "bad"),
    textContent: report.balanced ? "Ties to the cent." : "Does not match.",
  }));
  if (!report.balanced && report.status === "open") {
    parts.push(el("p", {}, el("a", { href: pageHref("/transactions", { account: report.account_id, period: report.from + ".." + report.to }), textContent: "See the period's rows" })));
  }
  byId("report-body").replaceChildren(...parts);
  byId("finish").hidden = report.status !== "open" || !report.balances_tie;
  byId("reopen").hidden = report.status !== "finished";
  byId("report").hidden = false;
}

async function openReport(id) {
  const result = await api("GET", "reconciliations/" + id);
  drawReport(result.reconciliation);
  byId("report-title").scrollIntoView({ block: "start" });
}

async function drawPast() {
  const result = await api("GET", "reconciliations", { query: { account: byId("s-account").value } });
  if (!result.reconciliations.length) {
    byId("past").replaceChildren(empty("None for this account yet."));
    return;
  }
  byId("past").replaceChildren(table(
    [
      { title: "Id", className: "num" }, { title: "From", className: "date" }, { title: "To", className: "date" },
      { title: "Begin", className: "num" }, { title: "End", className: "num" }, { title: "Difference", className: "num" }, { title: "Status" },
    ],
    result.reconciliations.slice().reverse().map((r) => [
      el("button", { type: "button", className: "link", textContent: String(r.id), "aria-label": "Open reconciliation " + r.id, onclick: () => openReport(r.id).catch(failed) }),
      r.from, r.to, r.statement_begin_printed, r.statement_end_printed, r.difference,
      r.status === "finished" ? tag("finished", "good") : tag(r.balanced ? "open, ties" : "open"),
    ]),
  ));
}

byId("run").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const result = await api("POST", "reconciliations", {
      body: {
        account_id: Number(byId("s-account").value),
        from: byId("s-from").value,
        to: byId("s-to").value,
        begin: byId("s-begin").value,
        end: byId("s-end").value,
      },
    });
    drawReport(result.reconciliation);
    await drawPast();
  });
});

byId("finish").addEventListener("click", (event) => busy(event.currentTarget, async () => {
  const result = await api("POST", "reconciliations/" + current.id + "/finish");
  drawReport(result.reconciliation);
  toast("Reconciliation " + current.id + " finished: " + current.rows + " rows locked.");
  await drawPast();
}));

byId("reopen").addEventListener("click", (event) => {
  if (!confirm("Reopen reconciliation " + current.id + "? Its rows are unlocked until it is finished again.")) return;
  busy(event.currentTarget, async () => {
    const result = await api("POST", "reconciliations/" + current.id + "/reopen");
    drawReport(result.reconciliation);
    toast("Reconciliation " + current.id + " is open again.");
    await drawPast();
  });
});

byId("s-account").addEventListener("change", () => {
  byId("report").hidden = true;
  keepParams({ account: byId("s-account").value });
  drawPast().catch(failed);
});

async function start() {
  const accountList = await accounts();
  if (!accountList.length) {
    byId("run").replaceChildren(empty("Add an account with the 'account add' command first."));
    byId("past").replaceChildren();
    return;
  }
  fillSelect(byId("s-account"), accountList, (a) => a.id, (a) => a.name + " (" + a.business + ")", null);
  const params = pageParams();
  if (params.get("account")) byId("s-account").value = params.get("account");
  await drawPast();
  if (params.get("open")) await openReport(params.get("open"));
}

start().catch(failed);
