"use strict";

const businessId = () => Number(byId("b-business").value);
const year = () => Number(byId("b-year").value);

async function remove(entry, button) {
  if (!confirm("Delete the " + entry.year + " budget for " + entry.code + " " + entry.name + "?")) return;
  await busy(button, async () => {
    await api("POST", "budgets/delete", { body: { business_id: businessId(), category: entry.code, year: entry.year } });
    toast("Deleted the " + entry.year + " budget for " + entry.code + " " + entry.name + ".");
    await drawBudgets();
  });
}

async function drawBudgets() {
  keepParams({ business: businessId(), year: year() });
  const result = await api("GET", "budgets", { query: { business: businessId(), year: year() } });
  if (!result.budgets.length) {
    byId("list").replaceChildren(empty("No budgets for " + year() + "."));
    return;
  }
  byId("list").replaceChildren(table(
    [{ title: "Category" }, { title: "Kind" }, { title: "Entered" }, { title: "Year total", className: "num" }, { title: "By month", className: "text" }, { title: "" }],
    result.budgets.map((entry) => {
      const button = el("button", { type: "button", className: "danger", textContent: "Delete", "aria-label": "Delete the budget for " + entry.code + " " + entry.name });
      button.addEventListener("click", () => remove(entry, button));
      return [
        entry.code + " " + entry.name, entry.kind, entry.entered_as, entry.year_total,
        entry.months.map((m) => m.month + ": " + m.amount).join(", "), button,
      ];
    }),
  ));
}

byId("set").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const body = { business_id: businessId(), year: year(), category: byId("s-category").value };
    if (byId("s-yearly").value.trim()) body.yearly = byId("s-yearly").value;
    if (byId("s-monthly").value.trim()) body.monthly = byId("s-monthly").value;
    if (byId("s-month").value) body.month = Number(byId("s-month").value);
    const result = await api("POST", "budgets", { body });
    toast("Budget saved for " + result.budget.code + " " + result.budget.name + ": " + result.budget.year_total + " for the year."
      + (result.spread ? " The yearly amount was spread over the months first." : ""));
    ["s-yearly", "s-monthly"].forEach((id) => { byId(id).value = ""; });
    await drawBudgets();
  });
});

async function drawBusiness() {
  fillCategories(byId("s-category"), await chart(businessId()), "Choose a category");
  await drawBudgets();
}

byId("pick").addEventListener("submit", (event) => {
  event.preventDefault();
  drawBudgets().catch(failed);
});
byId("b-business").addEventListener("change", () => drawBusiness().catch(failed));

async function start() {
  const businessList = await businesses();
  if (!businessList.length) {
    byId("list").replaceChildren(empty("No businesses yet. Add one with the 'business add' command."));
    return;
  }
  fillSelect(byId("b-business"), businessList, (b) => b.id, (b) => b.name, null);
  const params = pageParams();
  if (params.get("business")) byId("b-business").value = params.get("business");
  byId("b-year").value = params.get("year") || String(new Date().getFullYear());
  await drawBusiness();
}

start().catch(failed);
