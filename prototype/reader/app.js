const INDEX_URL = "./data/clauses/index.json";

const state = {
  index: null,
  data: null,
  query: "",
};

const els = {
  chapterLabel: document.querySelector("#chapter-label"),
  breadcrumbCode: document.querySelector("#breadcrumb-code"),
  clauseCode: document.querySelector("#clause-code"),
  pageTitle: document.querySelector("#page-title"),
  editionCount: document.querySelector("#edition-count"),
  versionCount: document.querySelector("#version-count"),
  scopeNote: document.querySelector("#scope-note p"),
  latestMeta: document.querySelector("#latest-meta"),
  latestText: document.querySelector("#latest-text"),
  transitionList: document.querySelector("#transition-list"),
  search: document.querySelector("#page-search"),
  searchStatus: document.querySelector("#search-status"),
  clauseResults: document.querySelector("#clause-results"),
  print: document.querySelector("#print-button"),
  loadError: document.querySelector("#load-error"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizedSearch(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/\s+/gu, "");
}

function sourceLink(url, label = "官方 ODT") {
  if (!url) return "";
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>`;
}

function editionRange(observations) {
  if (!observations?.length) return "來源版本未標示";
  const first = observations[0].edition_label;
  const last = observations.at(-1).edition_label;
  return first === last ? first : `${first}－${last}`;
}

function editionSource(observations, position = "last") {
  if (!observations?.length) return null;
  return position === "first" ? observations[0] : observations.at(-1);
}

function inlineSide(segments, side, fallback) {
  if (!segments?.length) return escapeHtml(fallback);
  const visible = segments.filter(
    (segment) => segment.side === "both" || segment.side === side,
  );
  if (!visible.length) return escapeHtml(fallback);
  return visible
    .map((segment) => {
      const content = escapeHtml(segment.text);
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
  const oldBlock = hunk.old_text
    ? `
      <div class="diff-side diff-side--old">
        <div class="diff-label"><span aria-hidden="true">−</span> 下一版刪除</div>
        <p>${inlineSide(hunk.inline_segments, "old", hunk.old_text)}</p>
      </div>`
    : "";
  const newBlock = hunk.new_text
    ? `
      <div class="diff-side diff-side--new">
        <div class="diff-label"><span aria-hidden="true">＋</span> 下一版新增</div>
        <p>${inlineSide(hunk.inline_segments, "new", hunk.new_text)}</p>
      </div>`
    : "";

  return `
    <section class="diff-hunk">
      <p class="diff-context">${escapeHtml(hunk.display_note || hunk.context_label)}</p>
      ${oldBlock}
      ${newBlock}
    </section>
  `;
}

function renderTransition(transition) {
  const olderRange = editionRange(transition.older.observed_editions);
  const newerRange = editionRange(transition.newer.observed_editions);
  const olderSource = editionSource(transition.older.observed_editions);
  const newerSource = editionSource(transition.newer.observed_editions, "first");
  const hunks = transition.hunks.map(renderHunk).join("");

  return `
    <article class="edition-row transition-row">
      <aside class="edition-meta">
        <p class="edition-meta__role">舊文字的來源觀察</p>
        <strong>${escapeHtml(olderRange)}</strong>
        <span class="compare-label">下一個文字版本：${escapeHtml(newerRange)}</span>
        <div class="source-pair">
          ${sourceLink(olderSource?.source?.official_url, "舊版來源")}
          ${sourceLink(newerSource?.source?.official_url, "下一版來源")}
        </div>
      </aside>
      <div class="edition-content">
        ${hunks}
        <p class="edge-caution">
          僅比較本條相鄰的不同文字狀態；未宣稱兩份來源之間沒有其他公告或法律事件。
        </p>
      </div>
    </article>
  `;
}

function renderHeader() {
  const { chapter, clause, coverage } = state.data;
  els.chapterLabel.textContent = chapter.display_label;
  els.breadcrumbCode.textContent = clause.canonical_code;
  els.clauseCode.textContent = clause.canonical_code;
  els.pageTitle.textContent = clause.display_title;
  document.title = `${clause.canonical_code} ${clause.display_title}｜健保給付條文歷史`;
  els.editionCount.textContent = `${coverage.observed_edition_count} 份`;
  els.versionCount.textContent = `${coverage.version_state_count} 版`;

  const unchangedNote =
    coverage.observed_edition_count === coverage.version_state_count
      ? ""
      : `其中相同文字的年度觀察已合併，因此不是 ${coverage.observed_edition_count} 個重複版本。`;
  els.scopeNote.innerHTML = `
    本條在明列的 <strong>${coverage.declared_edition_count}</strong> 份「通則」來源中，
    實際出現 <strong>${coverage.observed_edition_count}</strong> 份，
    共形成 <strong>${coverage.version_state_count}</strong> 個文字版本。
    ${escapeHtml(unchangedNote)}
    這不代表法律公告來源宇宙已封閉，也不把來源版名或文內日期自動認作生效日。
  `;
}

function renderLatest() {
  const latest = state.data.latest;
  const observations = latest.observed_editions;
  const latestSource = editionSource(observations);
  const observationNote =
    observations.length > 1
      ? `<span class="date-caution">同一文字連續見於 ${observations.length} 份來源</span>`
      : `<span class="date-caution">此文字目前只見於 1 份來源</span>`;

  els.latestMeta.innerHTML = `
    <p class="edition-meta__role">本條目前文字的來源觀察</p>
    <strong>${escapeHtml(editionRange(observations))}</strong>
    ${observationNote}
    ${sourceLink(latestSource?.source?.official_url)}
    ${sourceLink(latestSource?.source?.source_page_url, "官方來源頁")}
  `;

  els.latestText.innerHTML = latest.full_text_blocks
    .map((block, index) => {
      const className =
        index === 0 ? "rule-paragraph rule-paragraph--section" : "rule-paragraph";
      return `<p class="${className}">${escapeHtml(block.text)}</p>`;
    })
    .join("");
}

function renderHistory() {
  if (!state.data.transitions.length) {
    els.transitionList.innerHTML = `
      <div class="no-change">
        <span aria-hidden="true">＝</span>
        <p>
          <strong>目前沒有第二個文字版本</strong>
          <span>已保留所有來源觀察，但正規化後沒有可顯示的條文文字變更。</span>
        </p>
      </div>`;
    return;
  }
  els.transitionList.innerHTML = state.data.transitions
    .map(renderTransition)
    .join("");
}

function renderSearchResults() {
  const query = normalizedSearch(state.query);
  if (!query) {
    els.clauseResults.hidden = true;
    els.clauseResults.innerHTML = "";
    els.searchStatus.textContent = "";
    return;
  }

  const matches = state.index.clauses.filter((clause) =>
    normalizedSearch(clause.search_text).includes(query),
  );
  els.searchStatus.textContent = `找到 ${matches.length} 條；選擇後進入該條的全文與版本史。`;
  els.clauseResults.hidden = false;
  els.clauseResults.innerHTML = matches.length
    ? matches
        .map(
          (clause) => `
          <a class="clause-result" href="${escapeHtml(clause.reader_query)}"${
            clause.canonical_code === state.data.clause.canonical_code
              ? ' aria-current="page"'
              : ""
          }>
            <span class="clause-result__code">${escapeHtml(clause.canonical_code)}</span>
            <span class="clause-result__body">
              <strong>${escapeHtml(clause.display_title)}</strong>
              <small>${escapeHtml(clause.latest_excerpt)}</small>
            </span>
            <span class="clause-result__count">${clause.version_state_count} 個文字版本</span>
          </a>`,
        )
        .join("")
    : `<p class="empty-state">目前通則條文沒有符合「${escapeHtml(state.query)}」的結果。</p>`;
}

function validateIndex(index) {
  return (
    index.schema === "nhi-rule-history/single-clause-index/v1" &&
    index.generated_from === "PostgreSQL nhi_rule_history_clause" &&
    index.canonical_version_unit === "single_clause" &&
    index.chapter.display_label === "通則" &&
    index.chapter.navigation_code_origin === "project_assigned" &&
    Array.isArray(index.clauses)
  );
}

function validatePage(data, code) {
  return (
    data.schema === "nhi-rule-history/single-clause-reader/v1" &&
    data.generated_from === "PostgreSQL nhi_rule_history_clause" &&
    data.canonical_version_unit === "single_clause" &&
    data.chapter.display_label === "通則" &&
    data.chapter.navigation_code_origin === "project_assigned" &&
    data.clause.code_origin === "project_assigned" &&
    data.clause.canonical_code === code &&
    data.coverage.legal_history_complete === false
  );
}

function requestedClauseCode(index) {
  const requested = new URLSearchParams(window.location.search).get("rule");
  const available = new Set(index.clauses.map((clause) => clause.canonical_code));
  return requested && available.has(requested)
    ? requested
    : index.default_clause_code;
}

async function load() {
  try {
    const indexResponse = await fetch(INDEX_URL, { cache: "no-store" });
    if (!indexResponse.ok) throw new Error(`index HTTP ${indexResponse.status}`);
    const index = await indexResponse.json();
    if (!validateIndex(index)) throw new Error("clause index contract mismatch");

    const code = requestedClauseCode(index);
    const pageResponse = await fetch(
      `./data/clauses/${encodeURIComponent(code)}.json`,
      { cache: "no-store" },
    );
    if (!pageResponse.ok) throw new Error(`page HTTP ${pageResponse.status}`);
    const data = await pageResponse.json();
    if (!validatePage(data, code)) throw new Error("clause page contract mismatch");

    state.index = index;
    state.data = data;
    renderHeader();
    renderLatest();
    renderHistory();

    const allowedHashTargets = new Set(["#latest", "#history", "#method"]);
    if (allowedHashTargets.has(window.location.hash)) {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const target = document.querySelector(window.location.hash);
          if (!target) return;
          const previousBehavior =
            document.documentElement.style.scrollBehavior;
          document.documentElement.style.scrollBehavior = "auto";
          target.scrollIntoView();
          document.documentElement.style.scrollBehavior = previousBehavior;
        });
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
  renderSearchResults();
});

els.print.addEventListener("click", () => window.print());

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
    event.preventDefault();
    els.search.focus();
  }
});

load();
