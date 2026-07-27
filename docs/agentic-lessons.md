# Agentic 重建：Grok pilot 與驗收心得

這個專案把 LLM agent 當作可加速大量整理與實作的工作者，不把模型輸出本身
當作資料正確性的證據。Grok pilot 的價值，在於快速完成 14 份 ODT 的 XML
結構盤點、候選條號抽取、資料清理與 staging loader；真正能接受進資料庫的
版本，則來自可重跑輸出、獨立審查與逐項 gate。

## 適合交給 agent 的 groundwork

- 列舉 ODT 的段落、表格、frame、list 與 XML producer 差異；
- 產生 deterministic parser、manifest、issue ledger 與測試樣本；
- 將重複、疑似條號與解析異常保留成候選，不急著決定法律身分；
- 依明確 schema 實作批次載入、fingerprint 與回滾工具；
- 對大量資料做 checksum、row-count 與 locator round trip。

## Pilot 揭露的典型失敗

第一版看似能完成大量抽取，獨立審查仍找到高風險問題：

- frame／text box 內的巢狀段落可能被併入外層，失去獨立 locator；
- 空白、covered、row/column-spanned cell 可能只被計數，沒有可重建位置；
- `0.2 mL`、`0.5 mg` 等數值可能被 dotted-number regex 誤判為條號；
- CLI 的測試 bypass 若暴露給 production，可能把全文寫到錯誤目錄；
- receipt 若用 denylist 過濾，schema 演進後可能洩漏未列入的原文欄位；
- 只算 `.odt` 可能忽略來源目錄裡多出的 artifact；
- PostgreSQL code 可能在 mock 測試通過，卻使用真實 driver 不支援的介面；
- validate 與 apply 間若未重算 bytes，會有同 row count 但內容已改的 TOCTOU；
- 「sealed」若只是一個狀態欄位，沒有 trigger/privilege，實際上仍可被改寫；
- fingerprint 若沒有綁定 migration/schema，不能證明同一份資料契約；
- rollback 若用廣域 `DROP ... CASCADE`，可能傷及 schema 外依賴。

## 已固化的改進

現行 stage parser/loader 將上述問題轉成程式約束與回歸測試：

- 巢狀 `p/h` 各自成 block，外層文字不吸收內層條號；
- empty／covered cells 形成 `empty_table_cell` block，locator 保存 repeat 與
  row/column span；
- 數值加單位由分類器拒絕，不進 occurrence candidate；
- production CLI 永不開放 unrestricted stage；
- tracked receipts 使用 top-level 與 nested key allowlist；
- manifest、artifact set、bytes、row count、stream fingerprint 在 apply 時重驗；
- psycopg3 路徑以 cursor 執行批次寫入，並有無 `Connection.executemany` 的測試；
- sealed child rows 由 trigger 與權限保持 append-only；
- code/schema fingerprint、composite artifact/block FK、共同 advisory lock 與
  bounded rollback 納入 migration/loader 契約。

## 2026-07-27 v2 live run 的新反例

後續公告的真實資料又補了三個 synthetic fixture 沒抓到的問題：

- MOHW attachment 常以 `Content-Type: text/html` 回傳真正的 PDF／ODT；
  detector 必須 magic-first，header 只保留為 observation。
- 一份 2023 ODT 使用 `text:list-header`；v1 annual corpus 沒出現這種
  LibreOffice 結構。generic v2 walker 補上後，358/358 unique ODT artifacts
  才達到全 `p/h` coverage。
- FINT `RowNo` 在兩次相同 query 間會重排。第一版把 RowNo／parent ordinal
  放入 resource ID，造成同為 1,719 個資源卻有 344 個 key 不一致。detail
  改用 formal document number、attachment 改用 canonical PFID URL 後，
  兩次獨立 enumeration 才達成 key-set parity。

這三項都來自真實 endpoint／真實檔案，而不是 agent 自評。第一個已成 MIME
regression test，第二個由 production corpus coverage gate 驗證，第三個由
`compare-discovery` 雙輪測試與 live parity receipt 固化。

## 可重用的指揮原則

1. 給 agent 一個狹窄、可列舉、可 checksum 的工作包。
2. producer 不得自行宣告 promotion；由另一個 agent 或測試者做 adversarial
   audit。
