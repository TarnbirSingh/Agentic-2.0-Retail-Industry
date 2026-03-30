import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft, Zap, Plus, Inbox, Activity, CheckCircle2,
  XCircle, ChevronDown, ChevronUp, RefreshCw,
  AlertTriangle, Package, TrendingUp,
  Loader2, Send,
} from "lucide-react";
import {
  Button, Card, CardHeader, CardBody, Badge, Input, Select,
  Alert, Spinner, Textarea,
} from "../components/ui";
import { NegotiationTimeline } from "../components/NegotiationTimeline";
import { cn, formatCurrency } from "../lib/utils";
import {
  getCatalog,
  createDirectOffer,
  getRequestsForSupplier,
  matchProducts,
  createOfferFromRequest,
  getSupplierSessions,
  setSupplierConstraints,
  approveDeal,
  rejectDeal,
} from "../lib/api";
import type {
  Product, Partner, NegotiationSession, PartyLimits,
  AutoNegotiateResponse, HITLTrigger, SessionStatus,
  ProductRequest, ProductMatch,
} from "../lib/types";
import { STATUS_LABELS, ACTIVE_STATUSES, TERMINAL_STATUSES } from "../lib/types";

// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = "offer" | "requests" | "active" | "completed";

interface RequestState {
  loading: boolean;
  matches?: ProductMatch[];
  showOfferForm: boolean;
  selectedProductId: string;
  offerPrice: string;
  offerVolume: string;
  offerDelivery: string;
  offerPayment: string;
  offerNotes: string;
  minPrice: string;
  minVolume: string;
  maxVolume: string;
  submitted: boolean;
  error?: string;
}

interface NegotiationState {
  sessionId: string;
  session: NegotiationSession | null;
  autoResult?: AutoNegotiateResponse;
  hitlTrigger?: HITLTrigger;
  loading: boolean;
  expanded: boolean;
  minPrice: string;
  approvalPhase: boolean;
}

