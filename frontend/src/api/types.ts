export type Decision = "ANSWER" | "CLARIFY" | "REFUSE";
export type StockStatus = "IN_STOCK" | "OUT_OF_STOCK" | "UNKNOWN";
export type ClaimType =
  | "PRODUCT_EXISTS"
  | "PRICE"
  | "STOCK"
  | "RATING"
  | "CATEGORY"
  | "ATTRIBUTE"
  | "DESCRIPTION_QUOTE"
  | "FIELD_UNAVAILABLE"
  | "PRODUCT_SUMMARY"
  | "RESULT_COUNT"
  | "ABSENCE"
  | "POLICY_QUOTE"
  | "POLICY_NOT_FOUND";

export interface ConstraintOpIn {
  op: "SET" | "ADD" | "REMOVE" | "RESET";
  key: "category" | "price_min" | "price_max" | "stock" | "rating_min" | "attribute" | "term" | "sort" | "all";
  value?: any;
}

export interface ConstraintView {
  key: string;
  label: string;
  op_to_remove: ConstraintOpIn;
}

export interface ClientContext {
  focus_product_id?: string | null;
}

export interface Resolution {
  clarification_id: string;
  option_id: string;
}

export interface ChatOptions {
  max_results: number;
}

export interface ChatRequest {
  session_id?: string | null;
  message: string;
  client_context?: ClientContext;
  constraint_ops?: ConstraintOpIn[];
  resolution?: Resolution | null;
  options?: ChatOptions;
}

export interface CardFact {
  field: "price" | "stock";
  label: string;
  value: string | number | null;
  display: string;
  available: boolean;
  evidence_id: string;
}

export interface ProductCard {
  product_id: string;
  title: string;
  title_evidence_id: string;
  origin: "SEARCH_RESULT" | "EXISTENCE_MATCH" | "LOOKUP" | "CLARIFY_OPTION" | "RELATED";
  applied_search_id?: string | null;
  facts: CardFact[];
}

export interface Claim {
  claim_id: string;
  type: ClaimType;
  scope: "PRODUCT" | "CATALOGUE" | "GENERAL_POLICY";
  product_id?: string | null;
  value: Record<string, any>;
  sentence: string;
  evidence_ids: string[];
}

export interface ProductFieldEvidence {
  evidence_id: string;
  kind: "PRODUCT_FIELD";
  product_id: string;
  field: string;
  value: string | number | null;
  display_value?: string | null;
  available: boolean;
  excerpt?: string | null;
}

export interface PolicyEvidence {
  evidence_id: string;
  kind: "POLICY_SECTION";
  section_id: string;
  heading: string;
  excerpt: string;
}

export interface SearchEvidence {
  evidence_id: string;
  kind: "SEARCH_MANIFEST";
  search: any;
}

export type Evidence = ProductFieldEvidence | PolicyEvidence | SearchEvidence;

export interface ClarificationOption {
  option_id: string;
  kind: "PRODUCT" | "CONSTRAINT_OPS" | "POLICY_TOPIC";
  label: string;
  product_id?: string | null;
  constraint_ops?: ConstraintOpIn[];
  policy_topic?: string | null;
  evidence_ids?: string[];
}

export interface ClarificationBlock {
  clarification_id: string;
  reason: string;
  options: ClarificationOption[];
}

export interface RefusalBlock {
  reason: string;
}

export interface Suggestion {
  claim_id: string;
  label: string;
  constraint_ops: ConstraintOpIn[];
}

export interface Metrics {
  request_id: string;
  total_ms: number;
  stages: Record<string, number>;
  tokens: Record<string, any>;
  validator_trips: number;
}

export interface ChatResponse {
  session_id: string;
  turn_id: number;
  decision: Decision;
  answer_text: string;
  claims: Claim[];
  evidence: Evidence[];
  products: ProductCard[];
  clarification?: ClarificationBlock | null;
  refusal?: RefusalBlock | null;
  active_constraints: ConstraintView[];
  suggestions: Suggestion[];
  warnings: string[];
  metrics: Metrics;
}

export interface ProductDetail {
  product_id: string;
  title: string;
  description: string | null;
  category: string | null;
  price_minor: number | null;
  currency: string | null;
  price_display: string | null;
  rating: number | null;
  rating_count: number | null;
  stock_status: StockStatus;
  stock_label: string;
  stock_quantity: number | null;
  attributes: Array<{
    key_raw: string;
    key_norm: string;
    value_raw: string;
    value_norm: string;
    value_num?: number | null;
    unit?: string | null;
  }>;
  unavailable_fields: string[];
  flags: {
    suspicious_text: boolean;
  };
}

export interface PolicySectionDetail {
  section_id: string;
  heading: string;
  body: string;
  parent_id: string | null;
}

export interface ResetResponse {
  session_id: string;
  reset: boolean;
}

export interface HealthResponse {
  status: "ok";
}
