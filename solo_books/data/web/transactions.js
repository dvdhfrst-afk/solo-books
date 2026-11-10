"use strict";
const PAGE_SIZE = 50;
let offset = 0;
let shown = null;

function chosenBusinessId(businessList) {
  if (byId("f-business").value) return Number(byId("f-business").value);
  const account = byId("f-account").value;
  if (account) return cache.accounts.find((a) => String(a.id) === account).business_id;
  return businessList.length === 1 ? businessList[0].id : null;
}

async function refreshFilterCategories() {
  const businessList = await businesses();
  const businessId = chosenBusinessId(businessList);
  const select = byId("f-category");
  select.disabled = businessId === null;
  if (businessId === null) {
    select.replaceChildren(el("option", { value: "", textContent: "Choose a business first" }));
    return;
  }
  fillCategories(select, await chart(businessId), "Any category");
}

function refreshFilterAccounts() {
  const business = byId("f-business").value;
  const shownAccounts = cache.accounts.filter((a) => !business || String(a.business_id) === business);
  fillSelect(byId("f-account"), shownAccounts, (a) => a.id, (a) => a.name, "Any account");
}

function filters() {
  return {
    business: byId("f-business").value,
    account: byId("f-account").value,
    period: byId("f-period").value.trim(),
    category: byId("f-category").disabled ? "" : byId("f-category").value,
    search: byId("f-search").value.trim(),
    uncategorized: byId("f-uncategorized").checked,
    order: byId("f-newest").checked ? "newest" : "",
  };
}

async function findRows() {
  const wanted = filters();
  keepParams(Object.assign({}, wanted, { offset: offset || "" }));
  const result = await api("GET", "transactions", { query: Object.assign({}, wanted, { limit: PAGE_SIZE, offset }) });
  const first = result.total ? offset + 1 : 0;
  const last = Math.min(offset + PAGE_SIZE, result.total);
  byId("summary").textContent = "Rows " + first + " to " + last + " of " + result.total + ", net " + result.net + ".";
  byId("prev").disabled = offset === 0;
  byId("next").disabled = last >= result.total;
  if (!result.transactions.length) {
    byId("rows").replaceChildren(empty("No rows match."));
    return;
  }
  const rows = result.transactions.map((row) => [
    row.date,
    row.account,
    el("button", { type: "button", className: "link", textContent: row.description, "aria-label": "Open row " + row.id + ": " + row.description, onclick: () => openRow(row.id).catch(failed) }),
    row.amount,
    row.category ? categoryText(row.category) : tag("waiting"),
    el("span", {}, ...row.tags.map((name) => tag(name))),
    row.reconciliation_id ? tag("reconciled", "good") : "",
  ]);
  byId("rows").replaceChildren(table(
    [{ title: "Date", className: "date" }, { title: "Account" }, { title: "Description", className: "text" }, { title: "Amount", className: "num" }, { title: "Category" }, { title: "Tags" }, { title: "" }],
    rows,
  ));
}

function drawDetail(row) {
  shown = row;
  byId("detail-title").textContent = "Transaction " + row.id;
  let booked = categoryText(row.category);
  if (row.category && row.booked_by === "manual") booked += ", by hand";
  if (row.category && row.booked_by === "rule") booked += row.rule ? ", by rule " + row.rule.id + " (" + row.rule.said + ")" : ", by a rule since deleted";
  byId("detail-facts").replaceChildren(facts([
    ["Date", row.date],
    ["Account", row.account + " (" + row.business + ")"],
    ["Amount", row.amount],
    ["Description", row.description],
    ["Merchant", row.merchant],
    ["Memo", row.memo],
    ["Bank id", row.reference],
    ["Category", booked],
    ["Held back from rules", row.held_back_from_rules.join(", ")],
    ["Reconciled", row.reconciliation_id ? "finished reconciliation " + row.reconciliation_id : "no"],
    ["Imported", row.imports.length ? row.imports.map((i) => i.file_name + " (import " + i.id + ")").join(", ") : "no, added by hand"],
  ]));
  byId("d-note").value = row.note || "";
  drawTags(row);
  byId("d-clear").disabled = !row.category;
  byId("d-delete").disabled = Boolean(row.reconciliation_id);
  byId("d-locked").textContent = row.reconciliation_id
    ? "This row is in a finished reconciliation. Reopen it to delete the row."
    : "";
}

function drawTags(row) {
  const chips = row.tags.map((name) => {
    const off = el("button", { type: "button", className: "link", textContent: "remove", "aria-label": "Take tag " + name + " off this row" });
    off.addEventListener("click", () => busy(off, async () => {
      await api("POST", "tags/remove", { body: { tag: name, ids: [row.id] } });
      await afterChange(await api("GET", "transactions/" + row.id), "Took tag " + name + " off.");
    }));
    return el("span", { className: "tag" }, name + " ", off);
  });
  byId("d-tags").replaceChildren(chips.length ? el("span", {}, "Tags: ", ...chips) : el("span", { className: "muted", textContent: "No tags." }));
}

