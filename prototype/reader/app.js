const currentRule = `
  <p>
    <mark>單獨使用於具有 FLT3 突變的復發性或難治性急性骨髓性白血病
    (R/R AML)</mark> 且計畫進行造血幹細胞移植的成年病人，限移植前使用：
  </p>
  <ol>
    <li>
      每位病人限給付 6 個療程。病人須至少接受過一次含 anthracycline
      藥物的化學治療。
    </li>
    <li>
      須經事前審查核准後使用，初次申請時須檢附：
      <ol>
        <li>相關病歷資料。</li>
        <li>
          完整之造血幹細胞移植計畫，並詳細記載確認捐贈者名單及移植前調適治療等資料。
          需由具訓練血液及骨髓移植醫師能力之醫院申請，並由完成血液及骨髓移植訓練之醫師確認移植計畫。
        </li>
        <li>
          染色體檢驗報告，若為 unfavorable karyotype（包含 complex
          karyotype、-5、-5q、-7、-7q、除 t(9;11) 外的 11q23
          abnormalities、inv(3)、(3;3)、t(6;9) 以及 t(9;22) 等）則不予給付。
        </li>
        <li>
          檢附之 FLT3 突變檢測結果報告，需符合全民健康保險藥品給付規定之通則十二。
        </li>
      </ol>
    </li>
    <li>
      每次申請為二個療程；續申請次二個療程時須檢附達到 PR、CRi 或 CR
      的證明方可續用。申請劑量以每日 120 mg 為上限。
    </li>
  </ol>
`;

const announcedRule = `
  <p>
    <mark>單獨使用於具有 FLT3 突變的復發性或難治性急性骨髓性白血病
    (R/R AML)</mark>，限用於：
  </p>
  <ol>
    <li>
      計畫進行造血幹細胞移植的成人病人，移植前使用，每位病人限給付 6
      個療程。病人須至少接受過一次含 anthracycline 藥物的化學治療。
    </li>
  </ol>
  <div class="legal-section" data-change="add">
    <p class="change-note">＋ 本版新增 · 移植後維持治療</p>
    <ol start="2">
      <li>
        移植後的維持治療，病人移植前需曾使用 gilteritinib 至少 1
        個療程，移植後 30 天內未發生疾病惡化，且至少達到複合完全緩解
        （Composite CR）方可繼續申請使用。
      </li>
    </ol>
  </div>
  <div class="legal-section" data-change="add">
    <p class="change-note">↔ 本版改寫 · 每次申請改為 3 個療程</p>
    <p>
      須經事前審查核准後使用，每次申請為 3 個療程，每個療程為 1 個月，
      申請劑量以每日 120 mg 為上限：
    </p>
  </div>
  <ol>
    <li>
      移植前使用之病人需檢附：
      <ol>
        <li>初次申請：相關病歷資料。</li>
        <li>
          完整之造血幹細胞移植計畫，並詳細記載確認捐贈者名單及移植前調適治療等資料。
          需由具訓練血液及骨髓移植醫師能力之醫院申請，並由完成血液及骨髓移植訓練之醫師確認移植計畫。
        </li>
        <li>
          染色體檢驗報告，若為 unfavorable karyotype（包含 complex
          karyotype、-5、-5q、-7、-7q、除 t(9;11) 外的 11q23
          abnormalities、inv(3)、(3;3)、t(6;9) 以及 t(9;22) 等）則不予給付。
        </li>
        <li>
          申請續用次 3 個療程時須檢附達到 PR、CRi 或 CR 的證明方可續用。
        </li>
      </ol>
    </li>
  </ol>
  <div class="legal-section" data-change="add">
    <p class="change-note">＋ 本版新增 · 移植後申請文件</p>
    <ol start="2">
      <li>
        移植後使用於維持治療之病人需檢附：
        <ol>
          <li>初次申請：相關病理與病歷資料、使用 gilteritinib 的用藥紀錄。</li>
          <li>
            申請續用：療效評估資料證明無疾病復發；3 個月內微量殘留病灶
            （MRD）檢驗報告。若 MRD 為陽性得繼續使用；若為陰性得續用並再觀察
            3 個療程，如再次評估仍為陰性，則停止使用。
          </li>
        </ol>
      </li>
    </ol>
  </div>
  <p>
    檢附之 FLT3 突變檢測結果報告，需符合全民健康保險藥品給付規定之通則十二。
  </p>
`;

