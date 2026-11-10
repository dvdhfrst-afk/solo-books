"use strict";

const businessId = () => Number(byId("p-business").value);

function yearsText(payee) {
  return payee.years.map((y) => y.year + ": " + y.rows + " rows, out " + y.money_out + ", in " + y.money_in).join("; ");
}

async function flag(payee, button) {
  await busy(button, async () => {
    await api("POST", "payees/contractor", { body: { business_id: businessId(), payee: payee.key, contractor: !payee.contractor } });
    toast(payee.name + (payee.contractor ? " is no longer flagged as a contractor." : " is flagged as a contractor."));
    await drawPayees();
  });
}

async function drawPayees() {
  const year = byId("p-year").value.trim();
  keepParams({ business: businessId(), year });
  const result = await api("GET", "payees", { query: { business: businessId(), year } });
  byId("payee-names").replaceChildren(...result.payees.map((p) => el("option", { value: p.name })));
  if (!result.payees.length) {
    byId("list").replaceChildren(empty("No payees" + (year ? " with rows in " + year : "") + "."));
    return;
  }
  byId("list").replaceChildren(table(
    [{ title: "Payee", className: "text" }, { title: "Spellings", className: "text" }, { title: "Rows and money", className: "text" }, { title: "Contractor" }],
    result.payees.map((payee) => {
      const button = el("button", { type: "button", textContent: payee.contractor ? "Flagged: clear" : "Flag", "aria-label": (payee.contractor ? "Clear the contractor flag on " : "Flag as a contractor: ") + payee.name });
      button.addEventListener("click", () => flag(payee, button));
      return [payee.name, payee.spellings.join("; "), yearsText(payee), button];
    }),
  ));
}

function onSubmit(id, work) {
  byId(id).addEventListener("submit", (event) => {
    event.preventDefault();
    busy(event.submitter, async () => {
      toast(await work());
      byId(id).reset();
      await drawPayees();
    });
  });
}

onSubmit("rename", async () => {
  const result = await api("POST", "payees/rename", { body: { business_id: businessId(), payee: byId("r-payee").value, name: byId("r-name").value } });
  return "The payee is now shown as " + result.payee.name + ".";
});

onSubmit("merge", async () => {
  const result = await api("POST", "payees/merge", { body: { business_id: businessId(), payee: byId("m-payee").value, into: byId("m-into").value } });
  return "Merged into " + result.payee.name + "." + (result.contractor_carried ? " The contractor flag carried over." : "");
});

onSubmit("unmerge", async () => {
  const result = await api("POST", "payees/unmerge", { body: { business_id: businessId(), payee: byId("u-payee").value } });
  return result.payee.name + " is its own payee again, apart from " + result.apart_from + ".";
});

byId("pick").addEventListener("submit", (event) => {
  event.preventDefault();
  drawPayees().catch(failed);
});
byId("p-business").addEventListener("change", () => drawPayees().catch(failed));

async function start() {
  const businessList = await businesses();
  if (!businessList.length) {
    byId("list").replaceChildren(empty("No businesses yet. Add one with the 'business add' command."));
    return;
  }
  fillSelect(byId("p-business"), businessList, (b) => b.id, (b) => b.name, null);
  const params = pageParams();
  if (params.get("business")) byId("p-business").value = params.get("business");
  byId("p-year").value = params.get("year") || "";
  await drawPayees();
}

start().catch(failed);
