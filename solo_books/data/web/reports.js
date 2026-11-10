"use strict";

// The parameters each report takes, as GET reports lists them.
let kinds = {};

function shownFields() {
  const wanted = kinds[byId("k-kind").value] || [];
  document.querySelectorAll("label[data-for]").forEach((label) => {
    label.hidden = !wanted.includes(label.dataset.for);
  });
}

function query() {
  const wanted = kinds[byId("k-kind").value] || [];
  const values = {
    business: byId("k-business").value,
    period: byId("k-period").value.trim(),
    year: byId("k-year").value.trim(),
    years: byId("k-years").value.trim(),
    by: byId("k-by").value === "total" ? "" : byId("k-by").value,
    tag: byId("k-tag").value.trim(),
    limit: byId("k-limit").value.trim(),
    threshold: byId("k-threshold").value.trim(),
  };
  const result = {};
  Object.entries(values).forEach(([name, value]) => {
    if (name === "business" || wanted.includes(name)) result[name] = value;
  });
  return result;
}

function drawTable(block) {
  const head = el("tr", {}, ...block.headers.map((title, index) => el("th", {
    scope: "col", className: block.numeric.includes(index) ? "num" : "", textContent: title,
  })));
  const rows = block.rows.map((cells, rowIndex) => {
    const cellsShown = cells.map((value, index) => el("td", { className: block.numeric.includes(index) ? "num" : "text", textContent: value }));
    return el("tr", { className: block.styles[rowIndex] || "" }, ...cellsShown);
  });
  return el("div", { className: "scroll" }, el("table", {}, el("thead", {}, head), el("tbody", {}, ...rows)));
}

function drawDocument(doc) {
  const parts = [];
  doc.sections.forEach((section) => {
    parts.push(el("h3", { textContent: section.heading }));
    section.intro.forEach((line) => parts.push(el("p", { className: "muted", textContent: line })));
    section.blocks.forEach((block) => {
      if (block.paragraph !== undefined) parts.push(el("p", { className: block.warning ? "result bad" : "", textContent: block.paragraph }));
      else parts.push(drawTable(block));
    });
  });
  parts.push(el("p", { className: "muted", textContent: doc.note }));
  byId("report-title").textContent = doc.title;
  byId("report").replaceChildren(...parts);
}

async function show() {
  const kind = byId("k-kind").value;
  keepParams(Object.assign({ kind }, query()));
  drawDocument(await api("GET", "reports/" + kind + "/document", { query: query() }));
}

// The file comes through the API with the key in its header, then is handed to the browser to save.
async function download(format, button) {
  await busy(button, async () => {
    const params = new URLSearchParams();
    Object.entries(Object.assign({}, query(), { format })).forEach(([name, value]) => { if (value) params.set(name, value); });
    let response;
    try {
      response = await fetch(API_ROOT + "reports/" + byId("k-kind").value + "?" + params.toString(), { headers: { "X-Solo-Books-Token": KEY } });
    } catch (error) {
      throw new ApiFailure(0, "offline", "The solo-books server is not answering. Is the web command still running?");
    }
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new ApiFailure(response.status, "failed", data && data.error ? data.error.message : "The download failed.");
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const link = el("a", { href: URL.createObjectURL(await response.blob()), download: match ? match[1] : "report" });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
}

byId("pick").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, show);
});
byId("k-kind").addEventListener("change", shownFields);
byId("get-csv").addEventListener("click", (event) => download("csv", event.currentTarget));
byId("get-html").addEventListener("click", (event) => download("html", event.currentTarget));
byId("get-markdown").addEventListener("click", (event) => download("markdown", event.currentTarget));

async function start() {
  const [listed, businessList] = await Promise.all([api("GET", "reports"), businesses()]);
  listed.reports.forEach((entry) => { kinds[entry.report] = entry.parameters; });
  fillSelect(byId("k-business"), businessList, (b) => b.id, (b) => b.name, "Every business");
  const params = pageParams();
  ["kind", "business", "period", "year", "years", "by", "tag", "limit", "threshold"].forEach((name) => {
    if (params.get(name)) byId("k-" + name).value = params.get(name);
  });
  if (!byId("k-year").value) byId("k-year").value = String(new Date().getFullYear() - 1);
  if (!byId("k-period").value) byId("k-period").value = byId("k-year").value;
  if (!byId("k-years").value) byId("k-years").value = byId("k-year").value;
  shownFields();
  if (params.get("kind")) await show();
}

start().catch(failed);
