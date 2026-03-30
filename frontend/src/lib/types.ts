// ─────────────────────────────────────────────────────────────────────────────
// Core domain types — mirrors the Python Pydantic models exactly
// ─────────────────────────────────────────────────────────────────────────────

export type AgentRole = "supplier" | "retailer";

export type SessionStatus =
  | "pending_limits"
  | "offer_sent"
  | "constraints_set"
  | "no_zopa"
  | "negotiating"
  | "renegotiating"
  | "hitl_required"
  | "paused"
  | "pending_approval"
  | "accepted"
  | "rejected"
  | "failed"
  | "max_rounds";

export type HITLTriggerReason =
  | "zopa_breach"
  | "large_price_jump"
  | "max_rounds_approaching"
  | "negotiation_stalled";

export type HITLSeverity = "info" | "warning" | "critical";

export interface NegotiationOffer {
  unit_price: number;
  volume: number;
  delivery_days: number;
  payment_terms: string;
  discount_percentage?: number;
  notes?: string;
}

export interface PartyLimits {
  // Supplier fields
  min_price?: number;
  min_volume?: number;
  max_volume?: number;
  acceptable_payment_terms?: string[];
  // Retailer fields
  max_price?: number;
  max_delivery_days?: number;
  target_margin?: number;
  retail_price?: number;
}

export interface AgentReasoning {
  reasoning_summary?: string;
  strategy?: string;
  concession_amount?: number;
  concession_pct?: number;
  steps?: string[];
  thought?: string;
  action?: string;
  observation?: string;
  final_answer?: string;
}

export interface NegotiationRound {
  round_number: number;
  role: AgentRole;
  offer: NegotiationOffer;
  is_valid: boolean;
  validation_message?: string;
  agent_reasoning?: AgentReasoning;
  timestamp?: string;
}

export interface HITLTrigger {
  reason: string;
  severity: "info" | "warning" | "critical";
  message: string;
  recommended_action: string;
  /** @deprecated prefer supplier_last_price / retailer_last_price */
  current_price?: number;
  supplier_last_price?: number;
  retailer_last_price?: number;
  price_gap?: number;
  zopa_min?: number;
  zopa_max?: number;
  rounds_remaining?: number;
}

export interface NegotiationSession {
  session_id: string;
  product_name: string;
  supplier_id: string;
  retailer_id: string;
  initiator: AgentRole;
  status: SessionStatus;
  status_message?: string;
  initial_offer: NegotiationOffer;
  supplier_limits?: PartyLimits;
  retailer_limits?: PartyLimits;
  zopa_min?: number;
  zopa_max?: number;
  zopa_exists?: boolean;
  current_round: number;
  max_rounds: number;
  rounds: NegotiationRound[];
  supplier_approved: boolean;
  retailer_approved: boolean;
  created_at: string;
  updated_at: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// API response types
// ─────────────────────────────────────────────────────────────────────────────

export interface Partner {
  id: string;
  name: string;
  type: "supplier" | "retailer";
  country?: string;
  city?: string;
  industry?: string;
  contact_email?: string;
  phone?: string;
  website?: string;
  description?: string;
}

export interface Product {
  id: string;
  name: string;
  category?: string;
  unit?: string;
  description?: string;
  base_price?: number;
  min_price?: number;
  supplier_id?: string;
  supplier_ids?: string[];
  min_order_quantity?: number;
  max_monthly_capacity?: number;
  lead_time_days?: number;
  default_payment_terms?: string;
  specifications?: Record<string, string | number>;
}

export interface CatalogEntry {
  product: Product;
  suppliers: Partner[];
}

export interface ZOPAAnalysis {
  zopa_exists: boolean;
  zopa_min?: number;
  zopa_max?: number;
  gap_amount?: number;
  recommendation: string;
  supplier_suggestion?: string;
  retailer_suggestion?: string;
  alternative_approaches?: string[];
}

export interface AutoNegotiateResponse {
  session_id: string;
  status: SessionStatus;
  message: string;
  rounds_completed: number;
  total_rounds: number;
  hitl_triggered: boolean;
  hitl_trigger?: HITLTrigger;
  rounds: Array<{
    round_number: number;
    role: AgentRole;
    unit_price: number;
    volume: number;
    delivery_days: number;
    payment_terms: string;
    is_valid: boolean;
    reasoning_summary?: string;
  }>;
  zopa_min?: number;
  zopa_max?: number;
}

export interface InterventionBody {
  action: "continue" | "adjust_constraints" | "pause" | "abort";
  role?: AgentRole;
  new_limits?: Partial<PartyLimits>;
  notes?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// NEW: Request / Offer flow types (Szenario 1 + 2)
// ─────────────────────────────────────────────────────────────────────────────

export type RequestStatus =
  | "pending"
  | "pending_supplier"
  | "matched"
  | "products_matched"
  | "offer_created"
  | "negotiating"
  | "completed"
  | "cancelled";

export interface StructuredRequest {
  category?: string;
  product_description?: string;
  estimated_volume?: number;
  timeframe?: string;
  budget_range?: string;
  quality_tier?: string;
  preferred_payment_terms?: string;
  special_requirements?: string;
  market_context?: string;
}

export interface ProductRequest {
  request_id: string;
  retailer_id: string;
  retailer_name?: string;
  raw_text: string;
  structured?: StructuredRequest;
  status: RequestStatus;
  matched_products?: ProductMatch[];
  created_at: string;
  target_supplier_ids?: string[];
  additional_context?: string;
}

export interface ProductMatch {
  product_id: string;
  product_name: string;
  supplier_id: string;
  match_score: number;
  match_reason: string;
  base_price: number;
}

export type DirectOfferStatus = "pending" | "accepted" | "rejected" | "negotiating" | "completed";

export interface DirectOffer {
  offer_id: string;
  supplier_id: string;
  retailer_id: string;
  product_id: string;
  product_name: string;
  unit_price: number;
  volume: number;
  delivery_days: number;
  payment_terms: string;
  notes?: string;
  status: DirectOfferStatus;
  created_at: string;
  request_id?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// UI state types
// ─────────────────────────────────────────────────────────────────────────────

export interface NegotiationFlowState {
  step: "setup" | "constraints" | "negotiating" | "hitl" | "approval" | "done";
  session?: NegotiationSession;
  autoResult?: AutoNegotiateResponse;
  hitlTrigger?: HITLTrigger;
  loading: boolean;
  error?: string;
}

export const STATUS_LABELS: Record<SessionStatus, string> = {
  pending_limits: "Warte auf Retailer",
  offer_sent: "Angebot gesendet",
  constraints_set: "Limits gesetzt",
  no_zopa: "Keine Einigung möglich",
  negotiating: "In Verhandlung",
  renegotiating: "Nachverhandlung",
  hitl_required: "Eingriff erforderlich",
  paused: "Pausiert",
  pending_approval: "Wartet auf Freigabe",
  accepted: "Deal abgeschlossen",
  rejected: "Abgelehnt",
  failed: "Fehlgeschlagen",
  max_rounds: "Max. Runden erreicht",
};

export const TERMINAL_STATUSES: SessionStatus[] = [
  "accepted",
  "rejected",
  "failed",
  "max_rounds",
];

export const ACTIVE_STATUSES: SessionStatus[] = [
  "offer_sent",
  "constraints_set",
  "negotiating",
  "renegotiating",
  "hitl_required",
  "pending_approval",
  "pending_limits",
  "paused",
];
