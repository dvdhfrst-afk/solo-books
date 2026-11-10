"use strict";

const businessId = () => Number(byId("r-business").value);

async function remove(rule, button) {
  if (!confirm("Delete rule " + rule.id + " (" + rule.said + ")? The rows it booked keep their category.")) return;
  await busy(button, async () => {
    await api("DELETE", "rules/" + rule.id);
    toast("Deleted rule " + rule.id + ".");
    await drawRules();
  });
}

async function switchRule(rule, button) {
  await busy(button, async () => {
    const result = await api("POST", "rules/" + rule.id + "/" + (rule.enabled ? "disable" : "enable"));
    toast(rule.enabled
      ? "Rule " + rule.id + " is disabled. The rows it booked stay booked."
      : "Rule " + rule.id + " is enabled. The rules booked " + result.categorized + " rows waiting in the queue.");
    await drawRules();
  });
}

async function moveRule(rule, place, button) {
  await busy(button, async () => {
    await api("POST", "rules/" + rule.id + "/move", { body: { to: place } });
    toast("Rule " + rule.id + " is now number " + place + ". Rows already booked stay as they are until the rules run again with re-run checked.");
    await drawRules();
  });
}

function button(text, label, className, onClick) {
  const node = el("button", { type: "button", className: className || "", textContent: text, "aria-label": label });
  node.addEventListener("click", () => onClick(node));
  return node;
}

async function drawRules() {
  const result = await api("GET", "rules", { query: { business: businessId() } });
  if (!result.rules.length) {
    byId("list").replaceChildren(empty("No rules yet."));
    return;
  }
  const count = result.rules.length;
  byId("list").replaceChildren(table(
    [
      { title: "#", className: "num" }, { title: "Id", className: "num" }, { title: "On" }, { title: "Conditions", className: "text" },
      { title: "Category" }, { title: "Note", className: "text" }, { title: "Rows", className: "num" }, { title: "" },
    ],
    result.rules.map((rule) => {
      const actions = el("span", { className: "actions" });
      const up = button("Up", "Move rule " + rule.id + " up", "", (node) => moveRule(rule, rule.place - 1, node));
      const down = button("Down", "Move rule " + rule.id + " down", "", (node) => moveRule(rule, rule.place + 1, node));
      up.disabled = rule.place === 1;
      down.disabled = rule.place === count;
      actions.append(
        up, down,
        button(rule.enabled ? "Disable" : "Enable", (rule.enabled ? "Disable" : "Enable") + " rule " + rule.id, "", (node) => switchRule(rule, node)),
        button("Delete", "Delete rule " + rule.id, "danger", (node) => remove(rule, node)),
      );
      return [
        String(rule.place), String(rule.id), rule.enabled ? "yes" : tag("off"), rule.conditions_said, categoryText(rule.category),
        rule.note, String(rule.rows), actions,
      ];
    }),
  ));
}

async function drawBusiness() {
  const id = businessId();
  fillCategories(byId("n-category"), await chart(id), "Choose a category");
  const own = (await accounts()).filter((a) => a.business_id === id);
  fillSelect(byId("n-account"), own, (a) => a.name, (a) => a.name, "Any account");
  fillSelect(byId("t-account"), own, (a) => a.id, (a) => a.name, "Any account");
  byId("test-result").replaceChildren();
  byId("explain-result").replaceChildren();
  await drawRules();
}

// The conditions the form gives, written the way a rule pack writes them (docs/RULES.md).
function conditions() {
  const found = [];
  const value = (id) => byId(id).value.trim();
  if (value("n-match")) found.push({ kind: value("n-match"), text: byId("n-pattern").value });
  else if (value("n-pattern")) throw new ApiFailure(0, "form", "Choose how the wording should match, or clear the text.");
  if (value("n-direction")) found.push({ kind: "direction", direction: value("n-direction") });
  const low = value("n-min");
  const high = value("n-max");
  if (low && high) found.push(low === high ? { kind: "amount_equals", amount: low } : { kind: "amount_between", min: low, max: high });
  else if (low) found.push({ kind: "amount_at_least", amount: low });
  else if (high) found.push({ kind: "amount_at_most", amount: high });
  if (value("n-account")) found.push({ kind: "account", account: value("n-account") });
  if (value("n-from") || value("n-to")) {
    const dates = { kind: "date_between" };
    if (value("n-from")) dates.from = value("n-from");
    if (value("n-to")) dates.to = value("n-to");
    found.push(dates);
  }
  if (value("n-day-from") || value("n-day-to")) {
    found.push({ kind: "day_of_month_between", from: value("n-day-from") ? Number(value("n-day-from")) : null, to: value("n-day-to") ? Number(value("n-day-to")) : null });
  }
  return found;
}

byId("add").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const body = {
      business_id: businessId(),
      conditions: conditions(),
      category: byId("n-category").value,
      priority: Number(byId("n-priority").value || 100),
    };
    if (byId("n-note").value.trim()) body.note = byId("n-note").value;
    const result = await api("POST", "rules", { body });
    toast("Added rule " + result.rule.id + ": " + result.rule.said + ". It and the other rules booked " + result.categorized + " rows waiting in the queue.");
    ["n-pattern", "n-min", "n-max", "n-from", "n-to", "n-day-from", "n-day-to", "n-note"].forEach((id) => { byId(id).value = ""; });
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
      parts.push(el("p", { className: "result good", textContent: "Rule " + result.rule.id + " (" + result.rule.said + ") books it." }));
    } else {
      parts.push(el("p", { className: "result bad", textContent: "No rule matches. This row would wait in the review queue." }));
      if (!body.amount) parts.push(el("p", { className: "muted", textContent: "Tested as " + result.tested_amount + ". Rules limited by direction or size need an amount." }));
    }
    byId("test-result").replaceChildren(...parts);
  });
});

byId("explain").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const result = await api("GET", "rules/explain", { query: { transaction: byId("e-id").value } });
    const parts = [el("p", { textContent: "Merchant reads as: " + result.merchant })];
    if (!result.trials.length) parts.push(empty("This row's business has no rules."));
    result.trials.forEach((trial) => {
      parts.push(el("h3", { textContent: trial.place + ". Rule " + trial.rule_id + ": " + trial.verdict }));
      parts.push(list(trial.conditions));
    });
    let outcome = result.winner ? "Rule " + result.winner + " books this row." : "No enabled rule books this row.";
    if (result.booked_by === "manual") outcome += " It was booked by hand, so rules leave it alone.";
    parts.push(el("p", { className: "result " + (result.winner ? "good" : "bad"), textContent: outcome }));
    byId("explain-result").replaceChildren(...parts);
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