3. 要求「真實檔案反例」而不只 synthetic happy path。
4. 每個 audit finding 都要變成 regression test、schema constraint 或明確
   gap；不能只寫在聊天紀錄。
5. 模型可提出候選 identity、日期、linkage、diff，但 official locator 與
   deterministic replay 才能升格為公開資料。
6. 每次更新保留輸入、程式、schema、輸出四層 fingerprint，避免「同數量」
   被誤認為「同內容」。
7. 動態 listing 的 row number、頁碼與排序只能當 locator；resource identity
   要用 authority-native key（文號、PFID）並由第二次獨立 enumeration 反證。

這套分工讓 Grok 這類 agent 的速度用在 groundwork，同時把法律歷史的最終
聲明鎖在可驗證 evidence，而不是鎖在模型自信。

## 2026-07-27 recurring worker canary

連續更新 lane 另外用真實公告測試 cm1 Claude primary 與 hm4 Codex
failure-only fallback。結果不是比較誰「回答較好」，而是觀察各模型在同一個
嚴格 JSON／exact-span contract 下能否穩定完成 bounded work：

- primary 已有成功案例，也出現一次 schema／span contract failure及一次
  600 秒 timeout；
- fallback 只在前一 attempt 已明確失敗後啟動，兩次都產生通過 deterministic
  validator 的 proposal；
- cabazitaxel 的部分修訂，以及同時涵蓋 carboplatin、pemetrexed 與免疫
  檢查點抑制劑的多條文公告，都正確停在 `needs_review`；
- fallback success 只證明可用性恢復，不能取代獨立法律身分、相鄰版本及
  anchor replay 審查。

這組 canary 顯示，最重要的能力不是模型能否寫出看似完整的 JSON，而是
controller 能否拒絕不合格輸出、保存失敗 attempt，並讓下一個模型從同一份
公開來源封包重做。對大型多條文公告，後續應在 deterministic controller
先依 source designation 切成數個 clause-scoped work packets；完整附件
inventory 與共同公告 metadata 仍保留，但每個 worker 只處理一個可驗證
effect scope。這可降低 primary timeout，也避免單一 proposal 把 split／merge
風險混成普通 replacement。

## 2026-07-27 歷史回溯與 promotion audit 的再蒸餾

把 query window 往 1996 年回溯後，又出現四類必須固化成 skill/checklist
的反例：

- FINT 沒有結果時仍會回傳空的 `<table id="dat04">`。只看 selector 存在會
  捏造一筆 record；adapter 必須區分 empty table、valid row 與 selector
  drift，並把 0-row partition 留在 manifest。
- 官方 `GetFile.ashx` 對早期 GIF/JPEG/TIFF 也可能宣告 `text/html`。
  Magic-first 不只適用 PDF/ODT；已知政府附件格式的 signature matrix 必須
  完整，未知格式只能進 gap，不能用 header 猜。
- NHI 最新「整份版」與「分章版」都由官方發布，但完整重建後 639 條中有
  33 個 designation hashes 不同。兩個 authoritative surfaces 衝突時，
  agent 不得替 owner 選邊；要產生 source-span discrepancy tickets，
  回查公告與生效時間。
- 第一次 promotion schema 的測試雖全過，獨立 adversarial reviewer 仍能
  找到 self-attested ODT-only、endpoint parity 冒充 event replay、
  rollback TOCTOU 及 security-state fingerprint 漏項。Migration review
  skill 必須固定測試「另一個 authenticated writer 在 rollback check/drop
  中插入」、「停用 trigger」、「開啟 RLS」、「任意 replay count/hash」
  與「producer/reviewer/executor 角色重疊」，不能只看 happy-path role grant。

對後續 agent 的明確改進：

1. Discovery adapter fixture 必須包含 empty、reordered、duplicate boundary、
   selector drift 與 content-type lie。
2. 「parity」一律在名稱中標明比較的層級：resource-key、header occurrence、
   full clause endpoint，或 ordered event replay；不得省略層級。
3. Absence claim 必須來自 sealed exhaustive inventory。PG 裡「沒有 PDF
   row」不能證明官方只提供 ODT。
