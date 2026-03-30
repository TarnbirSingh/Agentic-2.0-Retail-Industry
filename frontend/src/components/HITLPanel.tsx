// ─────────────────────────────────────────────────────────────────────────────
// HITLPanel — Human-in-the-Loop intervention panel
// ─────────────────────────────────────────────────────────────────────────────

import { useState } from "react";
import {
  AlertTriangle, XOctagon, ChevronDown, ChevronUp,
  Play, StopCircle, SlidersHorizontal,
  TrendingDown, TrendingUp, ArrowRightLeft,
} from "lucide-react";
import { Button, Input } from "./ui";
import { cn, formatCurrency } from "../lib/utils";
import type { HITLTrigger, PartyLimits, AgentRole } from "../lib/types";

interface HITLPanelProps {
  trigger: HITLTrigger;
  myRole: AgentRole;
  currentLimits: Partial<PartyLimits>;
  onContinue: () => void;
  onAdjustAndContinue: (newLimits: PartyLimits) => void;
  onAbort: (reason: string) => void;
  loading?: boolean;
}

export function HITLPanel({
  trigger,
  myRole,
  currentLimits,
  onContinue,
  onAdjustAndContinue,
  onAbort,
  loading,
}: HITLPanelProps) {
  const [showAdjustForm, setShowAdjustForm] = useState(false);
  const [abortReason, setAbortReason] = useState("");
  const [showAbortConfirm, setShowAbortConfirm] = useState(false);

  // Supplier constraint fields
  const [minPrice, setMinPrice] = useState(String(currentLimits.min_price ?? ""));
  const [minVolume, setMinVolume] = useState(String(currentLimits.min_volume ?? ""));
  const [maxVolume, setMaxVolume] = useState(String(currentLimits.max_volume ?? ""));

  // Retailer constraint fields
  const [maxPrice, setMaxPrice] = useState(String(currentLimits.max_price ?? ""));
  const [maxDelivery, setMaxDelivery] = useState(String(currentLimits.max_delivery_days ?? ""));
  const [retailPrice, setRetailPrice] = useState(String(currentLimits.retail_price ?? ""));
  const [targetMargin, setTargetMargin] = useState(
    String(currentLimits.target_margin ? Math.round(currentLimits.target_margin * 100) : "")
  );

  const isCritical = trigger.severity === "critical";
  const isWarning = trigger.severity === "warning";

  const handleAdjustAndContinue = () => {
    const newLimits: PartyLimits =
      myRole === "supplier"
        ? {
            min_price: parseFloat(minPrice) || undefined,
            min_volume: parseInt(minVolume) || undefined,
            max_volume: parseInt(maxVolume) || undefined,
          }
        : {
            max_price: parseFloat(maxPrice) || undefined,
            max_delivery_days: parseInt(maxDelivery) || undefined,
            retail_price: parseFloat(retailPrice) || undefined,
            target_margin: parseInt(targetMargin) / 100 || undefined,
          };
    onAdjustAndContinue(newLimits);
    setShowAdjustForm(false);
  };

  const handleAbort = () => {
    onAbort(abortReason);
    setShowAbortConfirm(false);
  };

  // ── Derive price metrics ─────────────────────────────────────────────────
  const supplierPrice = trigger.supplier_last_price;
  const retailerPrice = trigger.retailer_last_price;
  const priceGap = trigger.price_gap ??
    (supplierPrice != null && retailerPrice != null
      ? Math.abs(supplierPrice - retailerPrice)
      : undefined);

  // ZOPA: only show if both values are meaningful (> 0)
  const zopaValid =
    trigger.zopa_min != null &&
    trigger.zopa_max != null &&
    (trigger.zopa_min > 0 || trigger.zopa_max > 0);

  const hasMetrics =
    supplierPrice != null ||
    retailerPrice != null ||
    priceGap != null ||
    zopaValid ||
    trigger.rounds_remaining != null;

  return (
    <div
      className={cn(
        "rounded-xl border p-4 space-y-4",
        isCritical
          ? "bg-red-50 border-red-200"
          : isWarning
          ? "bg-amber-50 border-amber-200"
          : "bg-blue-50 border-blue-200"
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "h-9 w-9 rounded-lg flex items-center justify-center shrink-0 mt-0.5",
            isCritical
              ? "bg-red-100 border border-red-200"
              : isWarning
              ? "bg-amber-100 border border-amber-200"
              : "bg-blue-100 border border-blue-200"
          )}
        >
          {isCritical ? (
            <XOctagon className="h-5 w-5 text-red-600" />
          ) : (
            <AlertTriangle className="h-5 w-5 text-amber-600" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span
              className={cn(
                "text-xs font-bold uppercase tracking-widest",
                isCritical ? "text-red-700" : isWarning ? "text-amber-700" : "text-blue-700"
              )}
            >
              {isCritical
                ? "⛔ Kritischer Eingriff erforderlich"
                : isWarning
                ? "⚠ Eingriff empfohlen"
                : "ℹ Information"}
            </span>
          </div>
          <p
            className={cn(
              "text-sm font-medium",
              isCritical ? "text-red-800" : isWarning ? "text-amber-800" : "text-blue-800"
            )}
          >
            {trigger.message}
          </p>
          <p
            className={cn(
              "text-xs mt-1",
              isCritical ? "text-red-600" : isWarning ? "text-amber-600" : "text-blue-600"
            )}
          >
            Empfehlung: {trigger.recommended_action}
          </p>
        </div>
      </div>

      {/* Context metrics — clean two-party view */}
      {hasMetrics && (
        <div
          className={cn(
            "rounded-lg border px-4 py-3 grid gap-3",
            isCritical ? "bg-white border-red-100" : isWarning ? "bg-white border-amber-100" : "bg-white border-blue-100",
            // dynamic column count
            [supplierPrice, retailerPrice, priceGap, zopaValid, trigger.rounds_remaining != null]
              .filter(Boolean).length >= 4
              ? "grid-cols-2 sm:grid-cols-4"
              : "grid-cols-2 sm:grid-cols-3"
          )}
        >
          {/* Supplier last price */}
          {supplierPrice != null && (
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-1 text-xs text-slate-500">
                <TrendingUp className="h-3 w-3 text-violet-500" />
                Lieferant zuletzt
              </div>
              <p className="font-bold text-violet-700">{formatCurrency(supplierPrice)}</p>
            </div>
          )}

          {/* Retailer last price */}
          {retailerPrice != null && (
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-1 text-xs text-slate-500">
                <TrendingDown className="h-3 w-3 text-sky-500" />
                Einkäufer zuletzt
              </div>
              <p className="font-bold text-sky-700">{formatCurrency(retailerPrice)}</p>
            </div>
          )}

          {/* Price gap */}
          {priceGap != null && (
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-1 text-xs text-slate-500">
                <ArrowRightLeft className="h-3 w-3 text-slate-400" />
                Preis-Gap
              </div>
              <p
                className={cn(
                  "font-bold",
                  priceGap <= 2 ? "text-emerald-600" : priceGap <= 10 ? "text-amber-600" : "text-red-600"
                )}
              >
                {formatCurrency(priceGap)}
              </p>
            </div>
          )}

          {/* ZOPA range */}
          {zopaValid && (
            <div className="flex flex-col gap-0.5">
              <p className="text-xs text-slate-500">ZOPA-Bereich</p>
              <p className="font-bold text-emerald-700 text-sm">
                {formatCurrency(trigger.zopa_min!)} – {formatCurrency(trigger.zopa_max!)}
              </p>
            </div>
          )}

          {/* Rounds remaining */}
          {trigger.rounds_remaining != null && (
            <div className="flex flex-col gap-0.5">
              <p className="text-xs text-slate-500">Verbl. Runden</p>
              <p
                className={cn(
                  "font-bold",
                  trigger.rounds_remaining <= 2 ? "text-red-600" : "text-slate-900"
                )}
              >
                {trigger.rounds_remaining}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" size="sm" loading={loading} onClick={onContinue}>
          <Play className="h-3.5 w-3.5" />
          Weiter verhandeln
        </Button>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => setShowAdjustForm(!showAdjustForm)}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
          Limits anpassen
          {showAdjustForm ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </Button>

        <Button
          variant="danger"
          size="sm"
          onClick={() => setShowAbortConfirm(!showAbortConfirm)}
        >
          <StopCircle className="h-3.5 w-3.5" />
          Abbrechen
        </Button>
      </div>

      {/* Adjust limits form */}
      {showAdjustForm && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
            Neue private Limits setzen
          </p>
          <p className="text-xs text-slate-500">
            Diese Anpassung bleibt vertraulich. Ihr Agent verhandelt mit den neuen Grenzen weiter.
          </p>

          {myRole === "supplier" ? (
            <div className="grid grid-cols-3 gap-3">
              <Input
                label="Mindestpreis"
                type="number"
                prefix="€"
                step="0.50"
                value={minPrice}
                onChange={(e) => setMinPrice(e.target.value)}
              />
              <Input
                label="Min-Menge"
                type="number"
                suffix="Stück"
                value={minVolume}
                onChange={(e) => setMinVolume(e.target.value)}
              />
              <Input
                label="Max-Menge"
                type="number"
                suffix="Stück"
                value={maxVolume}
                onChange={(e) => setMaxVolume(e.target.value)}
              />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Max. Preis"
                type="number"
                prefix="€"
                step="0.50"
                value={maxPrice}
                onChange={(e) => setMaxPrice(e.target.value)}
              />
              <Input
                label="Max. Lieferzeit"
                type="number"
                suffix="Tage"
                value={maxDelivery}
                onChange={(e) => setMaxDelivery(e.target.value)}
              />
              <Input
                label="Verkaufspreis"
                type="number"
                prefix="€"
                step="0.50"
                value={retailPrice}
                onChange={(e) => setRetailPrice(e.target.value)}
              />
              <Input
                label="Ziel-Marge"
                type="number"
                suffix="%"
                value={targetMargin}
                onChange={(e) => setTargetMargin(e.target.value)}
              />
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button
              variant="primary"
              size="sm"
              loading={loading}
              onClick={handleAdjustAndContinue}
            >
              Anpassen & Weiterverhandeln
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setShowAdjustForm(false)}>
              Abbrechen
            </Button>
          </div>
        </div>
      )}

      {/* Abort confirm */}
      {showAbortConfirm && (
        <div className="bg-white rounded-lg border border-red-200 p-4 space-y-3">
          <p className="text-sm font-semibold text-red-800">
            Verhandlung wirklich abbrechen?
          </p>
          <Input
            label="Begründung (optional)"
            value={abortReason}
            onChange={(e) => setAbortReason(e.target.value)}
            placeholder="Preisvorstellungen zu weit auseinander..."
          />
          <div className="flex gap-2">
            <Button variant="danger" size="sm" loading={loading} onClick={handleAbort}>
              Ja, abbrechen
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setShowAbortConfirm(false)}>
              Zurück
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}