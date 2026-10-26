import type { Evidence, SearchEvidence, SearchManifest } from "../api/types";

export type SearchEvidenceDrawerState =
  | { kind: "search"; data: SearchManifest }
  | { kind: "error"; message: string };

export function resolveSearchEvidence(
  evidence: Evidence[] | undefined,
  searchId: string,
): SearchEvidenceDrawerState {
  const matches = (evidence ?? []).filter(
    (item): item is SearchEvidence =>
      item.kind === "SEARCH_MANIFEST" &&
      item.evidence_id === `search:${searchId}` &&
      item.search.search_id === searchId,
  );

  if (matches.length !== 1) {
    return {
      kind: "error",
      message: "Search evidence for this citation is not available in this response.",
    };
  }

  return { kind: "search", data: matches[0].search };
}