4. Rollback 的 emptiness gate 要先取得會阻擋 writer 的 locks，再檢查、再
   drop；check-then-lock 是資料遺失風險。
5. Schema fingerprint 必須涵蓋 security-relevant catalog state，不只
   columns/functions 的表面定義。

同輪執行還補了兩個 workflow 級教訓：

- `sources/source-plan-v2.json` 在後續啟用 current-anchor adapters 後，已不再
  等於先前 post-109 raw run 的 exact input bytes。幸好 raw manifest 保存
  `source_plan_sha256`，materializer 因 hash 不符而 fail closed。每個 accepted
  run 都應把原始 source plan 以不可變檔名另存；working plan 只能用於下一輪，
  不能悄悄代表舊 run。
- 整份／分章 33 個 mismatch designations 若只看數量，容易被誤讀成 33 個
  法律差異。階層 hash 會把 19 個 leaf differences 傳播到 14 個 ancestors；
  leaf exact diff 又分成 6 個版本／日期內容、6 個 list-marker 結構、6 個
  純標點與 1 個尾端補充表 layout。Agent 必須先把 provenance-preserving
  diff 與法律裁決分開，尤其 future-effective 文字不能因出現在較新的官方
  檔案就提前成為 current。

Promotion schema 的第二輪 adversarial review 又證明「把上一輪 finding
字面補上」仍不夠：

- attachment inventory 若只算特定 role，將 PDF 改標 `supporting` 就能繞過
  ODT-only gate；inventory 必須從 release-linked official attachment 全集
  推導，格式文字也不能靠一段任意 exact span 自我聲明；
- ordered event ID／count／fingerprint 相同，仍不等於逐條套用
  `before_hash → after_hash`；replay 要維護每條 running state 並和 post
  anchor 收斂；
- trigger/RLS fingerprint 完整，也可能漏掉 `UNLOGGED` durability drift 與
  owner-role membership backdoor；
- 只限制 effective date 不在未來，仍可能讓 publication/document dates
  在未來的假 event 通過。

後續 migration-audit skill 應固定加入 hidden-role attachment、companion-rule
unapplied event、`SET UNLOGGED`、owner membership、future publication date
五個 adversarial fixtures。完整 finding 見
[`2026-07-27-canonical-promotion-independent-review.md`](audits/2026-07-27-canonical-promotion-independent-review.md)。

第三輪工作又補上兩組可移植教訓：

- Listing adapter 不能從舊 selector 或網址外觀推演。NHI live surface 已是
  `section.list > table.rwdTable` 與
  `lp-3258-1.html?pi=N&ps=20`；pagination section 同時含每頁 20/40/60
  links，若把所有數字 anchor 都當頁碼，走到第 19 頁才會出現隱蔽衝突。
  六欄又同時有發文日、刊登日、刊登期限，且 858 rows 中有一筆期限空值。
  Skill 應先從 live DOM 建 exact contract，再用全頁真實 crawl 驗證 header、
  1..N 顯示序號、total/page declaration、query parameters 與兩輪 row-set
  parity；不可只用第一頁 synthetic fixture 宣告完成。
- Promotion suite 第二次全綠後，獨立 reviewer 仍成功 promotion 三個惡意
  fixture：`exhaustive_verified` 內被 quarantine 的 supporting PDF、anchor
  文字已改但沒有 accepted event 的 companion rule，以及原文未來
  `effective_date_raw` 被正規化成過去日期。Audit skill 必須檢查「全集中
  每個被隔離成員仍阻擋」、「所有 changed anchor members 都有完整 running
  chain」，並對每個 source-bound raw temporal value 做 deterministic parse
  與 normalized equality；不能把 candidate 自填的 date 欄位當真。

這兩組反例也說明 independent review 不是 reviewer 讀過 SQL 即完成，而是要
在 disposable PG 實際嘗試讓壞資料 promotion。Declared suite 的通過數只能
證明已知不變量，不能取代另一個人設計的攻擊資料。重現與 exact finding 見
[`2026-07-27-canonical-promotion-independent-rereview.md`](audits/2026-07-27-canonical-promotion-independent-rereview.md)。

日期完整性檢查與第四輪 promotion review 再補三項 skill 改進：

