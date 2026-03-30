import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft, Zap, Plus, Inbox, Activity, CheckCircle2,
  XCircle, Send, ChevronDown, ChevronUp, RefreshCw,
  AlertTriangle, Package, TrendingUp, FileText,
  Loader2,
} from "lucide-react";
import {
  Button, Card, CardHeader, CardBody, Badge, Input,
  Alert, Spinner, Textarea,
} from "../components/ui";
import { NegotiationTimeline } from "../components/NegotiationTimeline";
import { HITLPanel } from "../components/HITLPanel";
import { cn, formatCurrency, calcMargin } from "../lib/utils";
import {
  getCatalog,
  createTargetedRequest,
  getRetailerSessions,
  getDirectOffersForRetailer,
  setRetailerConstraints,
  runAutoNegotiation,
  humanIntervene,
  approveDeal,
  rejectDeal,
} from "../lib/api";
import type {
  Partner, NegotiationSession, PartyLimits,
  AutoNegotiateResponse, HITLTrigger, SessionStatus,
  ProductRequest, DirectOffer,
} from "../lib/types";
import { STATUS_LABELS, ACTIVE_STATUSES, TERMINAL_STATUSES } from "../lib/types";

// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = "request" | "offers" | "active" | "completed";

interface NegotiationState {
  sessionId: string;
  session: NegotiationSession;
  autoResult?: AutoNegotiateResponse;
  hitlTrigger?: HITLTrigger;
  loading: boolean;
  expanded: boolean;
  constraintPhase: boolean;
  // constraint form state per session
  maxPrice: string;
  maxDelivery: string;
  retailPrice: string;
  targetMargin: string;
  approvalPhase: boolean;
}

