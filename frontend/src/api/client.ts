import type {
  ChatRequest,
  ChatResponse,
  HealthResponse,
  PolicySectionDetail,
  ProductDetail,
  ResetResponse,
} from "./types";

const API_BASE = "";

export class ApiClientError extends Error {
  code: string;
  requestId?: string;
  details?: any[];

  constructor(code: string, message: string, requestId?: string, details?: any[]) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errCode = "HTTP_ERROR";
    let errMsg = `Request failed with status ${res.status}`;
    let reqId = res.headers.get("X-Request-Id") || undefined;
    let details = undefined;

    try {
      const data = await res.json();
      if (data && data.error) {
        errCode = data.error.code || errCode;
        errMsg = data.error.message || errMsg;
        reqId = data.error.request_id || reqId;
        details = data.error.details;
      }
    } catch {
      // ignore json parse error
    }

    throw new ApiClientError(errCode, errMsg, reqId, details);
  }

  return (await res.json()) as T;
}

export async function postChat(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(req),
  });
  return handleResponse<ChatResponse>(res);
}

export async function resetSession(sessionId: string): Promise<ResetResponse> {
  const res = await fetch(`${API_BASE}/api/session/reset`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return handleResponse<ResetResponse>(res);
}

export async function getProduct(productId: string): Promise<ProductDetail> {
  const res = await fetch(`${API_BASE}/api/products/${encodeURIComponent(productId)}`);
  return handleResponse<ProductDetail>(res);
}

export async function getPolicySection(sectionId: string): Promise<PolicySectionDetail> {
  const res = await fetch(`${API_BASE}/api/policy/sections/${encodeURIComponent(sectionId)}`);
  return handleResponse<PolicySectionDetail>(res);
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/api/health`);
  return handleResponse<HealthResponse>(res);
}
