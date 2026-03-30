// ─────────────────────────────────────────────────────────────────────────────
// API client — all calls go through /api (proxied to :8002 by Vite)
// ─────────────────────────────────────────────────────────────────────────────

import type {
  Partner,
  Product,
  NegotiationSession,
  PartyLimits,
  NegotiationOffer,
  AutoNegotiateResponse,
  ZOPAAnalysis,
  InterventionBody,
  ProductRequest,
  ProductMatch,
  DirectOffer,
} from "./types";

const BASE = "/api";

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err.detail;
    let message: string;
    if (Array.isArray(detail)) {
      message = detail
        .map((e: { loc?: string[]; msg?: string }) =>
          `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? "invalid"}`
        )
        .join("; ");
    } else {
      message = String(detail ?? res.statusText);
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

// ── Partners ──────────────────────────────────────────────────────────────────

export const getSuppliers = (): Promise<Partner[]> =>
  request("GET", "/partners/suppliers");

export const getRetailers = (): Promise<Partner[]> =>
  request("GET", "/partners/retailers");

export const getSupplier = (id: string): Promise<Partner> =>
  request("GET", `/partners/suppliers/${id}`);

export const getRetailer = (id: string): Promise<Partner> =>
  request("GET", `/partners/retailers/${id}`);

// ── Catalog ───────────────────────────────────────────────────────────────────

export const getCatalog = async (): Promise<{
  products: Product[];
  suppliers: Partner[];
  retailers: Partner[];
}> => {
  // Backend returns raw JSON with different field names than our frontend types.
  // We normalize here so the rest of the app can rely on Product.id, Product.base_price, etc.
  const raw = await request<{
    products: Array<Record<string, unknown>>;
    suppliers: Array<Record<string, unknown>>;
    retailers: Array<Record<string, unknown>>;
  }>("GET", "/catalog");

  const products: Product[] = (raw.products ?? []).map((p) => ({
    id: (p.product_id ?? p.id ?? "") as string,
    name: (p.name ?? p.product_id ?? "") as string,
    category: p.category as string | undefined,
    unit: p.unit as string | undefined,
    description: p.description as string | undefined,
    base_price: (p.base_price_eur ?? p.base_price) as number | undefined,
    min_price: (p.min_price_eur ?? p.min_price) as number | undefined,
    supplier_id: (p.supplier_id) as string | undefined,
    supplier_ids: p.supplier_id ? [(p.supplier_id as string)] : (p.supplier_ids as string[] | undefined),
    min_order_quantity: p.min_order_quantity as number | undefined,
    max_monthly_capacity: p.max_monthly_capacity as number | undefined,
    lead_time_days: p.lead_time_days as number | undefined,
    default_payment_terms: p.default_payment_terms as string | undefined,
    specifications: p.specifications as Record<string, string | number> | undefined,
  }));

  const suppliers: Partner[] = (raw.suppliers ?? []).map((s) => ({
    id: (s.supplier_id ?? s.id ?? "") as string,
    name: (s.name ?? s.supplier_id ?? "") as string,
    type: "supplier" as const,
    country: s.country as string | undefined,
    city: s.city as string | undefined,
    industry: s.industry as string | undefined,
    contact_email: s.contact_email as string | undefined,
    phone: s.phone as string | undefined,
    website: s.website as string | undefined,
    description: s.description as string | undefined,
  }));

  const retailers: Partner[] = (raw.retailers ?? []).map((r) => ({
    id: (r.retailer_id ?? r.id ?? "") as string,
    name: (r.name ?? r.retailer_id ?? "") as string,
    type: "retailer" as const,
    country: r.country as string | undefined,
    city: r.city as string | undefined,
    industry: r.industry as string | undefined,
    contact_email: r.contact_email as string | undefined,
    phone: r.phone as string | undefined,
    website: r.website as string | undefined,
    description: r.description as string | undefined,
  }));

  return { products, suppliers, retailers };
};

// ── Negotiation sessions ──────────────────────────────────────────────────────

export interface InitiateRequest {
  initiator: "supplier" | "retailer";
  product_id: string;
  product_name: string;
  supplier_id: string;
  retailer_id: string;
  initial_offer: NegotiationOffer;
}

export const initiateNegotiation = (
  body: InitiateRequest
): Promise<NegotiationSession> =>
  request("POST", "/negotiations/initiate", body);

export const getSession = (id: string): Promise<NegotiationSession> =>
  request("GET", `/negotiations/${id}`);

export const setSupplierConstraints = (
  sessionId: string,
  limits: PartyLimits
): Promise<NegotiationSession> =>
  request("POST", `/negotiations/${sessionId}/set-supplier-constraints`, limits);

export const setRetailerConstraints = (
  sessionId: string,
  limits: PartyLimits
): Promise<{ session: NegotiationSession; zopa_analysis: ZOPAAnalysis }> =>
  request("POST", `/negotiations/${sessionId}/set-retailer-constraints`, limits);

/** Primary negotiation call — auto-runs to completion or HITL pause */
export const runAutoNegotiation = (
  sessionId: string,
  maxRounds = 10
): Promise<AutoNegotiateResponse> =>
  request("POST", `/negotiations/${sessionId}/negotiate-auto?max_rounds=${maxRounds}`);

export const humanIntervene = (
  sessionId: string,
  body: InterventionBody
): Promise<{
  session_id: string;
  status: string;
  message: string;
  zopa_exists: boolean;
  zopa_min?: number;
  zopa_max?: number;
}> => request("POST", `/negotiations/${sessionId}/intervene`, body);

export const approveDeal = (
  sessionId: string,
  role: "supplier" | "retailer"
): Promise<NegotiationSession> =>
  request("POST", `/negotiations/${sessionId}/approve`, { role });

export const rejectDeal = (
  sessionId: string,
  role: "supplier" | "retailer",
  reason = ""
): Promise<NegotiationSession> =>
  request("POST", `/negotiations/${sessionId}/reject`, { role, reason });

// ── Session filtering ─────────────────────────────────────────────────────────

export const getSupplierSessions = (
  supplierId: string
): Promise<NegotiationSession[]> =>
  request<{ sessions: NegotiationSession[] }>("GET", `/sessions/supplier/${supplierId}`)
    .then((r) => r.sessions ?? []);

export const getRetailerSessions = (
  retailerId: string
): Promise<NegotiationSession[]> =>
  request<{ sessions: NegotiationSession[] }>("GET", `/sessions/retailer/${retailerId}`)
    .then((r) => r.sessions ?? []);

// ── Retailer Requests (Szenario 2) ────────────────────────────────────────────

export interface CreateRequestBody {
  retailer_id: string;
  retailer_name: string;
  raw_request: string;
}

export const createRequest = (
  body: CreateRequestBody
): Promise<ProductRequest> =>
  request("POST", "/requests/create", body);

export const createTargetedRequest = async (body: {
  retailer_id: string;
  retailer_name: string;
  raw_request: string;
  target_supplier_ids: string[];
}): Promise<ProductRequest> => {
  const r = await request<{
    request_id: string;
    status: string;
    structured_data?: {
      category?: string;
      product_description?: string;
      volume?: number;
      timeframe?: string;
      budget_range?: string;
      quality_tier?: string;
      preferred_payment_terms?: string;
      special_requirements?: string;
      market_context?: string;
    };
  }>("POST", "/requests/create-targeted", body);
  const rawStatus = r.status ?? "pending";
  return {
    request_id: r.request_id,
    retailer_id: body.retailer_id,
    retailer_name: body.retailer_name,
    raw_text: body.raw_request,
    status: (rawStatus === "pending_supplier" ? "pending" : rawStatus === "products_matched" ? "matched" : rawStatus) as ProductRequest["status"],
    structured: {
      category: r.structured_data?.category,
      product_description: r.structured_data?.product_description,
      estimated_volume: r.structured_data?.volume,
      timeframe: r.structured_data?.timeframe,
      budget_range: r.structured_data?.budget_range,
      quality_tier: r.structured_data?.quality_tier,
      preferred_payment_terms: r.structured_data?.preferred_payment_terms,
      special_requirements: r.structured_data?.special_requirements,
      market_context: r.structured_data?.market_context,
    },
    created_at: new Date().toISOString(),
  };
};

export const listRequests = (): Promise<ProductRequest[]> =>
  request("GET", "/requests");

export const listRetailerRequests = async (
  retailerId: string
): Promise<ProductRequest[]> => {
  try {
    return await request<ProductRequest[]>("GET", `/requests?retailer_id=${retailerId}`);
  } catch {
    return request<ProductRequest[]>("GET", "/requests");
  }
};

// ── Supplier: Match products to request ──────────────────────────────────────

export const matchProducts = (
  requestId: string,
  supplierId: string
): Promise<{ matches: ProductMatch[]; request: ProductRequest }> =>
  request("POST", `/requests/${requestId}/match-products?supplier_id=${encodeURIComponent(supplierId)}`);

export const getRequestsForSupplier = async (
  supplierId: string
): Promise<ProductRequest[]> => {
  const raw = await request<{ requests?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>>(
    "GET",
    `/requests/for-supplier/${supplierId}`
  );
  const list: Array<Record<string, unknown>> = Array.isArray(raw)
    ? raw
    : ((raw as { requests?: Array<Record<string, unknown>> }).requests ?? []);
  return list.map((r) => {
    // Normalize backend statuses to frontend RequestStatus values
    const rawStatus = (r.status ?? "pending") as string;
    const normalizedStatus: ProductRequest["status"] =
      rawStatus === "pending_supplier" ? "pending" :
      rawStatus === "products_matched" ? "matched" :
      rawStatus as ProductRequest["status"];
    return {
      request_id: (r.request_id ?? r.id ?? "") as string,
      retailer_id: (r.retailer_id ?? "") as string,
      retailer_name: r.retailer_name as string | undefined,
      raw_text: (r.raw_request ?? r.raw_text ?? "") as string,
      status: normalizedStatus,
      structured: {
        category: r.product_category as string | undefined,
        product_description: r.product_description as string | undefined,
        estimated_volume: r.estimated_volume as number | undefined,
        timeframe: r.timeframe as string | undefined,
        budget_range: r.budget_range as string | undefined,
        quality_tier: r.quality_tier as string | undefined,
        preferred_payment_terms: r.preferred_payment_terms as string | undefined,
        special_requirements: r.special_requirements as string | undefined,
        market_context: r.market_context as string | undefined,
      },
      target_supplier_ids: r.target_supplier_ids as string[] | undefined,
      created_at: (r.created_at as string | undefined) ?? new Date().toISOString(),
    };
  });
};

// ── Supplier: Create offer from request ──────────────────────────────────────

export interface CreateOfferFromRequestBody {
  supplier_id: string;
  product_id: string;
  unit_price: number;
  volume: number;
  delivery_days: number;
  payment_terms: string;
  notes?: string;
}

export interface CreateOfferFromRequestResult {
  session_id: string;
  request_id: string;
  offer: import("./types").NegotiationOffer;
  status: string;
}

export const createOfferFromRequest = (
  requestId: string,
  body: CreateOfferFromRequestBody
): Promise<CreateOfferFromRequestResult> =>
  request("POST", `/requests/${requestId}/create-offer`, body);

// ── Supplier: Direct offer (Szenario 1) ──────────────────────────────────────

export interface CreateDirectOfferBody {
  supplier_id: string;
  supplier_name: string;
  retailer_id: string;
  retailer_name: string;
  product_id: string;
  offer_details: {
    unit_price: number;
    volume: number;
    delivery_days: number;
    payment_terms: string;
    notes?: string;
  };
  supplier_constraints: {
    min_price?: number;
    min_volume?: number;
    max_volume?: number;
    payment_terms?: string[];
  };
}

export const createDirectOffer = (
  body: CreateDirectOfferBody
): Promise<{ session_id: string; status: string; message: string; offer: NegotiationOffer }> =>
  request("POST", "/offers/create-direct", body);

export const getDirectOffersForRetailer = async (
  retailerId: string
): Promise<DirectOffer[]> => {
  try {
    const r = await request<{ offers?: DirectOffer[] } | DirectOffer[]>(
      "GET",
      `/offers/for-retailer/${retailerId}`
    );
    if (Array.isArray(r)) return r;
    return (r as { offers?: DirectOffer[] }).offers ?? [];
  } catch {
    return [];
  }
};