// ── Status Badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: SessionStatus }) {
  const map: Record<string, string> = {
    accepted: "emerald", rejected: "red", hitl_required: "amber",
    negotiating: "sky", pending_approval: "blue",
    no_zopa: "red", failed: "red", max_rounds: "amber",
    pending_limits: "slate", paused: "slate", renegotiating: "violet",
  };
  return (
    <Badge variant={(map[status] ?? "slate") as any} dot>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

// ── Tab Button ────────────────────────────────────────────────────────────────

function TabButton({
  active, onClick, children, count,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 whitespace-nowrap transition-colors",
        active
          ? "border-[#0070d2] text-[#0070d2]"
          : "border-transparent text-slate-500 hover:text-slate-700 hover:border-gray-300"
      )}
    >
      {children}
      {count !== undefined && count > 0 && (
        <span className={cn(
          "inline-flex items-center justify-center h-5 min-w-5 rounded-full text-xs px-1.5",
          active ? "bg-[#0070d2] text-white" : "bg-gray-200 text-slate-600"
        )}>
          {count}
        </span>
      )}
    </button>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function RetailerDashboard() {
  const navigate = useNavigate();

  // Catalog
  const [suppliers, setSuppliers] = useState<Partner[]>([]);
  const [retailers, setRetailers] = useState<Partner[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);

  // Identity
  const [retailerId, setRetailerId] = useState("");

  // Tab
  const [activeTab, setActiveTab] = useState<Tab>("request");

  // Data
  const [sessions, setSessions] = useState<NegotiationSession[]>([]);
  const [offers, setOffers] = useState<DirectOffer[]>([]);
  const [dataLoading, setDataLoading] = useState(false);

  // Request creation form
  const [requestText, setRequestText] = useState("");
  const [additionalContext, setAdditionalContext] = useState("");
  const [selectedSupplierIds, setSelectedSupplierIds] = useState<string[]>([]);
  const [requestLoading, setRequestLoading] = useState(false);
  const [requestSuccess, setRequestSuccess] = useState<ProductRequest | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  // Per-session negotiation states
  const [negStates, setNegStates] = useState<Record<string, NegotiationState>>({});

  // Offer acceptance flow
  const [acceptingOffer, setAcceptingOffer] = useState<string | null>(null);
  const [offerConstraints, setOfferConstraints] = useState<Record<string, {
    maxPrice: string; maxDelivery: string; retailPrice: string; targetMargin: string;
  }>>({});

  // ── Load catalog ──────────────────────────────────────────────────────────

  useEffect(() => {
    getCatalog()
      .then((data) => {
        setSuppliers(data.suppliers);
        setRetailers(data.retailers);
        if (data.retailers[0]) setRetailerId(data.retailers[0].id);
      })
      .catch(console.error)
      .finally(() => setCatalogLoading(false));
  }, []);

  // ── Load inbox data ───────────────────────────────────────────────────────

  const loadData = useCallback(async () => {
    if (!retailerId) return;
    setDataLoading(true);
    try {
      const [sess, offs] = await Promise.allSettled([
        getRetailerSessions(retailerId),
        getDirectOffersForRetailer(retailerId),
      ]);
      if (sess.status === "fulfilled") setSessions(sess.value);
      if (offs.status === "fulfilled") setOffers(offs.value);
    } finally {
      setDataLoading(false);
    }
  }, [retailerId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ── Auto-polling: refresh data every 5 s when not busy ───────────────────

  useEffect(() => {
    const id = setInterval(() => {
      const anyNegRunning = Object.values(negStates).some((s) => s.loading);
      if (!dataLoading && !anyNegRunning) loadData();
    }, 5000);
    return () => clearInterval(id);
  }, [loadData, dataLoading, negStates]);

  // ── Derived lists ─────────────────────────────────────────────────────────

  const activeSessions = sessions.filter((s) =>
    ACTIVE_STATUSES.includes(s.status)
  );
  const completedSessions = sessions.filter((s) =>
    TERMINAL_STATUSES.includes(s.status)
  );
  const pendingOffers = offers.filter((o) => o.status === "pending");

  // ── Tab 1: Create request ─────────────────────────────────────────────────

  const handleCreateRequest = async () => {
    if (!requestText.trim() || !retailerId) return;
    setRequestLoading(true);
    setRequestError(null);
    setRequestSuccess(null);
    const retailerInfo = retailers.find((r) => r.id === retailerId);
    try {
      const req = await createTargetedRequest({
        retailer_id: retailerId,
        retailer_name: retailerInfo?.name ?? retailerId,
        raw_request: requestText.trim(),
        target_supplier_ids: selectedSupplierIds,
      });
      setRequestSuccess(req);
      setRequestText("");
      setAdditionalContext("");
      setSelectedSupplierIds([]);
    } catch (e: any) {
      setRequestError(e.message);
    } finally {
      setRequestLoading(false);
    }
  };

  const toggleSupplierSelection = (id: string) => {
    setSelectedSupplierIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  // ── Tab 2: Start negotiation from offer ───────────────────────────────────

  const handleStartNegotiationFromOffer = async (offer: DirectOffer) => {
    const oc = offerConstraints[offer.offer_id];
    if (!oc) return;

    // offer.offer_id IS the session_id created by the supplier via create-direct
    const existingSessionId = offer.offer_id;
    // Store under session_id so renderActiveSession (which looks up by session_id) finds it
    const sessionKey = existingSessionId;

    setNegStates((prev) => ({
      ...prev,
      [sessionKey]: {
        sessionId: existingSessionId,
        session: null as any,
        loading: true,
        expanded: true,
        constraintPhase: false,
        maxPrice: oc.maxPrice,
        maxDelivery: oc.maxDelivery,
        retailPrice: oc.retailPrice,
        targetMargin: oc.targetMargin,
        approvalPhase: false,
      },
    }));

    try {
      // 1. Set retailer constraints on the EXISTING session (which already has supplier_limits)
      const limits: PartyLimits = {
        max_price: parseFloat(oc.maxPrice) || undefined,
        max_delivery_days: parseInt(oc.maxDelivery) || undefined,
        retail_price: parseFloat(oc.retailPrice) || undefined,
        target_margin: parseInt(oc.targetMargin) / 100 || undefined,
      };
      const constraintResult = await setRetailerConstraints(existingSessionId, limits);

      // 2. Only auto-negotiate if ZOPA exists
      if (constraintResult.zopa_analysis?.zopa_exists) {
        const result = await runAutoNegotiation(existingSessionId, 50);

        setNegStates((prev) => ({
          ...prev,
          [sessionKey]: {
            ...prev[sessionKey],
            sessionId: existingSessionId,
            session: constraintResult.session,
            autoResult: result,
            hitlTrigger: result.hitl_triggered ? result.hitl_trigger : undefined,
            loading: false,
            approvalPhase: !result.hitl_triggered,
          },
        }));
      } else {
        // No ZOPA — show the result without attempting negotiation
        setNegStates((prev) => ({
          ...prev,
          [sessionKey]: {
            ...prev[sessionKey],
            sessionId: existingSessionId,
            session: constraintResult.session,
            loading: false,
            approvalPhase: false,
          },
        }));
      }

      // Refresh session list
      await loadData();
      setAcceptingOffer(null);
    } catch (e: any) {
      setNegStates((prev) => ({
        ...prev,
        [sessionKey]: { ...prev[sessionKey], loading: false },
      }));
    }
  };

  // ── Negotiation helpers (per active session) ──────────────────────────────

  const handleHITLAction = async (
    sessionId: string,
    action: "continue" | "abort",
    newLimits?: PartyLimits
  ) => {
    const state = Object.values(negStates).find((s) => s.sessionId === sessionId);
    if (!state) return;
    const key = Object.keys(negStates).find((k) => negStates[k].sessionId === sessionId)!;

    setNegStates((prev) => ({ ...prev, [key]: { ...prev[key], loading: true } }));
    try {
      await humanIntervene(sessionId, {
        action: action === "continue" ? "continue" : "abort",
        role: "retailer",
        new_limits: newLimits,
      });

      if (action === "abort") {
        await loadData();
        setNegStates((prev) => {
          const u = { ...prev };
          delete u[key];
          return u;
        });
        return;
      }

      const result = await runAutoNegotiation(sessionId, 50);
      setNegStates((prev) => ({
        ...prev,
        [key]: {
          ...prev[key],
          autoResult: result,
          hitlTrigger: result.hitl_triggered ? result.hitl_trigger : undefined,
          loading: false,
          approvalPhase: !result.hitl_triggered,
        },
      }));
      await loadData();
    } catch (e: any) {
      console.error("HITL action error:", e);
      // Keep session visible, just clear loading and reload from backend
      setNegStates((prev) => ({ ...prev, [key]: { ...prev[key], loading: false } }));
      await loadData();
    }
  };

  const handleApprove = async (sessionId: string) => {
    try {
      await approveDeal(sessionId, "retailer");
      await loadData();
    } catch (e: any) {
      console.error(e);
    }
  };

  const handleReject = async (sessionId: string, reason = "") => {
    try {
      await rejectDeal(sessionId, "retailer", reason);
      await loadData();
    } catch (e: any) {
      console.error(e);
    }
  };

  // ── Handle constraint-setting for pending_limits sessions (from request flow) ──

  const handleSetConstraintsForSession = async (session: NegotiationSession) => {
    const key = session.session_id;
    const ns = negStates[key];
    if (!ns) return;

    setNegStates((prev) => ({
      ...prev,
      [key]: { ...prev[key], loading: true },
    }));

    try {
      const limits: PartyLimits = {
        max_price: parseFloat(ns.maxPrice) || undefined,
        max_delivery_days: parseInt(ns.maxDelivery) || undefined,
        retail_price: parseFloat(ns.retailPrice) || undefined,
        target_margin: parseInt(ns.targetMargin) / 100 || undefined,
      };
      const constraintResult = await setRetailerConstraints(key, limits);

      if (constraintResult.zopa_analysis?.zopa_exists) {
        const result = await runAutoNegotiation(key, 50);
        setNegStates((prev) => ({
          ...prev,
          [key]: {
            ...prev[key],
            session: constraintResult.session,
            autoResult: result,
            hitlTrigger: result.hitl_triggered ? result.hitl_trigger : undefined,
            loading: false,
            constraintPhase: false,
            approvalPhase: !result.hitl_triggered,
          },
        }));
      } else {
        setNegStates((prev) => ({
          ...prev,
          [key]: {
            ...prev[key],
            session: constraintResult.session,
            loading: false,
            constraintPhase: false,
            approvalPhase: false,
          },
        }));
      }
      await loadData();
    } catch {
      setNegStates((prev) => ({
        ...prev,
        [key]: { ...prev[key], loading: false },
      }));
    }
  };

  // ── Auto-populate negStates for HITL sessions not started by this dashboard ──

  useEffect(() => {
    sessions.forEach((session) => {
      // Auto-populate constraint form state for pending_limits sessions from request flow
      if (session.status === "pending_limits" && !negStates[session.session_id]) {
        const initialPrice = session.initial_offer?.unit_price ?? 0;
        setNegStates((prev) => {
          if (prev[session.session_id]) return prev;
          return {
            ...prev,
            [session.session_id]: {
              sessionId: session.session_id,
              session,
              loading: false,
              expanded: true,
              constraintPhase: true,
              maxPrice: initialPrice ? String((initialPrice * 1.05).toFixed(2)) : "",
              maxDelivery: String((session.initial_offer?.delivery_days ?? 14) + 7),
              retailPrice: initialPrice ? String((initialPrice * 1.4).toFixed(2)) : "",
              targetMargin: "25",
              approvalPhase: false,
            },
          };
        });
      }
      if (session.status === "pending_approval" && !negStates[session.session_id]) {
        setNegStates((prev) => {
          if (prev[session.session_id]) return prev;
          return {
            ...prev,
            [session.session_id]: {
              sessionId: session.session_id,
              session,
              loading: false,
              expanded: true,
              constraintPhase: false,
              maxPrice: String(session.retailer_limits?.max_price ?? ""),
              maxDelivery: String(session.retailer_limits?.max_delivery_days ?? ""),
              retailPrice: String(session.retailer_limits?.retail_price ?? ""),
              targetMargin: String(
                Math.round((session.retailer_limits?.target_margin ?? 0.25) * 100)
              ),
              approvalPhase: true,
            },
          };
        });
      }
      if (session.status === "hitl_required" && !negStates[session.session_id]) {
        setNegStates((prev) => {
          if (prev[session.session_id]) return prev; // already populated
          const lastRound = session.rounds?.[session.rounds.length - 1] as any;
          const currentPrice =
            lastRound?.offer?.unit_price ?? lastRound?.unit_price ?? undefined;
          const supplierLastPrice = session.rounds
            ?.slice()
            .reverse()
            .find((r) => r.role === "supplier")?.offer?.unit_price;
          const retailerLastPrice = session.rounds
            ?.slice()
            .reverse()
            .find((r) => r.role === "retailer")?.offer?.unit_price;
          const priceGap =
            supplierLastPrice != null && retailerLastPrice != null
              ? Math.abs(supplierLastPrice - retailerLastPrice)
              : undefined;
          const syntheticTrigger: HITLTrigger = {
            reason: "negotiation_stalled" as any,
            severity: "warning" as any,
            message: session.status_message || "Menschlicher Eingriff erforderlich — bitte Verhandlungsstand prüfen.",
            recommended_action:
              "Prüfen Sie die aktuellen Runden und entscheiden Sie: Fortsetzen, Limits anpassen oder abbrechen.",
            current_price: currentPrice,
            supplier_last_price: supplierLastPrice,
            retailer_last_price: retailerLastPrice,
            price_gap: priceGap,
            zopa_min: session.zopa_min ?? undefined,
            zopa_max: session.zopa_max ?? undefined,
            rounds_remaining: session.max_rounds - session.current_round,
          };
          return {
            ...prev,
            [session.session_id]: {
              sessionId: session.session_id,
              session,
              hitlTrigger: syntheticTrigger,
              loading: false,
              expanded: true,
              constraintPhase: false,
              maxPrice: String(session.retailer_limits?.max_price ?? ""),
              maxDelivery: String(session.retailer_limits?.max_delivery_days ?? ""),
              retailPrice: String(session.retailer_limits?.retail_price ?? ""),
              targetMargin: String(
                Math.round((session.retailer_limits?.target_margin ?? 0.25) * 100)
              ),
              approvalPhase: false,
            },
          };
        });
      }
    });
  }, [sessions]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Inline negotiation panel for active sessions ──────────────────────────

  const renderActiveSession = (session: NegotiationSession) => {
    const ns = negStates[session.session_id];
    const expanded = ns?.expanded ?? false;
    // Prefer live autoResult rounds; fall back to rounds already stored on the session
    const displayRounds = (ns?.autoResult?.rounds ?? session.rounds ?? []).map((r: any) => ({
      round_number: r.round_number,
      role: r.role,
      offer: r.offer ?? {
        unit_price: r.unit_price,
        volume: r.volume,
        delivery_days: r.delivery_days,
        payment_terms: r.payment_terms,
      },
      is_valid: r.is_valid ?? true,
      agent_reasoning: r.reasoning_summary
        ? { reasoning_summary: r.reasoning_summary }
        : r.agent_reasoning,
    }));

    const supplierName = suppliers.find((s) => s.id === session.supplier_id)?.name ?? session.supplier_id;

    // Derive price/deal details from autoResult if available, otherwise fall back to session.rounds
    const autoLastRound = ns?.autoResult?.rounds[ns.autoResult.rounds.length - 1];
    const sessLastRound = session.rounds?.length
      ? (session.rounds[session.rounds.length - 1] as any)
      : undefined;
    // autoResult rounds have flat fields; session rounds have nested offer object
    const finalPrice =
      autoLastRound?.unit_price ??
      sessLastRound?.offer?.unit_price ??
      sessLastRound?.unit_price;
    const displayVolume =
      autoLastRound?.volume ??
      sessLastRound?.offer?.volume ??
      sessLastRound?.volume;
    const displayDelivery =
      autoLastRound?.delivery_days ??
      sessLastRound?.offer?.delivery_days ??
      sessLastRound?.delivery_days;

    return (
      <Card key={session.session_id} noPad className="overflow-hidden">
        {/* Session header */}
        <div className="px-5 py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-9 w-9 rounded-lg bg-sky-50 border border-sky-100 flex items-center justify-center shrink-0">
              <Package className="h-4 w-4 text-sky-600" />
            </div>
            <div className="min-w-0">
              <p className="font-medium text-slate-900 text-sm truncate">
                {session.product_name}
              </p>
              <p className="text-xs text-slate-500">
                {supplierName} · Runde {session.current_round}/{session.max_rounds}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <StatusBadge status={session.status} />
            {finalPrice && (
              <span className="text-sm font-semibold text-[#0070d2]">
                {formatCurrency(finalPrice)}
              </span>
            )}
            <button
              onClick={() =>
                setNegStates((prev) => ({
                  ...prev,
                  [session.session_id]: {
                    ...prev[session.session_id],
                    expanded: !expanded,
                  } as any,
                }))
              }
              className="p-1 rounded-md hover:bg-gray-100 text-slate-400 transition-colors"
            >
              {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Expanded content */}
        {expanded && (
          <div className="border-t border-gray-100 px-5 py-4 space-y-4">
            {ns?.loading && (
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin text-[#0070d2]" />
                KI-Agenten verhandeln...
              </div>
            )}

            {/* Constraint form for pending_limits sessions (from request flow) */}
            {session.status === "pending_limits" && !ns?.loading && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                  <p className="text-xs text-amber-700 font-medium">
                    Setzen Sie Ihre vertraulichen Limits — der Lieferant sieht diese nicht.
                  </p>
                </div>
                {/* Supplier's initial offer summary */}
                {session.initial_offer && (
                  <div className="grid grid-cols-4 gap-2 text-center">
                    <div className="bg-slate-50 rounded-lg p-2">
                      <p className="text-xs text-slate-500 mb-0.5">Angebotspreis</p>
                      <p className="font-bold text-slate-800 text-sm">{formatCurrency(session.initial_offer.unit_price)}</p>
                    </div>
                    <div className="bg-slate-50 rounded-lg p-2">
                      <p className="text-xs text-slate-500 mb-0.5">Menge</p>
                      <p className="font-semibold text-slate-800 text-sm">{session.initial_offer.volume.toLocaleString()}</p>
                    </div>
                    <div className="bg-slate-50 rounded-lg p-2">
                      <p className="text-xs text-slate-500 mb-0.5">Lieferzeit</p>
                      <p className="font-semibold text-slate-800 text-sm">{session.initial_offer.delivery_days}d</p>
                    </div>
                    <div className="bg-slate-50 rounded-lg p-2">
                      <p className="text-xs text-slate-500 mb-0.5">Zahlung</p>
                      <p className="font-semibold text-slate-800 text-xs">{session.initial_offer.payment_terms}</p>
                    </div>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <Input
                    label="Max. Preis (privat)"
                    type="number"
                    prefix="€"
                    step="0.50"
                    value={ns?.maxPrice ?? ""}
                    onChange={(e) =>
                      setNegStates((prev) => ({
                        ...prev,
                        [session.session_id]: { ...prev[session.session_id], maxPrice: e.target.value },
                      }))
                    }
                    hint="Ihr absolutes Maximum"
                  />
                  <Input
                    label="Max. Lieferzeit (privat)"
                    type="number"
                    suffix="Tage"
                    value={ns?.maxDelivery ?? ""}
                    onChange={(e) =>
                      setNegStates((prev) => ({
                        ...prev,
                        [session.session_id]: { ...prev[session.session_id], maxDelivery: e.target.value },
                      }))
                    }
                  />
                  <Input
                    label="Ihr Verkaufspreis"
                    type="number"
                    prefix="€"
                    step="0.50"
                    value={ns?.retailPrice ?? ""}
                    onChange={(e) =>
                      setNegStates((prev) => ({
                        ...prev,
                        [session.session_id]: { ...prev[session.session_id], retailPrice: e.target.value },
                      }))
                    }
                    hint="Ihr Endkundenpreis"
                  />
                  <Input
                    label="Ziel-Marge"
                    type="number"
                    suffix="%"
                    value={ns?.targetMargin ?? "25"}
                    onChange={(e) =>
                      setNegStates((prev) => ({
                        ...prev,
                        [session.session_id]: { ...prev[session.session_id], targetMargin: e.target.value },
                      }))
                    }
                  />
                </div>
                {ns?.maxPrice && ns?.retailPrice && (
                  <div className="flex items-center justify-between text-xs bg-gray-50 rounded-lg px-3 py-2">
                    <span className="text-slate-500">Marge bei Max-Preis:</span>
                    <span className={cn(
                      "font-semibold",
                      calcMargin(parseFloat(ns.maxPrice), parseFloat(ns.retailPrice)) >= parseInt(ns.targetMargin)
                        ? "text-emerald-600" : "text-amber-600"
                    )}>
                      {calcMargin(parseFloat(ns.maxPrice), parseFloat(ns.retailPrice)).toFixed(1)}%
                    </span>
                  </div>
                )}
                <div className="flex gap-2 pt-1">
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={!ns?.maxPrice}
                    onClick={() => handleSetConstraintsForSession(session)}
                  >
                    <Activity className="h-3.5 w-3.5" />
                    KI-Verhandlung starten
                  </Button>
                </div>
              </div>
            )}

            {/* HITL Panel */}
            {session.status === "hitl_required" && ns?.hitlTrigger && (
              <HITLPanel
                trigger={ns.hitlTrigger}
                myRole="retailer"
                currentLimits={{
                  max_price: parseFloat(ns.maxPrice) || undefined,
                  max_delivery_days: parseInt(ns.maxDelivery) || undefined,
                  retail_price: parseFloat(ns.retailPrice) || undefined,
                  target_margin: parseInt(ns.targetMargin) / 100 || undefined,
                }}
                onContinue={() => handleHITLAction(session.session_id, "continue")}
                onAdjustAndContinue={(limits) =>
                  handleHITLAction(session.session_id, "continue", limits)
                }
                onAbort={(_reason) => handleHITLAction(session.session_id, "abort")}
                loading={ns?.loading}
              />
            )}

            {/* Timeline */}
            {displayRounds.length > 0 && (
              <NegotiationTimeline
                rounds={displayRounds as any}
                myRole="retailer"
                zopaMin={ns?.autoResult?.zopa_min}
                zopaMax={ns?.autoResult?.zopa_max}
                running={ns?.loading}
              />
            )}

            {/* Approval */}
            {session.status === "pending_approval" && (
              <div className="bg-gray-50 rounded-xl border border-gray-200 p-4 space-y-3">
                <h4 className="font-medium text-slate-800 text-sm">
                  Verhandlung abgeschlossen — Ihr Urteil:
                </h4>
                <div className="grid grid-cols-3 gap-3">
                  <div className="text-center p-3 bg-white rounded-lg border border-gray-200">
                    <p className="text-xs text-slate-500 mb-1">Einigungspreis</p>
                    <p className="font-bold text-[#0070d2]">
                      {finalPrice ? formatCurrency(finalPrice) : "—"}
                    </p>
                  </div>
                  {(displayVolume || displayDelivery) && (
                    <>
                      <div className="text-center p-3 bg-white rounded-lg border border-gray-200">
                        <p className="text-xs text-slate-500 mb-1">Menge</p>
                        <p className="font-semibold text-slate-800">
                          {displayVolume?.toLocaleString() ?? "—"}
                        </p>
                      </div>
                      <div className="text-center p-3 bg-white rounded-lg border border-gray-200">
                        <p className="text-xs text-slate-500 mb-1">Lieferzeit</p>
                        <p className="font-semibold text-slate-800">
                          {displayDelivery != null ? `${displayDelivery} Tage` : "—"}
                        </p>
                      </div>
                    </>
                  )}
                </div>
                <div className="flex gap-2 pt-1">
                  <Button variant="success" onClick={() => handleApprove(session.session_id)}>
                    <CheckCircle2 className="h-4 w-4" />
                    Deal annehmen
                  </Button>
                  <Button variant="danger" onClick={() => handleReject(session.session_id)}>
                    <XCircle className="h-4 w-4" />
                    Ablehnen
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </Card>
    );
  };

  // ── Loading state ─────────────────────────────────────────────────────────

  if (catalogLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Spinner size="lg" />
          <p className="text-sm text-slate-500">Plattform wird geladen...</p>
        </div>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-20">
        <div className="max-w-6xl mx-auto px-4 h-12 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/")}
              className="text-slate-400 hover:text-slate-600 transition-colors"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="flex items-center gap-2">
              <div className="h-6 w-6 rounded-md bg-[#0070d2] flex items-center justify-center">
                <Zap className="h-3.5 w-3.5 text-white" />
              </div>
              <span className="text-sm font-semibold text-slate-800">TradeBridge AI</span>
            </div>
            <span className="text-gray-300">/</span>
            <Badge variant="sky" dot>Retailer</Badge>
          </div>
          <div className="flex items-center gap-3">
            {/* Identity selector */}
            <select
              value={retailerId}
              onChange={(e) => setRetailerId(e.target.value)}
              className="text-sm border border-gray-200 rounded-lg px-3 h-8 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-[#0070d2]"
            >
              {retailers.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
            <button
              onClick={loadData}
              disabled={dataLoading}
              className="p-1.5 rounded-md hover:bg-gray-100 text-slate-400 transition-colors"
            >
              <RefreshCw className={cn("h-4 w-4", dataLoading && "animate-spin")} />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="max-w-6xl mx-auto px-4 border-t border-gray-100">
          <div className="flex items-center overflow-x-auto">
            <TabButton active={activeTab === "request"} onClick={() => setActiveTab("request")}>
              <Plus className="h-3.5 w-3.5" />
              Neue Anfrage
            </TabButton>
            <TabButton active={activeTab === "offers"} onClick={() => setActiveTab("offers")} count={pendingOffers.length}>
              <Inbox className="h-3.5 w-3.5" />
              Eingehende Angebote
            </TabButton>
            <TabButton active={activeTab === "active"} onClick={() => setActiveTab("active")} count={activeSessions.length}>
              <Activity className="h-3.5 w-3.5" />
              Laufende Verhandlungen
            </TabButton>
            <TabButton active={activeTab === "completed"} onClick={() => setActiveTab("completed")}>
              <CheckCircle2 className="h-3.5 w-3.5" />
              Abgeschlossen
            </TabButton>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-6">

        {/* ── TAB 1: Neue Anfrage ── */}
        {activeTab === "request" && (
          <div className="max-w-2xl space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-900 mb-1">
                Anfrage erstellen
              </h2>
              <p className="text-sm text-slate-500">
                Beschreiben Sie Ihren Bedarf in eigenen Worten. 
                Die KI strukturiert Ihre Anfrage und leitet sie an passende Lieferanten weiter.
              </p>
            </div>

            {requestSuccess && (
              <Alert variant="success" title="Anfrage erfolgreich gesendet!">
                {requestSuccess.structured ? (
                  <div className="mt-1 space-y-1 text-xs">
                    {requestSuccess.structured.category && (
                      <div><span className="font-medium">Kategorie:</span> {requestSuccess.structured.category}</div>
                    )}
                    {requestSuccess.structured.product_description && (
                      <div><span className="font-medium">Produkt:</span> {requestSuccess.structured.product_description}</div>
                    )}
                    {requestSuccess.structured.estimated_volume && (
                      <div><span className="font-medium">Menge:</span> {requestSuccess.structured.estimated_volume} Einheiten</div>
                    )}
                    {requestSuccess.structured.budget_range && (
                      <div><span className="font-medium">Budget:</span> {requestSuccess.structured.budget_range}</div>
                    )}
                    {requestSuccess.structured.timeframe && (
                      <div><span className="font-medium">Zeitrahmen:</span> {requestSuccess.structured.timeframe}</div>
                    )}
                  </div>
                ) : (
                  "Ihre Anfrage wurde an die Lieferanten übermittelt."
                )}
              </Alert>
            )}

            {requestError && (
              <Alert variant="error">{requestError}</Alert>
            )}

            <Card noPad>
              <CardHeader>
                <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                  <FileText className="h-4 w-4 text-slate-400" />
                  Bedarfsbeschreibung
                </h3>
              </CardHeader>
              <CardBody className="space-y-4">
                <Textarea
                  label="Was benötigen Sie?"
                  rows={5}
                  value={requestText}
                  onChange={(e) => setRequestText(e.target.value)}
                  placeholder="Beispiel: Wir suchen 500 Stück Akku-Bohrschrauber für unser Frühjahrsortiment. Budget ca. €80-100 pro Stück, Lieferung innerhalb 14 Tagen. Wir bevorzugen Markenhersteller mit Servicegarantie."
                  hint="Die KI wandelt Ihren Text automatisch in eine strukturierte Anfrage um."
                />

                <Textarea
                  label="Zusätzliche Informationen (optional)"
                  rows={2}
                  value={additionalContext}
                  onChange={(e) => setAdditionalContext(e.target.value)}
                  placeholder="z.B. Bevorzugte Marken, Zertifikate, besondere Anforderungen..."
                />

                {/* Supplier targeting */}
                <div className="space-y-2">
                  <label className="text-xs font-semibold text-slate-600 uppercase tracking-wide block">
                    Ziel-Lieferanten (optional)
                  </label>
                  <p className="text-xs text-slate-500">
                    Leer lassen = alle Lieferanten. Auswählen = nur diese.
                  </p>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    {suppliers.map((s) => (
                      <label
                        key={s.id}
                        className={cn(
                          "flex items-center gap-2 rounded-lg border px-3 py-2 cursor-pointer transition-colors text-sm",
                          selectedSupplierIds.includes(s.id)
                            ? "border-[#0070d2] bg-blue-50 text-[#0070d2]"
                            : "border-gray-200 bg-white text-slate-600 hover:border-gray-300"
                        )}
                      >
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-[#0070d2]"
                          checked={selectedSupplierIds.includes(s.id)}
                          onChange={() => toggleSupplierSelection(s.id)}
                        />
                        <span className="text-xs font-medium truncate">{s.name}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="pt-1 flex justify-end">
                  <Button
                    variant="primary"
                    size="lg"
                    loading={requestLoading}
                    disabled={!requestText.trim() || !retailerId}
                    onClick={handleCreateRequest}
                  >
                    <Send className="h-4 w-4" />
                    Anfrage absenden
                  </Button>
                </div>
              </CardBody>
            </Card>

            {/* Tips */}
            <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-700">
              <p className="font-medium mb-2">💡 Wie es funktioniert</p>
              <ol className="space-y-1 text-xs list-decimal list-inside text-blue-600">
                <li>Sie beschreiben Ihren Bedarf in natürlicher Sprache</li>
                <li>Die KI strukturiert Ihre Anfrage (Kategorie, Menge, Budget)</li>
                <li>Lieferanten erhalten die strukturierte Anfrage in ihrem Posteingang</li>
                <li>Passende Angebote erscheinen im Tab "Eingehende Angebote"</li>
                <li>Sie setzen Ihre Limits — KI-Agenten verhandeln autonom</li>
              </ol>
            </div>
          </div>
        )}

        {/* ── TAB 2: Eingehende Angebote ── */}
        {activeTab === "offers" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 mb-1">
                  Eingehende Angebote
                </h2>
                <p className="text-sm text-slate-500">
                  Angebote von Lieferanten — setzen Sie Ihre Limits und starten Sie die KI-Verhandlung.
                </p>
              </div>
              <Badge variant="blue">{pendingOffers.length} ausstehend</Badge>
            </div>

            {pendingOffers.length === 0 && (
              <Card className="text-center py-12">
                <Inbox className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-slate-500 font-medium">Keine ausstehenden Angebote</p>
                <p className="text-xs text-slate-400 mt-1">
                  Neue Angebote von Lieferanten erscheinen hier
                </p>
              </Card>
            )}

            <div className="space-y-3">
              {pendingOffers.map((offer) => {
                const supplier = suppliers.find((s) => s.id === offer.supplier_id);
                const isAccepting = acceptingOffer === offer.offer_id;
                const oc = offerConstraints[offer.offer_id] ?? {
                  maxPrice: String((offer.unit_price * 1.05).toFixed(2)),
                  maxDelivery: String(offer.delivery_days + 7),
                  retailPrice: String((offer.unit_price * 1.4).toFixed(2)),
                  targetMargin: "25",
                };

                return (
                  <Card key={offer.offer_id} noPad>
                    <div className="px-5 py-4">
                      {/* Offer header */}
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <div className="flex items-center gap-3">
              <div className="h-9 w-9 rounded-lg bg-violet-50 border border-violet-100 flex items-center justify-center shrink-0">
                <Package className="h-4 w-4 text-violet-600" />
                          </div>
                          <div>
                            <p className="font-medium text-slate-900 text-sm">{offer.product_name}</p>
                            <p className="text-xs text-slate-500">{supplier?.name ?? offer.supplier_id}</p>
                          </div>
                        </div>
                        <Badge variant="amber" dot>Neu</Badge>
                      </div>

                      {/* Offer details */}
                      <div className="grid grid-cols-4 gap-3 mb-3">
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Preis/Stück</p>
                          <p className="font-bold text-slate-900">{formatCurrency(offer.unit_price)}</p>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Menge</p>
                          <p className="font-semibold text-slate-800">{offer.volume.toLocaleString()}</p>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Lieferzeit</p>
                          <p className="font-semibold text-slate-800">{offer.delivery_days}d</p>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Zahlung</p>
                          <p className="font-semibold text-slate-800 text-xs">{offer.payment_terms}</p>
                        </div>
                      </div>

                      {/* Supplier notes (if any) */}
                      {offer.notes && (
                        <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-3 text-xs text-amber-800">
                          <span className="font-medium">Notiz des Lieferanten: </span>
                          {offer.notes}
                        </div>
                      )}

                      {/* Constraint form (expanding) */}
                      {!isAccepting && (
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => {
                            setAcceptingOffer(offer.offer_id);
                            setOfferConstraints((prev) => ({
                              ...prev,
                              [offer.offer_id]: oc,
                            }));
                          }}
                        >
                          Verhandlung starten
                        </Button>
                      )}

                      {isAccepting && (
                        <div className="border-t border-gray-100 mt-3 pt-3 space-y-3">
                          <div className="flex items-center gap-2 mb-2">
                            <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                            <p className="text-xs text-amber-700 font-medium">
                              Ihre Limits sind vertraulich — der Lieferant sieht diese nicht.
                            </p>
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <Input
                              label="Max. Preis (privat)"
                              type="number"
                              prefix="€"
                              step="0.50"
                              value={oc.maxPrice}
                              onChange={(e) =>
                                setOfferConstraints((prev) => ({
                                  ...prev,
                                  [offer.offer_id]: { ...oc, maxPrice: e.target.value },
                                }))
                              }
                              hint="Ihr absolutes Maximum"
                            />
                            <Input
                              label="Max. Lieferzeit (privat)"
                              type="number"
                              suffix="Tage"
                              value={oc.maxDelivery}
                              onChange={(e) =>
                                setOfferConstraints((prev) => ({
                                  ...prev,
                                  [offer.offer_id]: { ...oc, maxDelivery: e.target.value },
                                }))
                              }
                            />
                            <Input
                              label="Ihr Verkaufspreis"
                              type="number"
                              prefix="€"
                              step="0.50"
                              value={oc.retailPrice}
                              onChange={(e) =>
                                setOfferConstraints((prev) => ({
                                  ...prev,
                                  [offer.offer_id]: { ...oc, retailPrice: e.target.value },
                                }))
                              }
                              hint="Ihr Endkundenpreis"
                            />
                            <Input
                              label="Ziel-Marge"
                              type="number"
                              suffix="%"
                              value={oc.targetMargin}
                              onChange={(e) =>
                                setOfferConstraints((prev) => ({
                                  ...prev,
                                  [offer.offer_id]: { ...oc, targetMargin: e.target.value },
                                }))
                              }
                            />
                          </div>
                          {oc.maxPrice && oc.retailPrice && (
                            <div className="flex items-center justify-between text-xs bg-gray-50 rounded-lg px-3 py-2">
                              <span className="text-slate-500">Marge bei Max-Preis:</span>
                              <span className={cn(
                                "font-semibold",
                                calcMargin(parseFloat(oc.maxPrice), parseFloat(oc.retailPrice)) >= parseInt(oc.targetMargin)
                                  ? "text-emerald-600" : "text-amber-600"
                              )}>
                                {calcMargin(parseFloat(oc.maxPrice), parseFloat(oc.retailPrice)).toFixed(1)}%
                              </span>
                            </div>
                          )}
                          <div className="flex gap-2 pt-1">
                            <Button
                              variant="primary"
                              size="sm"
                              onClick={() => handleStartNegotiationFromOffer(offer)}
                            >
                              <Activity className="h-3.5 w-3.5" />
                              KI-Verhandlung starten
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setAcceptingOffer(null)}
                            >
                              Abbrechen
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Inline negotiation result */}
                    {negStates[offer.offer_id] && (() => {
                      const ns = negStates[offer.offer_id];
                      const displayRounds = (ns.autoResult?.rounds ?? []).map((r) => ({
                        round_number: r.round_number,
                        role: r.role,
                        offer: { unit_price: r.unit_price, volume: r.volume, delivery_days: r.delivery_days, payment_terms: r.payment_terms },
                        is_valid: r.is_valid,
                        agent_reasoning: r.reasoning_summary ? { reasoning_summary: r.reasoning_summary } : undefined,
                      }));

                      return (
                        <div className="border-t border-gray-100 px-5 py-4 bg-gray-50 space-y-3">
                          {ns.loading ? (
                            <div className="flex items-center gap-2 text-sm text-slate-500">
                              <Loader2 className="h-4 w-4 animate-spin text-[#0070d2]" />
                              KI-Agenten verhandeln...
                            </div>
                          ) : (
                            <>
                              {displayRounds.length > 0 && (
                                <NegotiationTimeline
                                  rounds={displayRounds as any}
                                  myRole="retailer"
                                  zopaMin={ns.autoResult?.zopa_min}
                                  zopaMax={ns.autoResult?.zopa_max}
                                  running={false}
                                />
                              )}
                              {ns.approvalPhase && ns.autoResult && (
                                <div className="flex gap-2">
                                  <Button variant="success" size="sm" onClick={() => ns.sessionId && handleApprove(ns.sessionId)}>
                                    <CheckCircle2 className="h-3.5 w-3.5" /> Deal annehmen
                                  </Button>
                                  <Button variant="danger" size="sm" onClick={() => ns.sessionId && handleReject(ns.sessionId)}>
                                    <XCircle className="h-3.5 w-3.5" /> Ablehnen
                                  </Button>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      );
                    })()}
                  </Card>
                );
              })}
            </div>
          </div>
        )}

        {/* ── TAB 3: Laufende Verhandlungen ── */}
        {activeTab === "active" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 mb-1">
                  Laufende Verhandlungen
                </h2>
                <p className="text-sm text-slate-500">
                  Aktive Verhandlungssessions — expandieren für Details und Eingriffsmöglichkeiten.
                </p>
              </div>
              <Badge variant="sky">{activeSessions.length} aktiv</Badge>
            </div>

            {activeSessions.length === 0 && (
              <Card className="text-center py-12">
                <Activity className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-slate-500 font-medium">Keine laufenden Verhandlungen</p>
                <p className="text-xs text-slate-400 mt-1">
                  Starten Sie eine Verhandlung über "Eingehende Angebote"
                </p>
              </Card>
            )}

            <div className="space-y-3">
              {activeSessions.map((session) => renderActiveSession(session))}
            </div>
          </div>
        )}

        {/* ── TAB 4: Abgeschlossene Deals ── */}
        {activeTab === "completed" && (
          <div className="space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-900 mb-1">
                Abgeschlossene Deals
              </h2>
              <p className="text-sm text-slate-500">
                Alle abgeschlossenen, abgelehnten oder beendeten Verhandlungen.
              </p>
            </div>

            {completedSessions.length === 0 && (
              <Card className="text-center py-12">
                <TrendingUp className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-slate-500 font-medium">Noch keine abgeschlossenen Deals</p>
                <p className="text-xs text-slate-400 mt-1">
                  Abgeschlossene Verhandlungen erscheinen hier
                </p>
              </Card>
            )}

            <div className="space-y-2">
              {completedSessions.map((session) => {
                const supplier = suppliers.find((s) => s.id === session.supplier_id);
                const lastRound = session.rounds?.[session.rounds.length - 1];
                const isAccepted = session.status === "accepted";

                return (
                  <Card key={session.session_id} noPad>
                    <div className="px-5 py-4 flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className={cn(
                          "h-9 w-9 rounded-full flex items-center justify-center shrink-0",
                          isAccepted ? "bg-emerald-50 border border-emerald-100" : "bg-red-50 border border-red-100"
                        )}>
                          {isAccepted
                            ? <CheckCircle2 className="h-4.5 w-4.5 text-emerald-600" />
                            : <XCircle className="h-4.5 w-4.5 text-red-500" />}
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium text-slate-900 text-sm truncate">
                            {session.product_name}
                          </p>
                          <p className="text-xs text-slate-500">
                            {supplier?.name ?? session.supplier_id} · {new Date(session.updated_at).toLocaleDateString("de-DE")}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {lastRound && isAccepted && (
                          <div className="text-right">
                            <p className="text-xs text-slate-500">Endpreis</p>
                            <p className="font-semibold text-slate-900">
                              {formatCurrency(lastRound.offer.unit_price)}
                            </p>
                          </div>
                        )}
                        <StatusBadge status={session.status} />
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}