// ── Status Badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: SessionStatus }) {
  const map: Record<string, string> = {
    accepted: "emerald", rejected: "red", hitl_required: "amber",
    negotiating: "violet", pending_approval: "blue",
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
          ? "border-violet-600 text-violet-600"
          : "border-transparent text-slate-500 hover:text-slate-700 hover:border-gray-300"
      )}
    >
      {children}
      {count !== undefined && count > 0 && (
        <span className={cn(
          "inline-flex items-center justify-center h-5 min-w-5 rounded-full text-xs px-1.5",
          active ? "bg-violet-600 text-white" : "bg-gray-200 text-slate-600"
        )}>
          {count}
        </span>
      )}
    </button>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function SupplierDashboard() {
  const navigate = useNavigate();

  // Catalog
  const [suppliers, setSuppliers] = useState<Partner[]>([]);
  const [retailers, setRetailers] = useState<Partner[]>([]);
  const [allProducts, setAllProducts] = useState<Product[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);

  // Identity
  const [supplierId, setSupplierId] = useState("");

  // Tab
  const [activeTab, setActiveTab] = useState<Tab>("offer");

  // Data
  const [sessions, setSessions] = useState<NegotiationSession[]>([]);
  const [incomingRequests, setIncomingRequests] = useState<ProductRequest[]>([]);
  const [dataLoading, setDataLoading] = useState(false);

  // Direct offer form
  const [doRetailerId, setDoRetailerId] = useState("");
  const [doProductId, setDoProductId] = useState("");
  const [doPrice, setDoPrice] = useState("");
  const [doVolume, setDoVolume] = useState("500");
  const [doDelivery, setDoDelivery] = useState("10");
  const [doPayment, setDoPayment] = useState("net30");
  const [doNotes, setDoNotes] = useState("");
  const [doMinPrice, setDoMinPrice] = useState("");
  const [doMinVolume, setDoMinVolume] = useState("200");
  const [doMaxVolume, setDoMaxVolume] = useState("2000");
  const [offerLoading, setOfferLoading] = useState(false);
  const [offerSuccess, setOfferSuccess] = useState(false);
  const [offerError, setOfferError] = useState<string | null>(null);

  // Per-request states
  const [requestStates, setRequestStates] = useState<Record<string, RequestState>>({});

  // Per-session negotiation states
  const [negStates, setNegStates] = useState<Record<string, NegotiationState>>({});

  // ── Derived ───────────────────────────────────────────────────────────────

  const activeSessions = sessions.filter((s) => ACTIVE_STATUSES.includes(s.status));
  const completedSessions = sessions.filter((s) => TERMINAL_STATUSES.includes(s.status));
  const pendingRequests = incomingRequests.filter(
    (r) => r.status === "pending" || r.status === "matched"
  );

  // ── Load catalog ──────────────────────────────────────────────────────────

  useEffect(() => {
    getCatalog()
      .then((data) => {
        setAllProducts(data.products);
        setSuppliers(data.suppliers);
        setRetailers(data.retailers);

        const firstSupplierId = data.suppliers[0]?.id ?? "";
        if (firstSupplierId) setSupplierId(firstSupplierId);

        if (data.retailers[0]) setDoRetailerId(data.retailers[0].id);

        // Pre-select first product of the first supplier
        const firstProduct = data.products.find(
          (p) => p.supplier_id === firstSupplierId
        ) ?? data.products[0];
        if (firstProduct) {
          setDoProductId(firstProduct.id);
          const bp = firstProduct.base_price ?? 0;
          if (bp) {
            setDoPrice(String(Math.round(bp * 1.1 * 100) / 100));
            setDoMinPrice(String(bp));
          }
        }
      })
      .catch(console.error)
      .finally(() => setCatalogLoading(false));
  }, []);

  // Derived: products belonging to the currently selected supplier
  const myProducts = allProducts.filter((p) => p.supplier_id === supplierId);

  // When the active supplier identity changes, reset to that supplier's first product
  useEffect(() => {
    if (!supplierId || allProducts.length === 0) return;
    const firstProduct = allProducts.find((p) => p.supplier_id === supplierId);
    if (firstProduct) {
      setDoProductId(firstProduct.id);
      const bp = firstProduct.base_price ?? 0;
      if (bp) {
        setDoPrice(String(Math.round(bp * 1.1 * 100) / 100));
        setDoMinPrice(String(bp));
      } else {
        setDoPrice("");
        setDoMinPrice("");
      }
    } else {
      setDoProductId("");
      setDoPrice("");
      setDoMinPrice("");
    }
  }, [supplierId, allProducts]);

  // Update price when product selection changes
  const handleProductChange = (productId: string) => {
    setDoProductId(productId);
    const p = myProducts.find((x) => x.id === productId);
    if (p?.base_price) {
      setDoPrice(String(Math.round(p.base_price * 1.1 * 100) / 100));
      setDoMinPrice(String(p.base_price));
    } else {
      setDoPrice("");
      setDoMinPrice("");
    }
  };

  // ── Load inbox data ───────────────────────────────────────────────────────

  const loadData = useCallback(async () => {
    if (!supplierId) return;
    setDataLoading(true);
    try {
      const [sess, reqs] = await Promise.allSettled([
        getSupplierSessions(supplierId),
        getRequestsForSupplier(supplierId),
      ]);
      if (sess.status === "fulfilled") setSessions(sess.value);
      if (reqs.status === "fulfilled") setIncomingRequests(reqs.value);
    } finally {
      setDataLoading(false);
    }
  }, [supplierId]);

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

  // ── Auto-populate negStates for HITL sessions not started by this dashboard ──

  useEffect(() => {
    sessions.forEach((session) => {
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
              minPrice: String(session.supplier_limits?.min_price ?? ""),
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
            message:
              session.status_message ||
              "Menschlicher Eingriff erforderlich — bitte Verhandlungsstand prüfen.",
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
              minPrice: String(session.supplier_limits?.min_price ?? ""),
              approvalPhase: false,
            },
          };
        });
      }
    });
  }, [sessions]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Tab 1: Create direct offer ────────────────────────────────────────────

  const handleCreateDirectOffer = async () => {
    if (!doProductId || !doRetailerId || !doPrice) return;
    setOfferLoading(true);
    setOfferError(null);
    setOfferSuccess(false);
    const supplierInfo = suppliers.find((s) => s.id === supplierId);
    const retailerInfo = retailers.find((r) => r.id === doRetailerId);
    try {
      await createDirectOffer({
        supplier_id: supplierId,
        supplier_name: supplierInfo?.name ?? supplierId,
        retailer_id: doRetailerId,
        retailer_name: retailerInfo?.name ?? doRetailerId,
        product_id: doProductId,
        offer_details: {
          unit_price: parseFloat(doPrice),
          volume: parseInt(doVolume),
          delivery_days: parseInt(doDelivery),
          payment_terms: doPayment,
          notes: doNotes || undefined,
        },
        supplier_constraints: {
          min_price: parseFloat(doMinPrice) || undefined,
          min_volume: parseInt(doMinVolume) || undefined,
          max_volume: parseInt(doMaxVolume) || undefined,
        },
      });
      setOfferSuccess(true);
      setDoNotes("");
      await loadData();
    } catch (e: any) {
      setOfferError(e.message);
    } finally {
      setOfferLoading(false);
    }
  };

  // ── Tab 2: Match products to incoming request ─────────────────────────────

  const handleMatchProducts = async (requestId: string) => {
    setRequestStates((prev) => ({
      ...prev,
      [requestId]: {
        ...getDefaultRequestState(requestId),
        ...prev[requestId],
        loading: true,
      },
    }));
    try {
      const result = await matchProducts(requestId, supplierId);
      const topMatch = result.matches?.[0];
      setRequestStates((prev) => ({
        ...prev,
        [requestId]: {
          ...prev[requestId],
          loading: false,
          matches: result.matches,
          showOfferForm: true,
          selectedProductId: topMatch?.product_id ?? "",
          offerPrice: topMatch ? String(Math.round(topMatch.base_price * 1.1 * 100) / 100) : "",
          minPrice: topMatch ? String(topMatch.base_price) : "",
          offerVolume: "500",
          offerDelivery: "10",
          offerPayment: "net30",
          offerNotes: "",
          minVolume: "200",
          maxVolume: "2000",
          submitted: false,
        },
      }));
    } catch (e: any) {
      setRequestStates((prev) => ({
        ...prev,
        [requestId]: {
          ...getDefaultRequestState(requestId),
          loading: false,
          error: e.message,
        },
      }));
    }
  };

  const getDefaultRequestState = (_requestId?: string): RequestState => ({
    loading: false,
    showOfferForm: false,
    selectedProductId: "",
    offerPrice: "",
    offerVolume: "500",
    offerDelivery: "10",
    offerPayment: "net30",
    offerNotes: "",
    minPrice: "",
    minVolume: "200",
    maxVolume: "2000",
    submitted: false,
  });

  const updateRequestState = (requestId: string, updates: Partial<RequestState>) => {
    setRequestStates((prev) => ({
      ...prev,
      [requestId]: { ...(prev[requestId] ?? getDefaultRequestState(requestId)), ...updates },
    }));
  };

  // ── Tab 2: Create offer from request ─────────────────────────────────────

