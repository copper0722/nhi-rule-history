function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function parameters() {
  const params = new URLSearchParams(window.location.search);
  return {
    rule: params.get("rule") || "0.4",
    tag: params.get("tag") || "",
  };
}

function renderDrug(tag) {
  const codes = tag.terminology?.codes ?? [];
  return `
    <p class="tag-system">ATC</p>
    <div class="tag-code-list">
      ${codes
        .map(
          (item) => `
            <div class="tag-code">
              <strong>${escapeHtml(item.code)}</strong>
              <span>${escapeHtml(item.review_status)}</span>
            </div>
          `,
        )
        .join("")}
    </div>
    <p class="tag-explanation">
      這是本條使用到的 ATC 關聯，不是完整 ATC/DDD 索引。
    </p>
    ${
      codes[0]?.source_url
        ? `<a class="action-button action-button--primary" href="${escapeHtml(codes[0].source_url)}" target="_blank" rel="noopener noreferrer">健保署藥品查詢 ↗</a>`
        : ""
    }
  `;
}

function renderDisease(tag) {
  const terminology = tag.terminology;
  const codes = terminology.codes ?? [];
  return `
    <p class="tag-system">ICD-11</p>
    <div class="tag-code-list">
      ${
        codes.length
          ? codes
              .map(
                (item) => `
                  <div class="tag-code">
                    <strong>${escapeHtml(item.code)}</strong>
                    <span>${
                      item.mapping_status === "agent_selected"
                        ? "已確認關聯"
                        : "候選關聯 · 待人工確認"
                    }</span>
                  </div>
                `,
              )
              .join("")
          : `
              <div class="tag-code tag-code--pending">
                <strong>尚無單一適切 CODE</strong>
                <span>原條文用語過廣，保留為待判讀。</span>
              </div>
            `
      }
    </div>
    <p class="tag-explanation">
      公開頁只顯示本專案建立的 code 關聯；ICD-11 標題、URI、定義與參考資料仍只存於私有 PG。
    </p>
    <dl class="tag-query">
      <div>
        <dt>WHO 查詢詞</dt>
        <dd>${escapeHtml(terminology.lookup_query)}</dd>
      </div>
      <div>
        <dt>公開工具版本</dt>
        <dd>${escapeHtml(terminology.release)}</dd>
      </div>
    </dl>
    <a class="action-button action-button--primary" href="${escapeHtml(terminology.official_lookup_url)}" target="_blank" rel="noopener noreferrer">開啟 WHO ICD-11 Coding Tool ↗</a>
  `;
}

function renderTreatment(tag) {
  const terminology = tag.terminology;
  const codes = terminology.codes ?? [];
  return `
    <p class="tag-system">${escapeHtml(terminology.system_label)}</p>
    <div class="tag-code-list">
      ${codes
        .map(
          (item) => `
            <div class="tag-code">
              <strong>${escapeHtml(item.code)}</strong>
              <span>${item.is_primary ? "核心現行處置碼" : "相關支付碼"}</span>
              <small>${escapeHtml(item.name_zh)}</small>
            </div>
          `,
        )
        .join("")}
    </div>
    <p class="tag-explanation">
      同一治療概念可對應不同申報服務；本頁以 CAPD 追蹤處置碼為核心，並列出直接相關的現行支付碼。
    </p>
    ${
      codes[0]?.source_url
        ? `<a class="action-button action-button--primary" href="${escapeHtml(codes[0].source_url)}" target="_blank" rel="noopener noreferrer">開啟健保署支付標準資料 ↗</a>`
        : ""
    }
  `;
}

function tagTypeLabel(tagType) {
  return {
    drug: "藥品標籤",
    disease: "疾病標籤",
    treatment: "治療標籤",
  }[tagType] ?? "條文標籤";
}

function renderTerminology(tag) {
  if (tag.tag_type === "drug") return renderDrug(tag);
  if (tag.tag_type === "disease") return renderDisease(tag);
  return renderTreatment(tag);
}

async function loadTag() {
  const { rule, tag: tagId } = parameters();
  const card = document.querySelector("#tag-card");
  const error = document.querySelector("#tag-error");
  const back = document.querySelector("#tag-back");
  back.href = `./?rule=${encodeURIComponent(rule)}`;
  try {
    const response = await fetch(
      `./data/clauses/${encodeURIComponent(rule)}.json`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const tag = data.semantic_tags?.find((item) => item.tag_id === tagId);
    if (!tag) throw new Error("tag not found");
    document.title = `${tag.display_text}｜條文關鍵字`;
    card.innerHTML = `
      <p class="eyebrow">${tagTypeLabel(tag.tag_type)}</p>
      <h1>${escapeHtml(tag.display_text)}</h1>
      <p class="tag-context">
        出現在 ${escapeHtml(data.clause.canonical_code)}
        ${escapeHtml(data.clause.display_title)}
      </p>
      <section class="tag-terminology">
        ${renderTerminology(tag)}
      </section>
    `;
  } catch (loadError) {
    console.error(loadError);
    card.hidden = true;
    error.hidden = false;
  }
}

loadTag();
