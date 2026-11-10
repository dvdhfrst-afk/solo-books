"use strict";

function printed(account, field) {
  const value = account[field + "_printed"];
  if (value === null) return "none";
  return value + (account.printed_as_owed ? " owed" : "");
}

async function showAccount(id) {
  const account = await api("GET", "accounts/" + id);
  byId("one-title").textContent = account.name;
  const parts = [facts([
    ["Business", account.business],
    ["Kind", account.kind.replace("_", " ")],
    ["Institution", account.institution],
    ["Balance", account.balance === null ? "no opening balance" : printed(account, "balance") + " on " + account.balance_on],
    ["Opening balance", account.opening_date ? printed(account, "opening_balance") + " at the start of " + account.opening_date : "none set"],
    ["Rows", String(account.rows)],
    ["Reconciled through", account.reconciled_through || "never"],
  ])];
  if (account.rows_before_opening) {
    parts.push(el("p", { className: "result bad", textContent: account.rows_before_opening + " rows are dated before the opening date and are left out of the balance." }));
  }
  parts.push(el("h2", { textContent: "Column mapping" }));
  if (account.mapping) {
    parts.push(facts(Object.entries(account.mapping).filter(([, value]) => value !== null && value !== false).map(([name, value]) => [name, Array.isArray(value) ? value.join(", ") : String(value)])));
  } else {
    parts.push(el("p", { className: "muted", textContent: "None yet. OFX, QFX and QBO files need none; a CSV needs one, set with 'account map'." }));
  }
  parts.push(el("h2", { textContent: "Reconciliations" }));
  parts.push(account.reconciliations.length
    ? table(
      [{ title: "Id", className: "num" }, { title: "From", className: "date" }, { title: "To", className: "date" }, { title: "Ending balance", className: "num" }, { title: "Status" }],
      account.reconciliations.map((r) => [
        el("a", { href: pageHref("/reconcile", { account: account.id, open: r.id }), textContent: String(r.id) }),
        r.from, r.to, r.statement_end_printed, r.status === "finished" ? tag("finished", "good") : tag("open"),
      ]),
    )
    : el("p", { className: "muted", textContent: "None yet." }));
  parts.push(el("div", { className: "actions" },
    el("a", { href: pageHref("/transactions", { account: account.id }), textContent: "See its rows" }),
    el("a", { href: pageHref("/reconcile", { account: account.id }), textContent: "Reconcile it" }),
    el("a", { href: pageHref("/imports", { account: account.id }), textContent: "Import a file into it" }),
  ));
  byId("one-body").replaceChildren(...parts);
  byId("one").hidden = false;
  byId("one-title").scrollIntoView({ block: "start" });
}

async function drawChart() {
  const businessId = byId("c-business").value;
  if (!businessId) {
    byId("chart").replaceChildren(empty("No businesses yet."));
    return;
  }
  const result = await api("GET", "businesses/" + businessId + "/chart", { query: { include_retired: byId("c-retired").checked } });
  byId("chart").replaceChildren(table(
    [{ title: "Code" }, { title: "Category" }, { title: "Kind" }, { title: "Form line" }, { title: "" }],
    result.categories.map((c) => [
      c.code, c.name, c.kind, c.line ? c.line + " " + c.line_name : "", c.active ? "" : tag("retired"),
    ]),
    "Form lines as of tax year " + result.tax_year,
  ));
}

async function start() {
  const [accountList, businessList] = await Promise.all([accounts(), businesses()]);
  if (!accountList.length) {
    byId("list").replaceChildren(empty("No accounts yet."));
  } else {
    byId("list").replaceChildren(table(
      [{ title: "Account" }, { title: "Business" }, { title: "Kind" }, { title: "Balance", className: "num" }, { title: "Rows", className: "num" }, { title: "Reconciled through", className: "date" }, { title: "Mapping" }],
      accountList.map((account) => [
        el("button", { type: "button", className: "link", textContent: account.name, onclick: () => showAccount(account.id).catch(failed) }),
        account.business,
        account.kind.replace("_", " "),
        account.balance === null ? "no opening balance" : printed(account, "balance"),
        String(account.rows),
        account.reconciled_through || "never",
        account.has_mapping ? "yes" : "none",
      ]),
    ));
  }
  fillSelect(byId("c-business"), businessList, (b) => b.id, (b) => b.name, null);
  await drawChart();
  const wanted = pageParams().get("account");
  if (wanted) await showAccount(wanted);
}

byId("c-business").addEventListener("change", () => drawChart().catch(failed));
byId("c-retired").addEventListener("change", () => drawChart().catch(failed));
start().catch(failed);
