import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveSearchEvidence } from "../src/utils/searchEvidence.ts";

const manifest = {
  search_id: "s_7",
  scope: "CATALOGUE",
  intent: "SEARCH",
  query_text: "black shoes",
  lookup_ids: [],
  applied_constraints: [],
  term_requirements: ["black", "shoes"],
  term_min_coverage: 0.67,
  completeness: "INCOMPLETE",
  incomplete_reasons: ["LEXICAL_BACKEND_ERROR"],
  eligible_count: 5,
  exact_match_count: 0,
  tied_count: null,
  returned_count: 0,
  excluded_unknown_stock: 0,
  relaxations: [],
  ranking: { lexical: true, semantic: false },
  related_count: 0,
};

const evidence = {
  evidence_id: "search:s_7",
  kind: "SEARCH_MANIFEST",
  search: manifest,
};

test("search citation resolves to its matching response manifest", () => {
  assert.deepEqual(resolveSearchEvidence([evidence], "s_7"), {
    kind: "search",
    data: manifest,
  });
});

test("search citation does not borrow a different or mismatched manifest", () => {
  assert.equal(resolveSearchEvidence([evidence], "s_8").kind, "error");
  assert.equal(
    resolveSearchEvidence([{ ...evidence, evidence_id: "search:s_8" }], "s_7").kind,
    "error",
  );
});

test("duplicate matching manifests fail closed instead of choosing one", () => {
  assert.equal(resolveSearchEvidence([evidence, evidence], "s_7").kind, "error");
});
