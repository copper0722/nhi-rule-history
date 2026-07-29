# R3 reconciliation — 2.6.1 announced version and Table 1 aid

Date: 2026-07-29

## Accepted repairs

- Add `official_notice -> notice_effect -> clause_projection` rather than
  treating the notice as a single-clause document.
- Project 2.6.1 first and declare the projection partial; list 2.6.2, 2.6.3,
  and reimbursed-item changes as unresolved event scope.
- Store the amendment's new text as source-exact components. Build the displayed
  2.6.1 full text as a deterministic composite: source-exact amended components
  plus the unchanged current Table 2 replayed from the sealed predecessor.
  Preserve both provenance lanes and a composite-manifest hash.
- Separate display lifecycle from legal resolution/selectability. This release
  publishes the scheduled future version but does **not** enable an automatic
  legal selector.
- Limit the first calculator to “Table 1 LDL-C starting-treatment threshold
  check”. Use the four outcome codes required by Pro.
- Use exact NHI product codes for the 116-item Table-2 code set. Unknown code is
  insufficient information.
- Keep all user-entered values in ephemeral browser memory only.

## Frontend repair added from owner review

Tables with four or more columns cannot depend on horizontal swiping. At narrow
widths, each source row becomes a condition card. The risk group is the card
heading, starting threshold and target are paired, and the long prescription
rule is a native expandable section inside the card. The semantic reading order
and complete source text remain available without lateral scrolling.

## Release boundary

This release may:

1. display the official 2026-07-28 amendment now;
2. label it “2026-09-01 生效” everywhere it is used;
3. expose the reviewed composite 2.6.1 text with component provenance;
4. run the version-bound Table 1 threshold check after explicit user opt-in.

It may not call the result a full reimbursement decision or make the future
version the default current rule. Automatic effective-date selection remains a
separate gate after correction/withdrawal/conflict/freshness monitoring is
implemented and verified.

