const INDEX_URL = "./data/clauses/index.json";
const PUBLIC_DEMO_URL = "https://copper0722.github.io/nhi-rule-history/";
const FEEDBACK_URL =
  "https://github.com/copper0722/nhi-rule-history/issues/new";

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
  dockClauseCode: document.querySelector("#dock-clause-code"),
  dockPageTitle: document.querySelector("#dock-page-title"),
  mobileIslandCode: document.querySelector("#mobile-island-code"),
  mobileTocCode: document.querySelector("#mobile-toc-code"),
  mobileTocToggle: document.querySelector("#mobile-toc-toggle"),
  mobileTocClose: document.querySelector("#mobile-toc-close"),
  mobileTocDrawer: document.querySelector("#mobile-toc-drawer"),
  mobileSearchToggle: document.querySelector("#mobile-search-toggle"),
  mobileSearchClose: document.querySelector("#mobile-search-close"),
  mobileBackdrop: document.querySelector("#mobile-control-backdrop"),
  editionCount: document.querySelector("#edition-count"),
  versionCount: document.querySelector("#version-count"),
  scopeNote: document.querySelector("#scope-note p"),
  latestMeta: document.querySelector("#latest-meta"),
  latestText: document.querySelector("#latest-text"),
  agentSummary: document.querySelector("#agent-summary"),
  agentSummaryBody: document.querySelector("#agent-summary-body"),
  transitionList: document.querySelector("#transition-list"),
  diffIgnorePolicy: document.querySelector("#diff-ignore-policy"),
  search: document.querySelector("#page-search"),
  findBar: document.querySelector(".find-bar"),
  searchStatus: document.querySelector("#search-status"),
  clauseResults: document.querySelector("#clause-results"),
  print: document.querySelector("#print-button"),
  share: document.querySelector("#share-button"),
  shareStatus: document.querySelector("#share-status"),
  feedback: document.querySelector("#feedback-link"),
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

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

const LATIN_TOKEN_CHARACTER =
  /[\p{Script=Latin}\p{Script=Greek}\p{Number}]/u;

function isEmbeddedLatinToken(text, start, end) {
  const first = text.slice(start, start + 1);
  const last = text.slice(end - 1, end);
  const previous = text.slice(Math.max(0, start - 1), start);
  const next = text.slice(end, end + 1);
  return (
    (LATIN_TOKEN_CHARACTER.test(first) &&
      LATIN_TOKEN_CHARACTER.test(previous)) ||
    (LATIN_TOKEN_CHARACTER.test(last) && LATIN_TOKEN_CHARACTER.test(next))
  );
}

function terminologyLabel(tag) {
  if (tag.tag_type === "disease") {
    const codes = tag.terminology?.codes ?? [];
    if (!codes.length) return "ICD-11 待判讀";
    const suffix = codes.some((item) => item.mapping_status === "candidate")
      ? " · 候選"
      : "";
    return `ICD-11 ${codes.map((item) => item.code).join(" · ")}${suffix}`;
  }
  const codes = tag.terminology?.codes?.map((item) => item.code) ?? [];
  if (!codes.length) return "ATC 待核對";
  return codes.length === 1
    ? `ATC ${codes[0]}`
    : `ATC ${codes[0]} +${codes.length - 1}`;
}

function collectRichTextMatches(text) {
  const candidates = [];
  const tags = state.data?.semantic_tags ?? [];
  for (const tag of tags) {
    const pattern = new RegExp(escapeRegExp(tag.tag_text), "giu");
    for (const match of text.matchAll(pattern)) {
      const end = match.index + match[0].length;
      if (isEmbeddedLatinToken(text, match.index, end)) continue;
      candidates.push({
        start: match.index,
        end,
        kind: "tag",
        priority: 30,
        payload: tag,
      });
    }
  }
  const markers = state.data?.condition_markers ?? [];
  for (const marker of markers) {
    const pattern = new RegExp(escapeRegExp(marker.marker_text), "gu");
    for (const match of text.matchAll(pattern)) {
      candidates.push({
        start: match.index,
        end: match.index + match[0].length,
        kind: "condition",
        priority: 10,
        payload: marker,
      });
    }
  }
  const datePattern =
    /[（(]\s*\d{2,3}\s*\/\s*\d{1,2}\s*\/\s*\d{1,2}(?:\s*[、,，]\s*\d{2,3}\s*\/\s*\d{1,2}\s*\/\s*\d{1,2})*\s*[）)]/gu;
  for (const match of text.matchAll(datePattern)) {
    candidates.push({
      start: match.index,
      end: match.index + match[0].length,
      kind: "date",
      priority: 20,
      payload: null,
    });
  }
  candidates.sort(
    (left, right) =>
      left.start - right.start ||
      right.end - right.start - (left.end - left.start) ||
      right.priority - left.priority,
  );
  const selected = [];
  let cursor = 0;
  for (const candidate of candidates) {
    if (candidate.start < cursor) continue;
    selected.push(candidate);
    cursor = candidate.end;
  }
  return selected;
}

