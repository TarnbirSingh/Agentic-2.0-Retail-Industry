// ─────────────────────────────────────────────────────────────────────────────
// NegotiationTimeline — light SAP Fiori theme
// Shows negotiation rounds with expandable agent reasoning
// ─────────────────────────────────────────────────────────────────────────────

import { useState } from "react";
import { ChevronDown, ChevronUp, TrendingDown, TrendingUp, Minus, Loader2 } from "lucide-react";
import { cn, formatCurrency } from "../lib/utils";
import type { AgentRole } from "../lib/types";

interface RoundOffer {
  unit_price: number;
  volume: number;
  delivery_days: number;
  payment_terms: string;
  notes?: string;
}

interface AgentReasoning {
  reasoning_summary?: string;
  strategy?: string;
  thought?: string;
  final_answer?: string;
  steps?: string[];
}

interface NegotiationRound {
  round_number: number;
  role: AgentRole;
  offer: RoundOffer;
  is_valid: boolean;
  validation_message?: string;
  agent_reasoning?: AgentReasoning;
  timestamp?: string;
}

interface Props {
  rounds: NegotiationRound[];
  myRole: AgentRole;
  zopaMin?: number;
  zopaMax?: number;
  running?: boolean;
}

export function NegotiationTimeline({ rounds, myRole, zopaMin, zopaMax, running }: Props) {
  const [expandedRounds, setExpandedRounds] = useState<Set<number>>(new Set());

  const toggleRound = (n: number) => {
    setExpandedRounds((prev) => {
      const s = new Set(prev);
      if (s.has(n)) s.delete(n);
      else s.add(n);
      return s;
    });
  };

  if (rounds.length === 0 && !running) {
    return (
      <div className="text-center py-8 text-sm text-slate-400">
        Noch keine Verhandlungsrunden
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* ZOPA band */}
      {zopaMin !== undefined && zopaMax !== undefined && (
        <div className="rounded-lg bg-emerald-50 border border-emerald-100 px-4 py-2.5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            <span className="text-xs font-semibold text-emerald-700">
              ZOPA — Einigungsbereich
            </span>
          </div>
          <span className="text-xs font-bold text-emerald-800">
            {formatCurrency(zopaMin)} – {formatCurrency(zopaMax)}
          </span>
        </div>
      )}

      {/* Rounds */}
      <div className="space-y-1.5">
        {rounds.map((round, idx) => {
          const isMyRole = round.role === myRole;
          const prevPrice = idx > 0 ? rounds[idx - 1].offer.unit_price : null;
          const priceChange = prevPrice !== null ? round.offer.unit_price - prevPrice : null;
          const hasReasoning = round.agent_reasoning &&
            (round.agent_reasoning.reasoning_summary ||
              round.agent_reasoning.strategy ||
              round.agent_reasoning.thought ||
              (round.agent_reasoning.steps?.length ?? 0) > 0);
          const expanded = expandedRounds.has(round.round_number);

          return (
            <div
              key={round.round_number}
              className={cn(
                "rounded-lg border overflow-hidden transition-all",
                isMyRole
                  ? "border-sky-200 bg-sky-50"
                  : "border-violet-200 bg-violet-50",
                !round.is_valid && "border-red-200 bg-red-50 opacity-70"
              )}
            >
              {/* Round header */}
              <div className="px-4 py-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  {/* Role indicator */}
                  <div className={cn(
                    "h-7 w-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0",
                    isMyRole
                      ? "bg-sky-200 text-sky-800"
                      : "bg-violet-200 text-violet-800"
                  )}>
                    {round.round_number}
                  </div>

                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={cn(
                        "text-xs font-semibold uppercase tracking-wide",
                        isMyRole ? "text-sky-700" : "text-violet-700"
                      )}>
                        {isMyRole
                          ? (myRole === "retailer" ? "Einkäufer" : "Lieferant")
                          : (myRole === "retailer" ? "Lieferant" : "Einkäufer")}
                      </span>
                      <span className="font-bold text-slate-900 text-sm">
                        {formatCurrency(round.offer.unit_price)}
                      </span>
                      {priceChange !== null && (
                        <span className={cn(
                          "flex items-center gap-0.5 text-xs font-medium",
                          priceChange < 0 ? "text-emerald-600" : priceChange > 0 ? "text-red-500" : "text-slate-400"
                        )}>
                          {priceChange < 0
                            ? <TrendingDown className="h-3 w-3" />
                            : priceChange > 0
                            ? <TrendingUp className="h-3 w-3" />
                            : <Minus className="h-3 w-3" />}
                          {priceChange !== 0 ? formatCurrency(Math.abs(priceChange)) : "–"}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 text-xs text-slate-500">
                      <span>{round.offer.volume.toLocaleString()} Stück</span>
                      <span>{round.offer.delivery_days}d</span>
                      <span>{round.offer.payment_terms}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {!round.is_valid && (
                    <span className="text-xs text-red-600 font-medium">Ungültig</span>
                  )}
                  {hasReasoning && (
                    <button
                      onClick={() => toggleRound(round.round_number)}
                      className="p-1 rounded-md hover:bg-white/60 text-slate-400 transition-colors"
                      title="Argumentation anzeigen"
                    >
                      {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    </button>
                  )}
                </div>
              </div>

              {/* Expanded reasoning */}
              {expanded && hasReasoning && round.agent_reasoning && (
                <div className={cn(
                  "border-t px-4 py-3 text-xs space-y-1.5",
                  isMyRole ? "border-sky-200 bg-white/50" : "border-violet-200 bg-white/50"
                )}>
                  <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">
                    🤖 Agent-Argumentation
                  </p>
                  {round.agent_reasoning.reasoning_summary && (
                    <p className="text-slate-700 leading-relaxed">
                      {round.agent_reasoning.reasoning_summary}
                    </p>
                  )}
                  {round.agent_reasoning.strategy && (
                    <div className="flex gap-1.5">
                      <span className="font-medium text-slate-500 shrink-0">Strategie:</span>
                      <span className="text-slate-700">{round.agent_reasoning.strategy}</span>
                    </div>
                  )}
                  {round.agent_reasoning.thought && (
                    <div className="flex gap-1.5">
                      <span className="font-medium text-slate-500 shrink-0">Gedanke:</span>
                      <span className="text-slate-700">{round.agent_reasoning.thought}</span>
                    </div>
                  )}
                  {round.agent_reasoning.final_answer && (
                    <div className="flex gap-1.5">
                      <span className="font-medium text-slate-500 shrink-0">Entscheidung:</span>
                      <span className="text-slate-700">{round.agent_reasoning.final_answer}</span>
                    </div>
                  )}
                  {round.agent_reasoning.steps && round.agent_reasoning.steps.length > 0 && (
                    <div>
                      <span className="font-medium text-slate-500">Schritte:</span>
                      <ul className="mt-1 space-y-0.5 list-disc list-inside text-slate-600">
                        {round.agent_reasoning.steps.map((step, i) => (
                          <li key={i}>{step}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {round.validation_message && !round.is_valid && (
                    <div className="flex gap-1.5 text-red-600">
                      <span className="font-medium shrink-0">⚠ Problem:</span>
                      <span>{round.validation_message}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* Running indicator */}
        {running && (
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-[#0070d2]" />
            <span className="text-sm text-slate-500">
              KI-Agenten entwickeln nächstes Angebot...
            </span>
          </div>
        )}
      </div>
    </div>
  );
}