async function openRow(id) {
  const row = await api("GET", "transactions/" + id);
  fillCategories(byId("d-category"), await chart(row.business_id), "Choose a category");
  byId("d-category").value = row.category ? row.category.code : "";
  drawDetail(row);
  await fillTagNames();
  if (!byId("detail").open) byId("detail").showModal();
}

async function afterChange(row, message) {
  drawDetail(row);
  toast(message);
  await findRows();
}

byId("detail-close").addEventListener("click", () => byId("detail").close());

byId("detail-category").addEventListener("submit", (event) => {
  event.preventDefault();
  const code = byId("d-category").value;
  if (!code) { toast("Choose a category first.", true); return; }
  busy(event.submitter, async () => {
    const result = await api("POST", "transactions/" + shown.id + "/category", { body: { category: code } });
    await afterChange(result.transaction, "Booked to " + categoryText(result.transaction.category) + ".");
  });
});

byId("d-clear").addEventListener("click", (event) => busy(event.currentTarget, async () => {
  const result = await api("POST", "transactions/" + shown.id + "/category", { body: { category: null } });
  byId("d-category").value = "";
  await afterChange(result.transaction, "Back in the review queue.");
}));

byId("detail-tags").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = byId("d-tag").value.trim();
  if (!name) { toast("Type a tag first.", true); return; }
  busy(event.submitter, async () => {
    const result = await api("POST", "tags/add", { body: { tag: name, ids: [shown.id] } });
    byId("d-tag").value = "";
    await afterChange(await api("GET", "transactions/" + shown.id), "Tagged it " + result.tag + (result.created ? " (a new tag)." : "."));
    await fillTagNames();
  });
});

async function fillTagNames() {
  const result = await api("GET", "tags", { query: { business: shown ? shown.business_id : "" } });
  byId("tag-names").replaceChildren(...result.tags.map((t) => el("option", { value: t.name })));
}

byId("detail-note").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const result = await api("POST", "transactions/" + shown.id + "/note", { body: { note: byId("d-note").value } });
    await afterChange(result.transaction, "Note saved.");
  });
});

byId("d-delete").addEventListener("click", (event) => {
  if (!confirm("Delete transaction " + shown.id + " (" + shown.amount + " " + shown.description + ")? A later export will not bring it back.")) return;
  busy(event.currentTarget, async () => {
    await api("DELETE", "transactions/" + shown.id);
    byId("detail").close();
    toast("Deleted transaction " + shown.id + ".");
    await findRows();
  });
});

async function refreshAddCategories() {
  const account = cache.accounts.find((a) => String(a.id) === byId("a-account").value);
  const select = byId("a-category");
  if (!account) {
    select.replaceChildren(el("option", { value: "", textContent: "Choose an account first" }));
    return;
  }
  fillCategories(select, await chart(account.business_id), "Leave it for review");
}

byId("add").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const body = {
      account_id: Number(byId("a-account").value),
      date: byId("a-date").value,
      amount: byId("a-amount").value,
      description: byId("a-description").value,
    };
    if (byId("a-category").value) body.category = byId("a-category").value;
    if (byId("a-note").value.trim()) body.note = byId("a-note").value;
    const result = await api("POST", "transactions", { body });
    const warning = result.warnings.length ? " Warning: " + result.warnings.join(" ") : "";
    toast("Added transaction " + result.transaction.id + "." + warning, Boolean(warning));
    ["a-amount", "a-description", "a-note"].forEach((id) => { byId(id).value = ""; });
    await findRows();
  });
});

byId("filters").addEventListener("submit", (event) => {
  event.preventDefault();
  offset = 0;
  findRows().catch(failed);
});
byId("f-business").addEventListener("change", () => {
  refreshFilterAccounts();
  refreshFilterCategories().catch(failed);
});
byId("f-account").addEventListener("change", () => refreshFilterCategories().catch(failed));
byId("a-account").addEventListener("change", () => refreshAddCategories().catch(failed));
byId("prev").addEventListener("click", () => { offset = Math.max(0, offset - PAGE_SIZE); findRows().catch(failed); });
byId("next").addEventListener("click", () => { offset += PAGE_SIZE; findRows().catch(failed); });

async function start() {
  const [businessList, accountList] = await Promise.all([businesses(), accounts()]);
  const params = pageParams();
  fillSelect(byId("f-business"), businessList, (b) => b.id, (b) => b.name, "Any business");
  byId("f-business").value = params.get("business") || "";
  refreshFilterAccounts();
  byId("f-account").value = params.get("account") || "";
  await refreshFilterCategories();
  byId("f-category").value = params.get("category") || "";
  byId("f-period").value = params.get("period") || "";
  byId("f-search").value = params.get("search") || "";
  byId("f-uncategorized").checked = params.get("uncategorized") === "true";
  byId("f-newest").checked = params.get("order") === "newest";
  offset = Number(params.get("offset")) || 0;
  fillSelect(byId("a-account"), accountList, (a) => a.id, (a) => a.name + " (" + a.business + ")", "Choose an account");
  await refreshAddCategories();
  await findRows();
}

start().catch(failed);
