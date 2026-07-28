const DATA_URL = "./data/chapter-00-reader.json";

const state = {
  data: null,
  query: "",
  changedOnly: false,
};

const els = {
  editionCount: document.querySelector("#edition-count"),
  edgeCount: document.querySelector("#edge-count"),
  scopeNote: document.querySelector("#scope-note p"),
  latestMeta: document.querySelector("#latest-meta"),
  latestText: document.querySelector("#latest-text"),
  transitionList: document.querySelector("#transition-list"),
  search: document.querySelector("#page-search"),
  searchStatus: document.querySelector("#search-status"),
  changedOnly: document.querySelector("#changed-only"),
  print: document.querySelector("#print-button"),
  loadError: document.querySelector("#load-error"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightSearch(value) {
  const text = escapeHtml(value);
  if (!state.query) return text;
  const pattern = new RegExp(`(${escapeRegExp(state.query)})`, "giu");
  return text.replace(pattern, '<mark class="search-hit">$1</mark>');
}

function containsQuery(...values) {
  if (!state.query) return true;
  return values.some((value) =>
    String(value || "").toLocaleLowerCase().includes(state.query.toLocaleLowerCase()),
  );
}

function dateRoleLabel(date) {
  if (date.role === "official_update_date") return "官方更新標示";
  return "官方年度版";
}

function sourceLink(url, label = "官方 ODT") {
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>`;
}

function renderLatest() {
  const latest = state.data.latest;
  els.latestMeta.innerHTML = `
    <p class="edition-meta__role">${dateRoleLabel(latest.date)}</p>
    <strong>${escapeHtml(latest.label)}</strong>
    <time datetime="${escapeHtml(latest.date.date_value)}">${escapeHtml(latest.date.date_value)}</time>
    <span class="date-caution">不是自動推定的法律生效日</span>
    ${sourceLink(latest.source.official_url)}
  `;

  let matchCount = 0;
  els.latestText.innerHTML = latest.full_text_blocks
    .map((block) => {
      const match = containsQuery(block.text);
      if (state.query && match) matchCount += 1;
      const path = block.structural_path.at(-1);
      const className = path ? "rule-paragraph rule-paragraph--section" : "rule-paragraph";
      return `<p class="${className}" data-search-match="${String(match)}">${highlightSearch(block.text)}</p>`;
    })
    .join("");
  return matchCount;
}

function inlineSide(segments, side, fallback) {
  if (!segments?.length) return highlightSearch(fallback || "");
  const visible = segments.filter((segment) => {
    if (segment.side === "both") return true;
    return side === "old" ? segment.side === "old" : segment.side === "new";
  });
  if (!visible.length) return highlightSearch(fallback || "");
  return visible
    .map((segment) => {
      const content = highlightSearch(segment.text);
      const changed =
        (side === "old" && segment.kind === "removed") ||
        (side === "new" && segment.kind === "added");
      if (!changed) return content;
      return side === "old"
        ? `<del class="inline-change inline-change--old">${content}</del>`
        : `<ins class="inline-change inline-change--new">${content}</ins>`;
    })
    .join("");
}

function renderHunk(hunk) {
  const matches = containsQuery(
    hunk.context_label,
    hunk.old_text,
    hunk.new_text,
  );
  if (state.query && !matches) return "";

  const oldBlock = hunk.old_text
    ? `
      <div class="diff-side diff-side--old">
        <div class="diff-label"><span aria-hidden="true">−</span> 前一版移除</div>
        <p>${inlineSide(hunk.inline_segments, "old", hunk.old_text)}</p>
      </div>`
    : "";
  const newBlock = hunk.new_text
    ? `
      <div class="diff-side diff-side--new">
        <div class="diff-label"><span aria-hidden="true">＋</span> 本版新增</div>
        <p>${inlineSide(hunk.inline_segments, "new", hunk.new_text)}</p>
      </div>`
    : "";

  return `
    <section class="diff-hunk" data-search-match="${String(matches)}">
      <p class="diff-context">${highlightSearch(hunk.context_label)}</p>
      ${oldBlock}
      ${newBlock}
    </section>
  `;
}

function renderTransition(transition) {
  const hunkHtml = transition.hunks.map(renderHunk).filter(Boolean);
  const hasChanges = transition.hunks.length > 0;
  const visibleForFilter = !state.changedOnly || hasChanges;
  const visibleForSearch =
    !state.query ||
    hunkHtml.length > 0 ||
    containsQuery(transition.newer.label, transition.older.label);
  if (!visibleForFilter || !visibleForSearch) return "";

  let body;
  if (hunkHtml.length > 0) {
    body = hunkHtml.join("");
  } else if (state.query && hasChanges) {
    return "";
  } else {
    body = `
      <div class="no-change">
        <span aria-hidden="true">＝</span>
        <p>
          <strong>未觀察到實質文字變更</strong>
          <span>正規化後全文相同；此版本仍保留在完整時間序列中。</span>
        </p>
      </div>`;
  }

  return `
    <article class="edition-row transition-row" data-has-changes="${String(hasChanges)}">
      <aside class="edition-meta">
        <p class="edition-meta__role">${dateRoleLabel(transition.newer.date)}</p>
        <strong>${highlightSearch(transition.newer.label)}</strong>
        <time datetime="${escapeHtml(transition.newer.date.date_value)}">${escapeHtml(transition.newer.date.date_value)}</time>
        <span class="compare-label">相較 ${highlightSearch(transition.older.label)}</span>
        <div class="source-pair">
          ${sourceLink(transition.older.source_url, "前一版")}
          ${sourceLink(transition.newer.source_url, "本版")}
        </div>
      </aside>
      <div class="edition-content">
        ${body}
        <p class="edge-caution">
          這是相鄰官方累積版本的文字比較；不宣稱兩者之間沒有其他法律事件。
        </p>
      </div>
    </article>
  `;
}

function renderHistory() {
  const html = state.data.transitions.map(renderTransition).filter(Boolean);
  els.transitionList.innerHTML =
    html.length > 0
      ? html.join("")
      : `<p class="empty-state">沒有符合目前搜尋／篩選條件的歷史變更。</p>`;
  return html.length;
}

function render() {
  if (!state.data) return;
  const latestMatches = renderLatest();
  const visibleTransitions = renderHistory();
  if (state.query) {
    els.searchStatus.textContent =
      `最新版命中 ${latestMatches} 段；歷史顯示 ${visibleTransitions} 個版本。`;
  } else {
    els.searchStatus.textContent = "";
  }
}

function renderHeader() {
  const coverage = state.data.coverage;
  els.editionCount.textContent = `${coverage.loaded_edition_count} 版`;
  els.edgeCount.textContent = `${coverage.adjacent_edge_count} 組`;
  els.scopeNote.innerHTML = `
    已完整載入明列的 <strong>${coverage.loaded_edition_count}</strong> 個官方累積版本，
    並建立 <strong>${coverage.adjacent_edge_count}</strong> 組相鄰版本 diff。
    這是「已宣告官方版本集」的完整對照，不代表官方公告來源宇宙已封閉，
    也不把版本標示日期偷換成法律生效日。
  `;
}

async function load() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (
      data.schema !== "nhi-rule-history/reader-projection/v1" ||
      data.generated_from !== "PostgreSQL nhi_rule_history_edition" ||
      data.rule.display_label !== "通則" ||
      data.rule.navigation_code_origin !== "project_assigned"
    ) {
      throw new Error("reader projection contract mismatch");
    }
    state.data = data;
    renderHeader();
    render();
    const allowedHashTargets = new Set(["#latest", "#history", "#method"]);
    if (allowedHashTargets.has(window.location.hash)) {
      window.requestAnimationFrame(() => {
        document.querySelector(window.location.hash)?.scrollIntoView();
      });
    }
  } catch (error) {
    console.error(error);
    els.loadError.hidden = false;
    els.latestText.innerHTML = "";
    els.transitionList.innerHTML = "";
  }
}

els.search.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  render();
});

els.changedOnly.addEventListener("change", (event) => {
  state.changedOnly = event.target.checked;
  render();
});

els.print.addEventListener("click", () => window.print());

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
    event.preventDefault();
    els.search.focus();
    els.search.select();
  }
});

load();