function renderRichText(value) {
  const text = String(value ?? "");
  const matches = collectRichTextMatches(text);
  if (!matches.length) return escapeHtml(text);
  const output = [];
  let cursor = 0;
  for (const match of matches) {
    output.push(escapeHtml(text.slice(cursor, match.start)));
    const matchedText = escapeHtml(text.slice(match.start, match.end));
    if (match.kind === "tag") {
      const tag = match.payload;
      output.push(
        `<a class="semantic-tag semantic-tag--${escapeHtml(tag.tag_type)}" href="${escapeHtml(tag.internal_url)}" title="${escapeHtml(terminologyLabel(tag))}"><span>${matchedText}</span><small>${escapeHtml(terminologyLabel(tag))}</small></a>`,
      );
    } else if (match.kind === "condition") {
      const marker = match.payload;
      output.push(
        `<mark class="condition-term condition-term--${escapeHtml(marker.semantic_role)}">${matchedText}</mark>`,
      );
    } else {
      output.push(
        `<small class="rule-date" title="條文內日期註記；尚未認定為法律生效日">${matchedText}</small>`,
      );
    }
    cursor = match.end;
  }
  output.push(escapeHtml(text.slice(cursor)));
  return output.join("");
}

function renderMarkdownInline(value) {
  return String(value ?? "")
    .split("**")
    .map((part, index) =>
      index % 2 ? `<strong>${renderRichText(part)}</strong>` : renderRichText(part),
    )
    .join("");
}