- slash-triplet regex 的「invalid date」不一定是錯印日期。這次六筆全是
  Trelegy Ellipta `92/55/22`／`184/55/22 mcg` 劑量。Date-marker skill
  必須分成 raw pattern、calendar validation、context adjudication 三層；
  raw 分母保留 6,366 供稽核，公告解析分母則是 6,360，不能讓劑量永遠卡在
  unresolved queue。
- 同檔日期＋條號 co-occurrence 之後，必須沿 artifact→resource→parent
  detail→official document number 回溯。這次 909 組全部可追到文號，但
  只有 490 組唯一、419 組仍有 2–11 個候選。Unique owning document 只是
  人工閱讀優先序，不是 amendment effect；同 bytes 被多份公文引用時必須
  保留所有 provenance，不能任選一份。
- 「byte-derived receipt」若仍由同一 caller 首次寫入，只是 immutable
  self-attestation。第四次獨立攻擊用真 PDF／ODT bytes 搭配自洽的
  `opaque` receipt 仍能繞過。Promotion skill 應要求不同 SESSION_USER 的
  detector/reviewer capability、SECURITY DEFINER 函數直接重算 raw bytes
  SHA/length/magic/mimetype、兩份 immutable receipts，且 promotion 重新
  驗證函式定義 hash；作者的 receipt producer 不得同時充當測試 oracle。
- 兩個獨立角色若共用同一個過度寬鬆的格式判定邏輯，仍會一起錯。第五次
  review 的 90-byte `BadZipFile` 同時含 `PK` magic 與 ODT mimetype 字串，
  兩位 classifier 都判成 ODT，promotion 因「一致」而通過。格式驗證不能
  停在 magic＋substring；ODT 必須驗 central directory、第一個 uncompressed
  `mimetype` entry、exact mimetype、必要 `content.xml`／manifest entries、
  offset/length 與 path safety。Independence 要求不同 authority，也要求
  判定 contract 本身足以識別有效 container。
- 合法 container structure 仍不等於 payload 完整。第六次 review 將
  deflated `content.xml` 破壞到 CRC 錯誤，但不改外層 central/local metadata；
  兩個 SQL parser 仍接受並產生 canonical receipt。若執行環境不能獨立
  解壓所有 payload、驗 CRC 並解析必要 XML，skill 不應在資料庫內手寫
  inflate 或保留「stored ODT 可過」的例外。最小安全邊界是所有 ODT/ODS
  observation-only、promotion receipt 固定為 0，直到 governed external
  archive verifier、簽署 receipt 與獨立 replay contract 完成。
- 同一原則適用 PDF：magic 不是 structural validity。第七次 review 用只有
  `%PDF-` 前綴、沒有版本、xref、trailer、catalog、page tree 或 EOF 的
51-byte 假檔；兩個 SQL classifier 一致判 PDF 並 promotion。修補不應把
更多 PDF parser 細節手寫進 PG；沒有 bounded full-document parser 與真正
獨立 oracle 時，合法 PDF 與假 PDF 都只能 observation-only。Skill 的
成功測試必須包含 valid、truncated、bad-xref、missing-trailer／catalog
與 decoy cases，且在 verifier 尚未完成時全部 canonical receipt=0。

## 2026-07-27 missing-號 live incident 與 gap queue

真實 recurring lane 又提供一個 synthetic fixture 沒有的 metadata 反例：
官方公告 `cp-20264-0cbbf-3258-1` 的 `發文字號` 是
`健保審字第1150671800`，沒有一般常見的末尾 `號`。原 strict parser 正確
fail closed，但同一筆 `acquired` work item 會一直被排在最前面，使後面五筆
selected items 飢餓。修補必須同時處理 provenance 與 queue liveness：

- exact `reference_number_raw` 永遠保留；
- normalized identifier 只允許移除 whitespace 與補一個末尾 `號`；
- normalization reason 與 versioned rule 另存，source UID 只取 normalized
  form；
- 其他 prefix、compound number、尾註或多值仍拒絕；
- raw／normalized collision 不能靠相同 UID 靜默合併，仍須比對 URL、
  subject、dates 與 artifact hashes。