const state = {
  version: "current",
};

const els = {
  ruleText: document.querySelector("#current-rule-text"),
  dateLabel: document.querySelector("#current-date-label"),
  statusPill: document.querySelector("#current-status-pill"),
  eyebrow: document.querySelector("#current-version-eyebrow"),
  title: document.querySelector("#current-version-title"),
  status: document.querySelector("#version-status"),
  currentButton: document.querySelector("#show-current"),
  announcedButton: document.querySelector("#show-announced"),
  noticeButton: document.querySelector("#notice-show-announced"),
  oldFullButton: document.querySelector("#show-old-full"),
  newFullButton: document.querySelector("#show-new-full"),
  search: document.querySelector("#rule-search"),
  results: document.querySelector("#search-results"),
  print: document.querySelector(".print-button"),
};

function setVersion(version, shouldScroll = false) {
  state.version = version;
  const announced = version === "announced";

  els.ruleText.innerHTML = announced ? announcedRule : currentRule;
  els.dateLabel.textContent = announced ? "2026.08.01 起" : "2024.06.01 起";
  els.eyebrow.textContent = announced ? "最新公告版本" : "目前有效版本";
  els.title.textContent = announced
    ? "給付條文全文（尚未生效）"
    : "給付條文全文";
  els.status.textContent = announced
    ? "這是已公告、將於 2026 年 8 月 1 日生效的版本；今天尚不適用。"
    : "今天適用 2024 年 6 月 1 日版；另有一版已公告、將於 2026 年 8 月 1 日生效。";

  els.statusPill.textContent = announced ? "尚未生效" : "今天適用";
  els.statusPill.classList.toggle("status-pill--current", !announced);
  els.statusPill.classList.toggle("status-pill--future", announced);

  els.currentButton.classList.toggle("is-active", !announced);
  els.announcedButton.classList.toggle("is-active", announced);
  els.currentButton.setAttribute("aria-pressed", String(!announced));
  els.announcedButton.setAttribute("aria-pressed", String(announced));

  if (shouldScroll) {
    document
      .querySelector("#current-version")
      .scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function updateSearch() {
  const term = els.search.value.trim().toLowerCase();
  const isMatch = [
    "gilteritinib",
    "xospata",
    "flt3",
    "急性骨髓",
    "白血病",
  ].some((candidate) => candidate.includes(term) || term.includes(candidate));

  els.results.hidden = term.length === 0 || !isMatch;
  els.search.setAttribute(
    "aria-expanded",
    String(term.length > 0 && isMatch),
  );

  const reason = document.querySelector(".match-reason");
  if (term.includes("xospata")) reason.textContent = "商品名相符";
  else if (term.includes("白血病") || term.includes("flt3"))
    reason.textContent = "適應症相符";
  else reason.textContent = "成分名相符";
}

els.currentButton.addEventListener("click", () => setVersion("current"));
els.announcedButton.addEventListener("click", () => setVersion("announced"));
els.noticeButton.addEventListener("click", () =>
  setVersion("announced", true),
);
els.oldFullButton.addEventListener("click", () => setVersion("current", true));
els.newFullButton.addEventListener("click", () =>
  setVersion("announced", true),
);
els.search.addEventListener("input", updateSearch);
els.print.addEventListener("click", () => window.print());

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    els.search.focus();
    els.search.select();
  }
});

const navLinks = [...document.querySelectorAll(".section-nav a")];
const sections = navLinks
  .map((link) => document.querySelector(link.getAttribute("href")))
  .filter(Boolean);

const observer = new IntersectionObserver(
  (entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    navLinks.forEach((link) => {
      link.classList.toggle(
        "is-active",
        link.getAttribute("href") === `#${visible.target.id}`,
      );
    });
  },
  { rootMargin: "-28% 0px -60% 0px", threshold: [0.01, 0.2] },
);

sections.forEach((section) => observer.observe(section));
setVersion("current");
