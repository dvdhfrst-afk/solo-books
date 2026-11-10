"use strict";
// Shared by every page. Values from the books always go in as text, never as markup.
const KEY = document.querySelector('meta[name="solo-books-key"]').content;
const API_ROOT = "/api/v1/";

class ApiFailure extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(method, path, options) {
  const opts = options || {};
  let url = API_ROOT + path;
  if (opts.query) {
    const params = new URLSearchParams();
    Object.entries(opts.query).forEach(([name, value]) => {
      if (value !== undefined && value !== null && value !== "" && value !== false) params.set(name, String(value));
    });
    const text = params.toString();
    if (text) url += "?" + text;
  }
  const headers = { "X-Solo-Books-Token": KEY };
  let body;
  if (opts.file !== undefined) {
    headers["Content-Type"] = "application/octet-stream";
    body = opts.file;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let response;
  try {
    response = await fetch(url, { method, headers, body });
  } catch (error) {
    throw new ApiFailure(0, "offline", "The solo-books server is not answering. Is the web command still running?");
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const problem = data && data.error;
    throw new ApiFailure(response.status, problem ? problem.code : "failed", problem ? problem.message : "The request failed.");
  }
  return data;
}

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([name, value]) => {
    // Names such as "aria-label" and "scope" are attributes; the rest, such as textContent, are properties.
    if (name.includes("-") || name === "scope" || name === "role") node.setAttribute(name, value);
    else node[name] = value;
  });
  kids.forEach((kid) => {
    if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
  });
  return node;
}

const byId = (id) => document.getElementById(id);

function pageHref(path, params) {
  const query = new URLSearchParams(params || {});
  query.set("key", KEY);
  return path + "?" + query.toString();
}

function pageParams() {
  return new URLSearchParams(location.search);
}

// Keep a page's filters in its address, so a reload or a bookmark comes back to the same view.
function keepParams(values) {
  const query = new URLSearchParams();
  query.set("key", KEY);
  Object.entries(values).forEach(([name, value]) => {
    if (value !== undefined && value !== null && value !== "" && value !== false) query.set(name, String(value));
  });
  history.replaceState(null, "", location.pathname + "?" + query.toString());
}

function toast(message, bad) {
  const box = byId("toast");
  byId("toast-text").textContent = message;
  box.className = "on" + (bad ? " bad" : "");
}
byId("toast-close").addEventListener("click", () => { byId("toast").className = ""; });

function failed(error) {
  toast(error.message, true);
}

// Run a request from a button: the button rests while it runs, and a refusal is shown in plain words.
async function busy(button, work) {
  if (button) button.disabled = true;
  try {
    return await work();
  } catch (error) {
    failed(error);
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}

function cell(value, className) {
  if (value instanceof Node) return el("td", { className: className || "" }, value);
  return el("td", { className: className || "", textContent: value === null || value === undefined ? "" : String(value) });
}

// columns: [{ title, className }]; rows: arrays of text or nodes, one per column.
function table(columns, rows, caption) {
  const head = el("tr", {}, ...columns.map((column) => el("th", { scope: "col", className: column.className || "", textContent: column.title })));
  const body = el("tbody", {}, ...rows.map((row) => el("tr", {}, ...row.map((value, index) => cell(value, columns[index].className)))));
  return el("div", { className: "scroll" }, el("table", {}, caption ? el("caption", { textContent: caption }) : null, el("thead", {}, head), body));
}

function empty(text) {
  return el("p", { className: "empty", textContent: text });
}

function tag(text, kind) {
  return el("span", { className: "tag" + (kind ? " " + kind : ""), textContent: text });
}

function categoryText(category) {
  return category ? category.code + " " + category.name : "Waiting for review";
}

function fillSelect(select, items, valueOf, textOf, firstText) {
  const chosen = select.value;
  select.replaceChildren();
  if (firstText !== null) select.append(el("option", { value: "", textContent: firstText }));
  items.forEach((item) => select.append(el("option", { value: String(valueOf(item)), textContent: textOf(item) })));
  if ([...select.options].some((option) => option.value === chosen)) select.value = chosen;
}

const KIND_GROUPS = [
  ["Expenses", ["expense"]],
  ["Not income or expense", ["transfer", "equity"]],
  ["Income", ["income", "returns"]],
  ["Cost of goods sold", ["cogs"]],
];

function fillCategories(select, categories, firstText) {
  const chosen = select.value;
  select.replaceChildren(el("option", { value: "", textContent: firstText }));
  KIND_GROUPS.forEach(([label, kinds]) => {
    const group = el("optgroup", { label });
    categories.filter((c) => c.active && kinds.includes(c.kind)).forEach((c) => {
      group.append(el("option", { value: c.code, textContent: c.code + " " + c.name }));
    });
    if (group.children.length) select.append(group);
  });
  if ([...select.options].some((option) => option.value === chosen)) select.value = chosen;
}

const cache = {};

async function businesses() {
  if (!cache.businesses) cache.businesses = (await api("GET", "businesses")).businesses;
  return cache.businesses;
}

async function accounts() {
  if (!cache.accounts) cache.accounts = (await api("GET", "accounts")).accounts;
  return cache.accounts;
}

async function chart(businessId) {
  const name = "chart" + businessId;
  if (!cache[name]) cache[name] = (await api("GET", "businesses/" + businessId + "/chart")).categories;
  return cache[name];
}

function list(items) {
  return el("ul", { className: "plain" }, ...items.map((item) => el("li", { textContent: item })));
}

function facts(pairs) {
  const box = el("dl", { className: "facts" });
  pairs.forEach(([name, value]) => {
    if (value === null || value === undefined || value === "") return;
    box.append(el("dt", { textContent: name }), value instanceof Node ? el("dd", {}, value) : el("dd", { textContent: String(value) }));
  });
  return box;
}