Grok 4.5 與 Gemini 3.1 Pro 都支持這個狹窄 contract，也都同意把 3,080 個
clause-date pairs 做成候選工作佇列，而非宣稱已解析法律沿革。可接受的共同
分層是 490 個 unique-document hints、419 個 ambiguous-document hints、
1,125 個 native-date/no-joint candidates，以及 1,046 個 marker-only
candidates。這些層級只決定閱讀順序，不改變所有 work units 的
`official_event_unresolved`、`direct_predecessor_unresolved` 與
`canonical_write_authorized=false`。

這次也再次顯示外部模型的能力邊界：

- Grok 的 direct fetch 被 Cloudflare 擋住，只能用搜尋索引重建頁面觀察；
  controller 手上的 sealed exact HTML 才是較強證據。
- Gemini 的說明混用了近似 OCR／unmapped 數量；接受前仍須用 3,080、
  2,034、909 三個正式分母重算，不能把模型的 approximate partition 寫進
  manifest。
- Grok 建議 weighted priority score，但 categorical evidence lanes 更容易
  重播與稽核；沒有驗證過的權重不應製造看似精密的 ranking。
- 兩個模型都可發現 normalization、collision 與 LLM authority 風險，但
  parser、queue counts、hash binding、PG transition 仍由確定性程式驗收。

## 2026-07-28 terminal-句號 canary 與版本分流

第一筆正式來源 canary 的公告欄位寫成
`健保審字第1150055418號。`。來源 bundle 與四份附件均已完整封存，但
v1.2 metadata parser 因尾端全形句號而 fail closed；同一頁也揭露舊 parser
只取 `公告事項` 第一段，會漏掉第二段真正的 cefiderocol 給付規定修訂。

修補不改寫 v1.2 語意，而新增 corpus manifest v1.3：

- raw 發文字號完整保留；
- 正規化只容許固定 whitespace 集合、恰一個末尾 U+3002 `。`，以及既有
  缺 `號` 補字；每一步都有固定順序與 reason；
- v1.2 固定 rule 1.0.0，v1.3 固定 rule 1.1.0，兩者不可互換；
- `raw.md`、manifest、Python registrar 與 PG registrar 必須重算一致；
- `公告事項` 只從同一 `th`／`td` row 取值並保留所有段落；不把全頁文字
  順序當成欄位邊界；
- cell 內的 `p`／`li`／`div`、`br` 與裸文字依原順序各保留一次；
  `dt`／`dd` 必須屬於同一個 `dl`，避免跨區塊誤配；
- `號。。`、ASCII full stop、內嵌句號、尾註與前綴句號都成為負例。

這次 canary 的價值不是「模型看懂了 cefiderocol」，而是正式來源在任何
模型呼叫前，先找出 metadata 與段落邊界的合成 fixture 缺口。正確的修補
單位因此是版本化 parser、registrar、migration、rollback 與真實 bundle
replay，而不是針對單一公告硬編字串替換。

Replay 也不能只驗 manifest 自己宣告的 hashes。若 `raw.md` 與其兩組
hash／size 一起被改寫，self-consistency 仍可能成立；真正的可重播性必須
從 sealed source 依原 manifest 版本的凍結 renderer 重建，要求逐位元相同，
並拒絕 target、manifest 或 payload symlink。這是「內容來源綁定」與
「檔案自洽」的差別。

後續 model-harness packet 應直接附 sealed source hash、exact value 與正式
count equations；output contract 要求逐項算術 reconciliation，近似值只能
進 uncertainties。模型結果若要長期保存，dispatcher 還應有原生 output-file
參數，避免只留在 terminal capture。

第一批五個 Grok 實檔 pilot 又找到一個 schema 命名陷阱：模型能正確讀出
1996–1997 日期存在於 2004–2020 的後來對照表，也能正確判斷 `(略)` 無法
支持改前／改後全文；但若輸出只有 `official_event_identity`，它仍可能把
「承載這個日期註記的後來公文」填進去，語義上像已找到「造成該版本的
event」。歷史重建 contract 必須拆成 `owning_document_identity` 與
`marker_event_identity`：前者可 supported、後者仍 unresolved。此 pilot
5/5 都是這種情況，故 0/5 能關閉 pre/post text、direct adjacency 或 anchor
replay。Grok 的可接受角色是 hash-bound source triage，不是逐條完整性認證。