const handleCreateOfferFromRequest = async (requestId: string) => {
    const rs = requestStates[requestId];
    if (!rs || !rs.selectedProductId || !rs.offerPrice) return;
    updateRequestState(requestId, { loading: true, error: undefined });
    try {
      // Step 1: Create offer — backend creates the session linked to the request
      const offerResult = await createOfferFromRequest(requestId, {
        supplier_id: supplierId,
        product_id: rs.selectedProductId,
        unit_price: parseFloat(rs.offerPrice),
        volume: parseInt(rs.offerVolume),
        delivery_days: parseInt(rs.offerDelivery),
        payment_terms: rs.offerPayment,
        notes: rs.offerNotes || undefined,
      });

      // Step 2: Set supplier constraints (private floor price, volumes) on the created session
      if (offerResult.session_id) {
        const limits: PartyLimits = {
          min_price: parseFloat(rs.minPrice) || undefined,
          min_volume: parseInt(rs.minVolume) || undefined,
          max_volume: parseInt(rs.maxVolume) || undefined,
        };
        await setSupplierConstraints(offerResult.session_id, limits);
      }

      // Offer is now waiting for the Retailer to set their constraints and start negotiation
      updateRequestState(requestId, { loading: false, submitted: true });
      await loadData();
      // Stay on requests tab — the Retailer must now respond
    } catch (e: any) {
      updateRequestState(requestId, { loading: false, error: e.message });
    }
  };

  const handleApprove = async (sessionId: string) => {
    try {
      await approveDeal(sessionId, "supplier");
      await loadData();
    } catch (e: any) {
      console.error(e);
    }
  };

  const handleReject = async (sessionId: string) => {
    try {
      await rejectDeal(sessionId, "supplier");
      await loadData();
    } catch (e: any) {
      console.error(e);
    }
  };

  // ── Render active session card ────────────────────────────────────────────

  const renderActiveSession = (session: NegotiationSession) => {
    const ns = negStates[session.session_id];
    const expanded = ns?.expanded ?? false;
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

    const retailer = retailers.find((r) => r.id === session.retailer_id);
    const autoLastRound = ns?.autoResult?.rounds[ns.autoResult.rounds.length - 1];
    const sessLastRound = session.rounds?.length
      ? (session.rounds[session.rounds.length - 1] as any)
      : undefined;
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
        <div className="px-5 py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-9 w-9 rounded-lg bg-violet-50 border border-violet-100 flex items-center justify-center shrink-0">
              <Package className="h-4 w-4 text-violet-600" />
            </div>
            <div className="min-w-0">
              <p className="font-medium text-slate-900 text-sm truncate">
                {session.product_name}
              </p>
              <p className="text-xs text-slate-500">
                {retailer?.name ?? session.retailer_id} · Runde {session.current_round}/{session.max_rounds}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <StatusBadge status={session.status} />
            {finalPrice && (
              <span className="text-sm font-semibold text-violet-600">
                {formatCurrency(finalPrice)}
              </span>
            )}
            <button
              onClick={() =>
                setNegStates((prev) => ({
                  ...prev,
                  [session.session_id]: {
                    ...(prev[session.session_id] ?? ({} as NegotiationState)),
                    expanded: !expanded,
                  } as NegotiationState,
                }))
              }
              className="p-1 rounded-md hover:bg-gray-100 text-slate-400 transition-colors"
            >
              {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {expanded && (
          <div className="border-t border-gray-100 px-5 py-4 space-y-4">
            {ns?.loading && (
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin text-violet-600" />
                KI-Agenten verhandeln...
              </div>
            )}

            {session.status === "hitl_required" && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-semibold text-amber-800">
                    Verhandlung pausiert — Retailer prüft
                  </p>
                  <p className="text-xs text-amber-700 mt-0.5">
                    {ns?.hitlTrigger?.message ||
                      "Der Retailer wurde um eine Entscheidung gebeten. Die Verhandlung wird fortgesetzt, sobald der Retailer reagiert."}
                  </p>
                </div>
              </div>
            )}

            {displayRounds.length > 0 && (
              <NegotiationTimeline
                rounds={displayRounds as any}
                myRole="supplier"
                zopaMin={ns?.autoResult?.zopa_min}
                zopaMax={ns?.autoResult?.zopa_max}
                running={ns?.loading}
              />
            )}

            {session.status === "pending_approval" && (
              <div className="bg-gray-50 rounded-xl border border-gray-200 p-4 space-y-3">
                <h4 className="font-medium text-slate-800 text-sm">
                  Verhandlung abgeschlossen — Ihre Entscheidung:
                </h4>
                <div className="grid grid-cols-3 gap-3">
                  <div className="text-center p-3 bg-white rounded-lg border border-gray-200">
                    <p className="text-xs text-slate-500 mb-1">Einigungspreis</p>
                    <p className="font-bold text-violet-600">
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
                        <p className="text-xs text-slate-500 mb-1">Gesamtumsatz</p>
                        <p className="font-semibold text-emerald-600">
                          {finalPrice && displayVolume
                            ? formatCurrency(finalPrice * displayVolume)
                            : "—"}
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
            <Badge variant="violet" dot>Supplier</Badge>
          </div>
          <div className="flex items-center gap-3">
            {/* Identity selector */}
            <select
              value={supplierId}
              onChange={(e) => setSupplierId(e.target.value)}
              className="text-sm border border-gray-200 rounded-lg px-3 h-8 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-500"
            >
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
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
            <TabButton active={activeTab === "offer"} onClick={() => setActiveTab("offer")}>
              <Plus className="h-3.5 w-3.5" />
              Neues Angebot
            </TabButton>
            <TabButton
              active={activeTab === "requests"}
              onClick={() => setActiveTab("requests")}
              count={pendingRequests.length}
            >
              <Inbox className="h-3.5 w-3.5" />
              Eingehende Anfragen
            </TabButton>
            <TabButton
              active={activeTab === "active"}
              onClick={() => setActiveTab("active")}
              count={activeSessions.length}
            >
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

        {/* ── TAB 1: Neues Angebot (Szenario 1 — proaktiv) ── */}
        {activeTab === "offer" && (
          <div className="max-w-2xl space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-900 mb-1">
                Proaktives Angebot erstellen
              </h2>
              <p className="text-sm text-slate-500">
                Sprechen Sie Retailer direkt an. Das Angebot erscheint in deren Posteingang 
                — Ihre Preisuntergrenzen bleiben vollständig vertraulich.
              </p>
            </div>

            {offerSuccess && (
              <Alert variant="success" title="Angebot erfolgreich gesendet!">
                Das Angebot wurde an den Retailer übermittelt und erscheint in dessen Posteingang.
              </Alert>
            )}
            {offerError && <Alert variant="error">{offerError}</Alert>}

            <Card noPad>
              <CardHeader>
                <h3 className="text-sm font-semibold text-slate-800">
                  Angebotsdetails
                </h3>
              </CardHeader>
              <CardBody className="space-y-4">
                <div className="grid sm:grid-cols-2 gap-4">
                  <Select
                    label="Produkt"
                    value={doProductId}
                    onChange={(e) => handleProductChange(e.target.value)}
                  >
                    {myProducts.length > 0 ? (
                      myProducts.map((p) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))
                    ) : (
                      <option value="" disabled>Keine Produkte für diesen Lieferanten</option>
                    )}
                  </Select>
                  <Select
                    label="Ziel-Retailer"
                    value={doRetailerId}
                    onChange={(e) => setDoRetailerId(e.target.value)}
                  >
                    {retailers.map((r) => (
                      <option key={r.id} value={r.id}>{r.name}</option>
                    ))}
                  </Select>
                </div>

                <div className="border-t border-gray-100 pt-4">
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
                    Angebotskonditionen
                  </p>
                  <div className="grid sm:grid-cols-2 gap-4">
                    <Input
                      label="Angebotspreis"
                      type="number"
                      prefix="€"
                      step="0.50"
                      value={doPrice}
                      onChange={(e) => setDoPrice(e.target.value)}
                      hint="Ihr Eröffnungspreis"
                    />
                    <Input
                      label="Menge"
                      type="number"
                      suffix="Stück"
                      value={doVolume}
                      onChange={(e) => setDoVolume(e.target.value)}
                    />
                    <Input
                      label="Lieferzeit"
                      type="number"
                      suffix="Tage"
                      value={doDelivery}
                      onChange={(e) => setDoDelivery(e.target.value)}
                    />
                    <Select
                      label="Zahlungsbedingungen"
                      value={doPayment}
                      onChange={(e) => setDoPayment(e.target.value)}
                    >
                      <option value="net7">Net 7</option>
                      <option value="net14">Net 14</option>
                      <option value="net30">Net 30</option>
                      <option value="net60">Net 60</option>
                      <option value="prepayment">Vorauszahlung</option>
                    </Select>
                  </div>
                </div>

                {/* Private constraints */}
                <div className="bg-amber-50 border border-amber-100 rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-amber-600" />
                    <p className="text-xs font-semibold text-amber-800 uppercase tracking-wide">
                      Ihre privaten Limits (vertraulich)
                    </p>
                  </div>
                  <p className="text-xs text-amber-700">
                    Diese Werte sieht der Retailer nie. Ihr KI-Agent verhandelt stets in diesen Grenzen.
                  </p>
                  <div className="grid sm:grid-cols-3 gap-3">
                    <Input
                      label="Mindestpreis"
                      type="number"
                      prefix="€"
                      step="0.50"
                      value={doMinPrice}
                      onChange={(e) => setDoMinPrice(e.target.value)}
                      hint="Absolute Untergrenze"
                    />
                    <Input
                      label="Mindest-Menge"
                      type="number"
                      suffix="Stück"
                      value={doMinVolume}
                      onChange={(e) => setDoMinVolume(e.target.value)}
                    />
                    <Input
                      label="Max. Menge"
                      type="number"
                      suffix="Stück"
                      value={doMaxVolume}
                      onChange={(e) => setDoMaxVolume(e.target.value)}
                    />
                  </div>
                </div>

                <Textarea
                  label="Nachricht an Retailer (optional)"
                  rows={2}
                  value={doNotes}
                  onChange={(e) => setDoNotes(e.target.value)}
                  placeholder="z.B. Sonderkonditionen für Großbestellung, Zertifizierungen, Verfügbarkeit..."
                />

                <div className="flex justify-end pt-1">
                  <Button
                    variant="primary"
                    size="lg"
                    loading={offerLoading}
                    disabled={!doProductId || !doRetailerId || !doPrice}
                    onClick={handleCreateDirectOffer}
                  >
                    <Send className="h-4 w-4" />
                    Angebot senden
                  </Button>
                </div>
              </CardBody>
            </Card>

            <div className="bg-violet-50 border border-violet-100 rounded-xl p-4 text-sm text-violet-800">
              <p className="font-medium mb-2">💡 Szenario 1 — Proaktive Angebote</p>
              <ol className="space-y-1 text-xs list-decimal list-inside text-violet-600">
                <li>Sie erstellen ein proaktives Angebot für einen Retailer</li>
                <li>Das Angebot erscheint in dessen "Eingehende Angebote" Tab</li>
                <li>Der Retailer setzt eigene Limits — die Sie nicht sehen</li>
                <li>KI-Agenten beider Seiten verhandeln autonom und argumentativ</li>
                <li>Bei Einigung: beide Seiten bestätigen den Deal</li>
              </ol>
            </div>
          </div>
        )}

        {/* ── TAB 2: Eingehende Anfragen (Szenario 2) ── */}
        {activeTab === "requests" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 mb-1">
                  Eingehende Anfragen
                </h2>
                <p className="text-sm text-slate-500">
                  Strukturierte Anfragen von Retailern — KI hat Freitext in strukturierten Bedarf umgewandelt.
                </p>
              </div>
              <Badge variant="violet">{pendingRequests.length} neu</Badge>
            </div>

            {pendingRequests.length === 0 && (
              <Card className="text-center py-12">
                <Inbox className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-slate-500 font-medium">Keine neuen Anfragen</p>
                <p className="text-xs text-slate-400 mt-1">
                  Neue Retailer-Anfragen erscheinen hier
                </p>
              </Card>
            )}

            <div className="space-y-3">
              {pendingRequests.map((req) => {
                const rs = requestStates[req.request_id] ?? getDefaultRequestState(req.request_id);
                const retailer = retailers.find((r) => r.id === req.retailer_id);

                return (
                  <Card key={req.request_id} noPad>
                    <div className="px-5 py-4">
                      {/* Request header */}
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <div className="flex items-center gap-3">
                          <div className="h-9 w-9 rounded-lg bg-sky-50 border border-sky-100 flex items-center justify-center shrink-0">
                            <Package className="h-4 w-4 text-sky-600" />
                          </div>
                          <div>
                            <p className="font-medium text-slate-900 text-sm">
                              {req.structured?.category ?? "Neue Anfrage"}
                            </p>
                            <p className="text-xs text-slate-500">
                              {retailer?.name ?? req.retailer_id} · {new Date(req.created_at).toLocaleDateString("de-DE")}
                            </p>
                          </div>
                        </div>
                        <Badge variant="sky" dot>Neu</Badge>
                      </div>

                      {/* Structured request summary */}
                      {req.structured && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                          {req.structured.product_description && (
                            <div className="bg-gray-50 rounded-lg p-2.5 col-span-2">
                              <p className="text-xs text-slate-500 mb-0.5">Bedarf</p>
                              <p className="text-sm text-slate-800 font-medium">{req.structured.product_description}</p>
                            </div>
                          )}
                          {req.structured.estimated_volume && (
                            <div className="bg-gray-50 rounded-lg p-2.5">
                              <p className="text-xs text-slate-500 mb-0.5">Menge</p>
                              <p className="text-sm text-slate-800 font-medium">{req.structured.estimated_volume}</p>
                            </div>
                          )}
                          {req.structured.budget_range && (
                            <div className="bg-gray-50 rounded-lg p-2.5">
                              <p className="text-xs text-slate-500 mb-0.5">Budget</p>
                              <p className="text-sm text-slate-800 font-medium">{req.structured.budget_range}</p>
                            </div>
                          )}
                          {req.structured.timeframe && (
                            <div className="bg-gray-50 rounded-lg p-2.5">
                              <p className="text-xs text-slate-500 mb-0.5">Zeitrahmen</p>
                              <p className="text-sm text-slate-800 font-medium">{req.structured.timeframe}</p>
                            </div>
                          )}
                          {req.structured.quality_tier && (
                            <div className="bg-gray-50 rounded-lg p-2.5">
                              <p className="text-xs text-slate-500 mb-0.5">Qualität</p>
                              <p className="text-sm text-slate-800 font-medium">{req.structured.quality_tier}</p>
                            </div>
                          )}
                          {req.structured.preferred_payment_terms && (
                            <div className="bg-gray-50 rounded-lg p-2.5">
                              <p className="text-xs text-slate-500 mb-0.5">Zahlungsziel</p>
                              <p className="text-sm text-slate-800 font-medium">{req.structured.preferred_payment_terms}</p>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Original text (collapsible) */}
                      <details className="mb-3">
                        <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-700 select-none">
                          Original-Anfrage anzeigen
                        </summary>
                        <div className="mt-2 bg-gray-50 rounded-lg px-3 py-2 text-xs text-slate-600 italic">
                          "{req.raw_text}"
                        </div>
                      </details>

                      {rs.error && <Alert variant="error" className="mb-3">{rs.error}</Alert>}

                      {/* Match button or results */}
                      {!rs.showOfferForm && !rs.submitted && (
                        <Button
                          variant="primary"
                          size="sm"
                          loading={rs.loading}
                          onClick={() => handleMatchProducts(req.request_id)}
                        >
                          Passende Produkte suchen
                        </Button>
                      )}

                      {rs.submitted && (
                        <Alert variant="success">
                          Angebot gesendet! Der Retailer setzt jetzt seine Konditionen — die Verhandlung startet automatisch sobald beide Seiten ihre Limits gesetzt haben.
                        </Alert>
                      )}

                      {/* Match results + offer form */}
                      {rs.showOfferForm && !rs.submitted && (
                        <div className="border-t border-gray-100 mt-3 pt-3 space-y-4">
                          {/* Product matches */}
                          {rs.matches && rs.matches.length > 0 && (
                            <div className="space-y-2">
                              <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                                Passende Produkte aus Ihrem Katalog
                              </p>
                              <div className="space-y-1.5">
                                {rs.matches.slice(0, 4).map((m) => (
                                  <label
                                    key={m.product_id}
                                    className={cn(
                                      "flex items-center justify-between rounded-lg border px-3 py-2.5 cursor-pointer transition-colors",
                                      rs.selectedProductId === m.product_id
                                        ? "border-violet-300 bg-violet-50"
                                        : "border-gray-200 bg-white hover:border-gray-300"
                                    )}
                                  >
                                    <div className="flex items-center gap-2">
                                      <input
                                        type="radio"
                                        name={`match-${req.request_id}`}
                                        value={m.product_id}
                                        checked={rs.selectedProductId === m.product_id}
                                        onChange={() => {
                                          updateRequestState(req.request_id, {
                                            selectedProductId: m.product_id,
                                            offerPrice: String(Math.round(m.base_price * 1.1 * 100) / 100),
                                            minPrice: String(m.base_price),
                                          });
                                        }}
                                        className="accent-violet-600"
                                      />
                                      <div>
                                        <p className="text-sm font-medium text-slate-800">{m.product_name}</p>
                                        <p className="text-xs text-slate-500">{m.match_reason}</p>
                                      </div>
                                    </div>
                                    <div className="text-right shrink-0">
                                      <p className="text-sm font-semibold text-slate-800">{formatCurrency(m.base_price)}</p>
                                      <p className="text-xs text-slate-400">Match: {Math.round(m.match_score * 100)}%</p>
                                    </div>
                                  </label>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Offer details */}
                          <div className="grid sm:grid-cols-2 gap-3">
                            <Input
                              label="Angebotspreis"
                              type="number"
                              prefix="€"
                              step="0.50"
                              value={rs.offerPrice}
                              onChange={(e) => updateRequestState(req.request_id, { offerPrice: e.target.value })}
                            />
                            <Input
                              label="Menge"
                              type="number"
                              suffix="Stück"
                              value={rs.offerVolume}
                              onChange={(e) => updateRequestState(req.request_id, { offerVolume: e.target.value })}
                            />
                            <Input
                              label="Lieferzeit"
                              type="number"
                              suffix="Tage"
                              value={rs.offerDelivery}
                              onChange={(e) => updateRequestState(req.request_id, { offerDelivery: e.target.value })}
                            />
                            <Select
                              label="Zahlungsbedingungen"
                              value={rs.offerPayment}
                              onChange={(e) => updateRequestState(req.request_id, { offerPayment: e.target.value })}
                            >
                              <option value="net7">Net 7</option>
                              <option value="net14">Net 14</option>
                              <option value="net30">Net 30</option>
                              <option value="net60">Net 60</option>
                              <option value="prepayment">Vorauszahlung</option>
                            </Select>
                          </div>

                          {/* Private constraints */}
                          <div className="bg-amber-50 border border-amber-100 rounded-xl p-3 space-y-2">
                            <div className="flex items-center gap-1.5">
                              <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                              <p className="text-xs font-semibold text-amber-800 uppercase tracking-wide">
                                Ihre privaten Limits (vertraulich)
                              </p>
                            </div>
                            <div className="grid grid-cols-3 gap-2">
                              <Input
                                label="Mindestpreis"
                                type="number"
                                prefix="€"
                                value={rs.minPrice}
                                onChange={(e) => updateRequestState(req.request_id, { minPrice: e.target.value })}
                              />
                              <Input
                                label="Mind.-Menge"
                                type="number"
                                suffix="Stück"
                                value={rs.minVolume}
                                onChange={(e) => updateRequestState(req.request_id, { minVolume: e.target.value })}
                              />
                              <Input
                                label="Max.-Menge"
                                type="number"
                                suffix="Stück"
                                value={rs.maxVolume}
                                onChange={(e) => updateRequestState(req.request_id, { maxVolume: e.target.value })}
                              />
                            </div>
                          </div>

                          <Textarea
                            label="Nachricht an Retailer (optional)"
                            rows={2}
                            value={rs.offerNotes}
                            onChange={(e) => updateRequestState(req.request_id, { offerNotes: e.target.value })}
                            placeholder="Hinweise zum Angebot, Sonderkonditionen..."
                          />

                          <div className="flex gap-2 pt-1">
                            <Button
                              variant="primary"
                              size="sm"
                              loading={rs.loading}
                              disabled={!rs.selectedProductId || !rs.offerPrice}
                              onClick={() => handleCreateOfferFromRequest(req.request_id)}
                            >
                              <Activity className="h-3.5 w-3.5" />
                              Angebot erstellen & Verhandlung starten
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => updateRequestState(req.request_id, { showOfferForm: false })}
                            >
                              Zurück
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
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
                  KI-Agenten verhandeln in Ihrem Namen. Bei Bedarf können Sie eingreifen.
                </p>
              </div>
              <Badge variant="violet">{activeSessions.length} aktiv</Badge>
            </div>

            {activeSessions.length === 0 && (
              <Card className="text-center py-12">
                <Activity className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-slate-500 font-medium">Keine laufenden Verhandlungen</p>
                <p className="text-xs text-slate-400 mt-1">
                  Bearbeiten Sie eine eingehende Anfrage oder erstellen Sie ein proaktives Angebot
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
                const retailer = retailers.find((r) => r.id === session.retailer_id);
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
                            ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                            : <XCircle className="h-4 w-4 text-red-500" />}
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium text-slate-900 text-sm truncate">
                            {session.product_name}
                          </p>
                          <p className="text-xs text-slate-500">
                            {retailer?.name ?? session.retailer_id} · {new Date(session.updated_at).toLocaleDateString("de-DE")}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {isAccepted && lastRound?.offer?.unit_price && (
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