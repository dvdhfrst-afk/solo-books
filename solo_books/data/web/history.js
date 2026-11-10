"use strict";

function filters() {
  return {
    business: byId("f-business").value,
    kind: byId("f-kind").value.trim(),
    period: byId("f-period").value.trim(),
    row: byId("f-row").value.trim(),
  };
}

async function revert(change, button) {
  if (!confirm("Take back change " + change.id + " (" + change.summary + ")? Taking it back is recorded too.")) return;
  await busy(button, async () => {
    const result = await api("POST", "history/" + change.id + "/revert");
    toast("Took back change " + change.id + ": " + result.summary);
    await findChanges();
    await showChange(result.change.id);
  });
}

function actionsFor(change) {
  const box = el("span", { className: "actions" });
  const show = el("button", { type: "button", textContent: "Show", "aria-label": "Show change " + change.id });
  show.addEventListener("click", () => showChange(change.id).catch(failed));
  box.append(show);
  if (change.revertible) {
    const back = el("button", { type: "button", className: "danger", textContent: "Take back", "aria-label": "Take back change " + change.id });
    back.addEventListener("click", () => revert(change, back));
    box.append(back);
  }
  return box;
}

async function findChanges() {
  const wanted = filters();
  keepParams(wanted);
  const result = await api("GET", "history", { query: wanted });
  if (!result.changes.length) {
    byId("list").replaceChildren(empty("No changes match."));
    return;
  }
  byId("list").replaceChildren(table(
    [
      { title: "Id", className: "num" }, { title: "When (UTC)", className: "date" }, { title: "From" }, { title: "Kind" },
      { title: "Business" }, { title: "Rows", className: "num" }, { title: "What changed", className: "text" }, { title: "" },
    ],
    result.changes.map((change) => [
      String(change.id), change.changed_at, change.source || "-", change.kind, change.business || "", String(change.rows),
      change.summary + (change.reverted_by ? " [taken back by " + change.reverted_by + "]" : ""), actionsFor(change),
    ]),
  ));
}

async function showChange(id) {
  const change = (await api("GET", "history/" + id)).change;
  byId("one-title").textContent = "Change " + change.id;
  const parts = [facts([
    ["What changed", change.summary],
    ["When (UTC)", change.changed_at],
    ["From", change.source || "not recorded"],
    ["Kind", change.kind + (change.kind_means ? " (" + change.kind_means + ")" : "")],
    ["Business", change.business],
    ["Taken back by", change.reverted_by ? "change " + change.reverted_by : ""],
  ])];
  if (change.row_changes.length) {
    parts.push(table(
      [{ title: "Row", className: "num" }, { title: "Before", className: "text" }, { title: "After", className: "text" }],
      change.row_changes.map((row) => [String(row.transaction_id), row.before_text, row.after_text]),
    ));
  }
  parts.push(actionsFor(change));
  byId("one-body").replaceChildren(...parts);
  byId("one").hidden = false;
  byId("one").scrollIntoView();
}

byId("filters").addEventListener("submit", (event) => {
  event.preventDefault();
  findChanges().catch(failed);
});

async function start() {
  fillSelect(byId("f-business"), await businesses(), (b) => b.id, (b) => b.name, "Any business");
  const params = pageParams();
  byId("f-business").value = params.get("business") || "";
  byId("f-kind").value = params.get("kind") || "";
  byId("f-period").value = params.get("period") || "";
  byId("f-row").value = params.get("row") || "";
  await findChanges();
}

start().catch(failed);