function renderLimitedMarkdown(markdown) {
  const lines = String(markdown ?? "").split(/\r?\n/u);
  const output = [];
  let listOpen = false;
  for (const line of lines) {
    if (line.startsWith("- ")) {
      if (!listOpen) {
        output.push("<ul>");
        listOpen = true;
      }
      output.push(`<li>${renderMarkdownInline(line.slice(2))}</li>`);
      continue;
    }
    if (listOpen) {
      output.push("</ul>");
      listOpen = false;
    }
    if (line.trim()) {
      output.push(`<p>${renderMarkdownInline(line)}</p>`);
    }
  }
  if (listOpen) output.push("</ul>");
  return output.join("");
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
  if (!segments?.length) return renderRichText(fallback);
  const visible = segments.filter(
    (segment) => segment.side === "both" || segment.side === side,
  );
  if (!visible.length) return renderRichText(fallback);
  return visible
    .map((segment) => {
      const content = renderRichText(segment.text);
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
  const kind = hunk.change_kind;
  if (kind === "format_only") return "";
  const showOld = kind === "removed" || kind === "replaced";
  const showNew = kind === "added" || kind === "replaced";
  const oldBlock = showOld && hunk.old_text
    ? `
      <div class="diff-side diff-side--old">
        <div class="diff-label"><span aria-hidden="true">−</span> 下一版刪除</div>
        <p>${inlineSide(hunk.inline_segments, "old", hunk.old_text)}</p>
      </div>`
    : "";
  const newBlock = showNew && hunk.new_text
    ? `
      <div class="diff-side diff-side--new">
        <div class="diff-label"><span aria-hidden="true">＋</span> 下一版新增</div>
        <p>${inlineSide(hunk.inline_segments, "new", hunk.new_text)}</p>
      </div>`
    : "";

  return `
    <section class="diff-hunk diff-hunk--${escapeHtml(kind)}">
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
  const dateLabel = transition.display_date?.label;
  const mainLabel = dateLabel || newerRange;
  const labelRole = dateLabel ? "條文內變更日期" : "下一文字版本的來源觀察";
  const dateCaution = dateLabel
    ? `<span class="date-caution">條文註記；尚未認定為法律生效日</span>`
    : "";

  return `
    <article class="edition-row transition-row">
      <aside class="edition-meta">
        <p class="edition-meta__role">${labelRole}</p>
        <strong>${escapeHtml(mainLabel)}</strong>
        ${dateCaution}
        <span class="compare-label">來源觀察：${escapeHtml(olderRange)} → ${escapeHtml(newerRange)}</span>
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
  els.dockClauseCode.textContent = clause.canonical_code;
  els.dockPageTitle.textContent = clause.display_title;
  els.mobileIslandCode.textContent = clause.canonical_code;
  els.mobileTocCode.textContent = clause.canonical_code;
  document.title = `${clause.canonical_code} ${clause.display_title}｜健保給付條文歷史`;
  els.editionCount.textContent = `${coverage.observed_edition_count} 份`;
  els.versionCount.textContent = `${coverage.version_state_count} 版`;
  const feedbackTitle = `0.4 給付條文 prototype 回饋：${clause.canonical_code}`;
  const feedbackBody = [
    `我查看的條文：${clause.canonical_code} ${clause.display_title}`,
    "",
    "我看不懂／容易誤解的地方：",
    "",
    "我建議的呈現方式：",
  ].join("\n");
  els.feedback.href =
    `${FEEDBACK_URL}?title=${encodeURIComponent(feedbackTitle)}` +
    `&body=${encodeURIComponent(feedbackBody)}`;

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
  els.diffIgnorePolicy.innerHTML = `
    <strong>不計入文字增刪：</strong>
    ${state.data.diff_policy.ignored_change_policy
      .map((item) => escapeHtml(item.label_zh))
      .join("、")}。
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
    .map((block) => {
      const content = renderRichText(block.text);
      if (block.render_kind === "clause_heading") {
        return `<h3 class="rule-heading">${content}</h3>`;
      }
      if (block.render_kind === "subsection") {
        return `<h4 class="rule-subsection">${content}</h4>`;
      }
      const className =
        block.render_kind === "list_item"
          ? "rule-paragraph rule-paragraph--list"
          : "rule-paragraph";
      return `<p class="${className}">${content}</p>`;
    })
    .join("");
}

function renderAgentSummary() {
  const summary = state.data.agent_history_summary;
  if (!summary) {
    els.agentSummary.hidden = true;
    els.agentSummaryBody.innerHTML = "";
    return;
  }
  els.agentSummary.hidden = false;
  els.agentSummaryBody.innerHTML = `
    ${renderLimitedMarkdown(summary.summary_markdown)}
    <p class="agent-summary__receipt">
      由 agent 依本頁相鄰版本 diff 產生；狀態：
      ${escapeHtml(summary.review_status)}。
    </p>
  `;
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

let mobilePanel = null;
let mobileReturnFocus = null;

function setMobilePanel(nextPanel, { restoreFocus = true } = {}) {
  const opening = nextPanel !== null;
  if (opening && mobilePanel === null) {
    mobileReturnFocus = document.activeElement;
  }
  mobilePanel = nextPanel;
  const tocOpen = nextPanel === "toc";
  const searchOpen = nextPanel === "search";

  els.mobileTocDrawer.classList.toggle("mobile-toc-drawer--open", tocOpen);
  els.mobileTocDrawer.setAttribute("aria-hidden", String(!tocOpen));
  els.mobileTocDrawer.inert = !tocOpen;
  els.mobileTocToggle.setAttribute("aria-expanded", String(tocOpen));
  els.findBar.classList.toggle("find-bar--mobile-open", searchOpen);
  els.mobileSearchToggle.setAttribute("aria-expanded", String(searchOpen));
  els.mobileBackdrop.classList.toggle(
    "mobile-control-backdrop--open",
    opening,
  );
  els.mobileBackdrop.setAttribute("aria-hidden", String(!opening));
  document.body.classList.toggle("mobile-panel-open", opening);

  if (tocOpen) {
    window.requestAnimationFrame(() => els.mobileTocClose.focus());
  } else if (searchOpen) {
    window.requestAnimationFrame(() => els.search.focus());
  } else if (
    restoreFocus &&
    mobileReturnFocus instanceof HTMLElement &&
    mobileReturnFocus.isConnected
  ) {
    mobileReturnFocus.focus();
  }
  if (!opening) mobileReturnFocus = null;
}

function initializeMobileTocScrollspy() {
  const links = Array.from(
    document.querySelectorAll("[data-mobile-toc-target]"),
  );
  const targetLinks = new Map(
    links.map((link) => [link.dataset.mobileTocTarget, link]),
  );
  const sections = Array.from(targetLinks.keys())
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  if (sections.length < 3) return;

  function selectSection(id) {
    for (const [target, link] of targetLinks) {
      if (target === id) {
        link.setAttribute("aria-current", "location");
      } else {
        link.removeAttribute("aria-current");
      }
    }
  }

  function selectFromScrollPosition() {
    const readingLine = window.innerHeight * 0.28;
    let current = sections[0];
    for (const section of sections) {
      if (section.getBoundingClientRect().top <= readingLine) {
        current = section;
      } else {
        break;
      }
    }
    selectSection(current.id);
  }

  let scrollUpdatePending = false;
  function requestScrollspyUpdate() {
    if (scrollUpdatePending) return;
    scrollUpdatePending = true;
    window.requestAnimationFrame(() => {
      selectFromScrollPosition();
      scrollUpdatePending = false;
    });
  }

  const observer = new IntersectionObserver(
    () => requestScrollspyUpdate(),
    {
      rootMargin: "-18% 0px -68% 0px",
      threshold: [0, 0.01],
    },
  );
  for (const section of sections) observer.observe(section);
  window.addEventListener("scroll", requestScrollspyUpdate, { passive: true });
  window.addEventListener("resize", requestScrollspyUpdate);
  window.addEventListener("hashchange", requestScrollspyUpdate);
  selectFromScrollPosition();
}

function validateIndex(index) {
  return (
    index.schema === "nhi-rule-history/single-clause-index/v1" &&
    index.generated_from === "PostgreSQL nhi_rule_history_clause" &&
    index.canonical_version_unit === "single_clause" &&
    index.diff_policy?.algorithm_version ===
      "chapter-00-semantic-diff-presentation/v2" &&
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
    data.diff_policy?.algorithm_version ===
      "chapter-00-semantic-diff-presentation/v2" &&
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
    renderAgentSummary();
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

els.mobileTocToggle.addEventListener("click", () => {
  setMobilePanel(mobilePanel === "toc" ? null : "toc");
});

els.mobileSearchToggle.addEventListener("click", () => {
  setMobilePanel(mobilePanel === "search" ? null : "search");
});

els.mobileTocClose.addEventListener("click", () => setMobilePanel(null));
els.mobileSearchClose.addEventListener("click", () => setMobilePanel(null));
els.mobileBackdrop.addEventListener("click", () => setMobilePanel(null));

for (const link of document.querySelectorAll("[data-mobile-toc-target]")) {
  link.addEventListener("click", () => {
    setMobilePanel(null, { restoreFocus: false });
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && mobilePanel !== null) {
    event.preventDefault();
    setMobilePanel(null);
  }
});

window.matchMedia("(max-width: 700px)").addEventListener("change", (event) => {
  if (!event.matches && mobilePanel !== null) setMobilePanel(null);
});

initializeMobileTocScrollspy();

els.print.addEventListener("click", () => window.print());

els.share.addEventListener("click", async () => {
  const code = state.data?.clause?.canonical_code || "0.4";
  const url = `${PUBLIC_DEMO_URL}?rule=${encodeURIComponent(code)}`;
  try {
    await navigator.clipboard.writeText(url);
    els.shareStatus.textContent = "已複製，可貼到 Facebook 分享。";
  } catch {
    window.prompt("複製這個示範網址", url);
    els.shareStatus.textContent = "";
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
    event.preventDefault();
    els.search.focus();
  }
});

load();
