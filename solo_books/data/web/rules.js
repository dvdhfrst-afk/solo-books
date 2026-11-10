"use strict";

const businessId = () => Number(byId("r-business").value);

function limits(rule) {
  const parts = [];
  if (rule.direction) parts.push(rule.direction === "in" ? "money in" : "money out");
  if (rule.min !== null) parts.push("at least " + rule.min);
  if (rule.max !== null) parts.push("at most " + rule.max);
  if (rule.account) parts.push(rule.account);
  return parts.join(", ");
}

async function remove(rule, button) {
  if (!confirm("Delete rule " + rule.id + " (" + rule.match + " " + rule.pattern + ")? The rows it booked keep their category.")) return;
  await busy(button, async () => {
    await api("DELETE", "rules/" + rule.id);
    toast("Deleted rule " + rule.id + ".");
    await drawRules();
  });
}

async function drawRules() {
  const result = await api("GET", "rules", { query: { business: businessId() } });
  if (!result.rules.length) {
    byId("list").replaceChildren(empty("No rules yet."));
    return;
  }
  byId("list").replaceChildren(table(
    [
      { title: "Id", className: "num" }, { title: "Priority", className: "num" }, { title: "Match" }, { title: "Pattern", className: "text" },
      { title: "Category" }, { title: "Only when" }, { title: "Rows", className: "num" }, { title: "" },
    ],
    result.rules.map((rule) => {
      const button = el("button", { type: "button", className: "danger", textContent: "Delete", "aria-label": "Delete rule " + rule.id });
      button.addEventListener("click", () => remove(rule, button));
      return [String(rule.id), String(rule.priority), rule.match, rule.pattern, categoryText(rule.category), limits(rule), String(rule.rows), button];
    }),
  ));
}

async function drawBusiness() {
  const id = businessId();
  fillCategories(byId("n-category"), await chart(id), "Choose a category");
  const own = (await accounts()).filter((a) => a.business_id === id);
  fillSelect(byId("n-account"), own, (a) => a.id, (a) => a.name, "Any account");
  fillSelect(byId("t-account"), own, (a) => a.id, (a) => a.name, "Any account");
  byId("test-result").replaceChildren();
  await drawRules();
}

byId("add").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const body = {
      business_id: businessId(),
      match: byId("n-match").value,
      pattern: byId("n-pattern").value,
      category: byId("n-category").value,
      priority: Number(byId("n-priority").value || 100),
    };
    if (byId("n-direction").value) body.direction = byId("n-direction").value;
    if (byId("n-min").value.trim()) body.min = byId("n-min").value;
    if (byId("n-max").value.trim()) body.max = byId("n-max").value;
    if (byId("n-account").value) body.account_id = Number(byId("n-account").value);
    const result = await api("POST", "rules", { body });
    toast("Added rule " + result.rule.id + ". It and the other rules booked " + result.categorized + " rows waiting in the queue.");
    byId("n-pattern").value = "";
    await drawRules();
  });
});

byId("test").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const body = { business_id: businessId(), description: byId("t-description").value };
    if (byId("t-amount").value.trim()) body.amount = byId("t-amount").value;
    if (byId("t-account").value) body.account_id = Number(byId("t-account").value);
    const result = await api("POST", "rules/test", { body });
    const parts = [el("p", { textContent: "Merchant reads as: " + result.merchant })];
    if (result.rule) {
      parts.push(el("p", { className: "result good", textContent: "Rule " + result.rule.id + " (" + result.rule.match + " " + result.rule.pattern + ") books it to " + categoryText(result.category) + "." }));
    } else {
      parts.push(el("p", { className: "result bad", textContent: "No rule matches. This row would wait in the review queue." }));
      if (!body.amount) parts.push(el("p", { className: "muted", textContent: "Tested as " + result.tested_amount + ". Rules limited by direction or size need an amount." }));
    }
    byId("test-result").replaceChildren(...parts);
  });
});

byId("apply").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const result = await api("POST", "rules/apply", { body: { business_id: businessId(), recheck: byId("p-recheck").checked } });
    const outcome = result.results[0];
    toast(outcome.business + ": booked " + outcome.categorized + ", changed " + outcome.changed + ", back to review " + outcome.returned_to_review + ".");
    await drawRules();
  });
});

byId("r-business").addEventListener("change", () => drawBusiness().catch(failed));

async function start() {
  const businessList = await businesses();
  if (!businessList.length) {
    byId("list").replaceChildren(empty("No businesses yet. Add one with the 'business add' command."));
    return;
  }
  fillSelect(byId("r-business"), businessList, (b) => b.id, (b) => b.name, null);
  const wanted = pageParams().get("business");
  if (wanted) byId("r-business").value = wanted;
  await drawBusiness();
}

start().catch(failed);
