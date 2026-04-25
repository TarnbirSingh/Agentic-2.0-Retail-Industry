"""
evaluation/viewer.py
────────────────────
Streamlit Evaluation Viewer für TradeBridge 2.0.

Starten:
    streamlit run evaluation/viewer.py
    python3 evaluation/viewer.py
"""

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TradeBridge 2.0 — Evaluation Viewer",
    layout="wide",
    initial_sidebar_state="expanded",
)

RESULTS_DIR = Path(__file__).parent / "results"
HAJ_FILE = RESULTS_DIR / "haj_ratings.json"

# ── Session-State Defaults ────────────────────────────────────────────────────
if "selected_scenario" not in st.session_state:
    st.session_state.selected_scenario = None
if "active_view" not in st.session_state:
    st.session_state.active_view = "📊 Dashboard"


# ══════════════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def load_all_runs() -> list[dict]:
    """Lädt alle eval_*.json (keine multirun), sortiert nach Timestamp absteigend."""
    files = sorted(
        [f for f in RESULTS_DIR.glob("eval_*.json") if "multirun" not in f.name],
        reverse=True,
    )
    runs = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
                data["_filename"] = f.name
                runs.append(data)
        except Exception:
            pass
    return runs


def load_multirun() -> dict | None:
    """Lädt das neueste eval_multirun_*.json falls vorhanden."""
    files = sorted(RESULTS_DIR.glob("eval_multirun_*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_haj_ratings() -> dict:
    """Lädt HAJ-Ratings aus haj_ratings.json."""
    if not HAJ_FILE.exists():
        return {}
    try:
        with open(HAJ_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_haj_rating(sid: str, arg: int, strat: int, conc: int, notes: str):
    """Speichert/überschreibt Rating für ein Szenario."""
    ratings = load_haj_ratings()
    ratings[sid] = {"arg": arg, "strat": strat, "conc": conc, "notes": notes}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(HAJ_FILE, "w", encoding="utf-8") as f:
        json.dump(ratings, f, indent=2, ensure_ascii=False)


def _status_color(status: str) -> str:
    """Gibt ein farbiges Emoji für einen Status zurück."""
    if status in ("accepted", "pending_approval"):
        return "🟢"
    if status in ("failed", "max_rounds_reached", "rejected"):
        return "🟡"
    if status == "ERROR":
        return "🔴"
    return "⚪"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("TradeBridge 2.0")
    st.caption("Evaluation Viewer")
    st.divider()

    view = st.radio(
        "Navigation",
        ["📊 Dashboard", "🔍 Szenario-Detail", "👤 Human as a Judge"],
        index=["📊 Dashboard", "🔍 Szenario-Detail", "👤 Human as a Judge"].index(
            st.session_state.active_view
        ),
        key="nav_radio",
    )
    st.session_state.active_view = view

    st.divider()

    all_runs = load_all_runs()
    multirun = load_multirun()

    if not all_runs:
        st.info("Keine Ergebnisse gefunden.")
        selected_run = None
    elif len(all_runs) == 1:
        selected_run = all_runs[0]
        st.caption(f"Run: {selected_run['_filename']}")
    else:
        run_labels = [r.get("timestamp_utc", r["_filename"]) for r in all_runs]
        chosen_label = st.selectbox("Run auswählen", run_labels)
        selected_run = all_runs[run_labels.index(chosen_label)]

    if multirun:
        st.success(f"Multi-Run verfügbar ({multirun['n_runs']} Runs)")


# ── Kein Run vorhanden ────────────────────────────────────────────────────────
if not all_runs:
    st.info(
        "Keine Evaluationsergebnisse gefunden. "
        "Führe zuerst `python3 tests/test_evaluation_scenarios.py` aus."
    )
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# VIEW 1: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.active_view == "📊 Dashboard":
    st.header("📊 Evaluations-Dashboard")

    kpis = selected_run.get("aggregate_kpis", {})
    waa_cm = selected_run.get("waa_confusion_matrix", {})

    # ── Metric-Cards ──────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    def _delta(key: str) -> str | None:
        if multirun:
            s = multirun["cross_run_statistics"].get(key, {})
            std = s.get("std")
            if std is not None:
                return f"±{std:.4f} über {multirun['n_runs']} Runs"
        return None

    with c1:
        st.metric("CSR Overall", f"{kpis.get('csr_overall', 0):.4f}", _delta("csr_overall"))
    with c2:
        st.metric("WAA F1", f"{kpis.get('waa_f1', 0):.4f}", _delta("waa_f1"))
    with c3:
        zu = kpis.get("zu_mean")
        st.metric("ZU Mean", f"{zu:.4f}" if zu is not None else "n/a", _delta("zu_mean"))
    with c4:
        st.metric("Outcome Accuracy", f"{kpis.get('outcome_accuracy', 0):.4f}", _delta("outcome_accuracy"))

    st.divider()

    # ── WAA Confusion Matrix ──────────────────────────────────────────────────
    col_cm, col_extra = st.columns([1, 2])
    with col_cm:
        st.subheader("WAA Confusion Matrix")
        tp = waa_cm.get("TP", 0)
        tn = waa_cm.get("TN", 0)
        fp = waa_cm.get("FP", 0)
        fn = waa_cm.get("FN", 0)

        cm_html = f"""
        <table style="border-collapse:collapse; text-align:center; width:260px;">
          <tr>
            <th style="padding:6px;"></th>
            <th style="padding:6px;">Pred: Abort</th>
            <th style="padding:6px;">Pred: Deal</th>
          </tr>
          <tr>
            <td style="padding:6px; font-weight:bold;">Actual: No-ZOPA</td>
            <td style="background:#2d6a2d; color:white; padding:10px; border-radius:4px;">
              TP = {tp}</td>
            <td style="background:#8b1a1a; color:white; padding:10px; border-radius:4px;">
              FN = {fn}<br><small>False Agreement!</small></td>
          </tr>
          <tr>
            <td style="padding:6px; font-weight:bold;">Actual: ZOPA</td>
            <td style="background:#cc7700; color:white; padding:10px; border-radius:4px;">
              FP = {fp}</td>
            <td style="background:#2d6a2d; color:white; padding:10px; border-radius:4px;">
              TN = {tn}</td>
          </tr>
        </table>
        """
        st.markdown(cm_html, unsafe_allow_html=True)
        far = kpis.get("false_agreement_rate", 0)
        mwr = kpis.get("missed_walkaway_rate", 0)
        if far == 0.0:
            st.success("False Agreement Rate: 0.0 ✓  (kein Deal trotz No-ZOPA)")
        else:
            st.error(f"False Agreement Rate: {far:.4f} ⚠️  (Deal trotz No-ZOPA!)")
        if mwr == 0.0:
            st.success("Missed Walkaway Rate: 0.0 ✓  (kein Walk-Away verpasst)")
        else:
            st.warning(f"Missed Walkaway Rate: {mwr:.4f}  (Walk-Away verpasst)")

    with col_extra:
        st.subheader("Weitere KPIs")
        extras = {
            "WAA Precision": kpis.get("waa_precision", 0),
            "WAA Recall": kpis.get("waa_recall", 0),
            "False Agreement Rate": kpis.get("false_agreement_rate", 0),
            "Missed Walkaway Rate": kpis.get("missed_walkaway_rate", 0),
            "ZU Median": kpis.get("zu_median"),
            "Ø Runden": kpis.get("avg_rounds", 0),
            "Agreement Rate": kpis.get("agreement_rate", 0),
            "Deals erreicht": kpis.get("deals_reached", 0),
            "Abbrüche": kpis.get("aborts", 0),
        }
        rows = []
        for k, v in extras.items():
            rows.append({"KPI": k, "Wert": f"{v:.4f}" if isinstance(v, float) and v is not None else str(v) if v is not None else "n/a"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.divider()

    # ── Szenario-Übersichtstabelle ────────────────────────────────────────────
    st.subheader("Szenarien")

    scenarios = selected_run.get("scenarios", [])
    rows = []
    for sc in scenarios:
        status = sc.get("actual_status", "")
        rows.append({
            "ID": sc.get("scenario_id", ""),
            "Name": sc.get("name", ""),
            "Kategorie": sc.get("category", ""),
            "Status": f"{_status_color(status)} {status}",
            "Runden": sc.get("rounds_used", 0),
            "Preis": f"€{sc['final_price']:.2f}" if sc.get("final_price") else "—",
            "CSR-S": f"{sc.get('csr_supplier', 0):.2f}",
            "CSR-R": f"{sc.get('csr_retailer', 0):.2f}",
            "ZU": f"{sc['zu']:.3f}" if sc.get("zu") is not None else "—",
            "OK": "✓" if sc.get("passed") else "✗",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.caption("Klicke auf ein Szenario unten, um es in Detail-View zu öffnen:")
    scenario_ids = [sc.get("scenario_id") for sc in scenarios]
    chosen = st.selectbox("Szenario öffnen →", scenario_ids, key="dash_jump")
    if st.button("🔍 Detail öffnen"):
        st.session_state.selected_scenario = chosen
        st.session_state.active_view = "🔍 Szenario-Detail"
        st.rerun()

    # ── Szenario-Stabilität (nur bei Multirun) ────────────────────────────────
    if multirun:
        st.divider()
        st.subheader(f"Szenario-Stabilität ({multirun['n_runs']} Runs)")
        stability = multirun.get("scenario_stability", {})
        for sid in sorted(stability.keys()):
            s = stability[sid]
            rate = s.get("stability_rate", 0)
            label = f"{sid}: {s['n_correct']}/{s['n_runs']}"
            if s.get("stable"):
                st.progress(rate, text=f"✓ {label}")
            else:
                outcomes_str = ", ".join(s.get("outcomes", []))
                st.progress(rate, text=f"✗ {label}  ← instabil | Outcomes: {outcomes_str}")


# ══════════════════════════════════════════════════════════════════════════════
# VIEW 2: SZENARIO-DETAIL
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.active_view == "🔍 Szenario-Detail":
    st.header("🔍 Szenario-Detail")

    scenarios = selected_run.get("scenarios", [])
    scenario_ids = [sc.get("scenario_id") for sc in scenarios]

    # Vorauswahl aus Dashboard-Navigation
    default_idx = 0
    if st.session_state.selected_scenario in scenario_ids:
        default_idx = scenario_ids.index(st.session_state.selected_scenario)

    chosen_sid = st.selectbox("Szenario", scenario_ids, index=default_idx)
    st.session_state.selected_scenario = chosen_sid

    sc = next((s for s in scenarios if s.get("scenario_id") == chosen_sid), None)
    if sc is None:
        st.error("Szenario nicht gefunden.")
        st.stop()

    # ── Metadaten-Header ──────────────────────────────────────────────────────
    col_l, col_r = st.columns(2)
    status = sc.get("actual_status", "")
    expected = sc.get("expected_outcome", "")
    outcome_ok = sc.get("outcome_correct", False)

    with col_l:
        st.markdown(f"**Name:** {sc.get('name', '')}")
        st.markdown(f"**Kategorie:** {sc.get('category', '')}")
        st.markdown(f"**Primär-KPI:** {sc.get('primary_kpi', '')}")
        outcome_icon = "✅" if outcome_ok else "❌"
        st.markdown(f"**Erwartet:** {expected} → **Tatsächlich:** {status} {outcome_icon}")
        if sc.get("final_price"):
            st.markdown(f"**Final-Preis:** €{sc['final_price']:.2f} | **Volumen:** {sc.get('final_volume')} | **Lieferung:** {sc.get('final_delivery_days')} Tage | **Zahlung:** {sc.get('final_payment_terms')}")

    with col_r:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("CSR Supplier", f"{sc.get('csr_supplier', 0):.2f}")
        m2.metric("CSR Retailer", f"{sc.get('csr_retailer', 0):.2f}")
        zu = sc.get("zu")
        m3.metric("ZU", f"{zu:.3f}" if zu is not None else "n/a")
        m4.metric("ZOPA-Pos.", sc.get("zopa_position", "n/a"))
        st.caption(f"Laufzeit: {sc.get('elapsed_sec', 0):.1f}s | {sc.get('rounds_used', 0)} Runden")

    # ── Preisverlauf-Chart ────────────────────────────────────────────────────
    price_path = sc.get("price_path", [])
    rounds_data = sc.get("rounds", [])

    if price_path:
        st.subheader("Preisverlauf")

        # Justification per Runde aufbauen für Hover
        justifications = {}
        for rnd in rounds_data:
            key = (rnd["round_number"], rnd["role"])
            just = rnd.get("offer", {}).get("justification", "")
            justifications[key] = just[:80] + "..." if len(just) > 80 else just

        supplier_points = [(p["round"], p["price"]) for p in price_path if p["role"] == "supplier"]
        retailer_points = [(p["round"], p["price"]) for p in price_path if p["role"] == "retailer"]

        fig = go.Figure()

        if supplier_points:
            s_x, s_y = zip(*supplier_points)
            s_hover = [justifications.get((x, "supplier"), "") for x in s_x]
            fig.add_trace(go.Scatter(
                x=list(s_x), y=list(s_y),
                mode="lines+markers",
                name="Supplier",
                line=dict(color="#1f77b4", width=2),
                marker=dict(symbol="circle", size=10),
                hovertemplate="Runde %{x}<br>Preis: €%{y:.2f}<br>%{customdata}<extra>Supplier</extra>",
                customdata=s_hover,
            ))

        if retailer_points:
            r_x, r_y = zip(*retailer_points)
            r_hover = [justifications.get((x, "retailer"), "") for x in r_x]
            fig.add_trace(go.Scatter(
                x=list(r_x), y=list(r_y),
                mode="lines+markers",
                name="Retailer",
                line=dict(color="#d62728", width=2),
                marker=dict(symbol="triangle-up", size=10),
                hovertemplate="Runde %{x}<br>Preis: €%{y:.2f}<br>%{customdata}<extra>Retailer</extra>",
                customdata=r_hover,
            ))

        fig.update_layout(
            title=f"Preisverlauf — {chosen_sid}",
            xaxis_title="Runde",
            yaxis_title="Preis (EUR)",
            hovermode="x unified",
            height=380,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Konzessionssequenzen
        sup_seq = sc.get("supplier_concession_sequence", [])
        ret_seq = sc.get("retailer_concession_sequence", [])
        if sup_seq or ret_seq:
            with st.expander("Konzessionssequenzen"):
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown("**Supplier** (negativ = Preissenkung)")
                    st.write(sup_seq if sup_seq else "—")
                with cc2:
                    st.markdown("**Retailer** (positiv = Preiserhöhung)")
                    st.write(ret_seq if ret_seq else "—")
    else:
        st.info("Keine Preispfad-Daten (älterer Run ohne Round-Export).")

    # ── Verhandlungsprotokoll ─────────────────────────────────────────────────
    if rounds_data:
        st.subheader("Verhandlungsprotokoll")

        # Constraint-Violations aufbauen für schnellen Lookup
        violations_by_round: dict[int, list] = {}
        for v in sc.get("constraint_violations", []):
            rn = v.get("round_number", 0)
            violations_by_round.setdefault(rn, []).append(v)

        for rnd in rounds_data:
            rn = rnd["round_number"]
            role = rnd["role"]
            offer = rnd.get("offer", {})
            raw = rnd.get("raw_offer")
            retry = rnd.get("retry_count", 0)
            is_valid = rnd.get("is_valid", True)
            val_msg = rnd.get("validation_message", "")

            role_badge = "🔵 Supplier" if role == "supplier" else "🔴 Retailer"
            retry_str = f"🔄 {retry}" if retry > 0 else "—"
            valid_str = "✓" if is_valid else f"✗ {val_msg}"
            viol_count = len(violations_by_round.get(rn, []))
            viol_str = f"⚠️ {viol_count}" if viol_count > 0 else "—"

            header = (
                f"**Rd {rn}** | {role_badge} | "
                f"€{offer.get('unit_price', 0):.2f} | "
                f"{offer.get('volume', '?')} Stk | "
                f"{offer.get('delivery_days', '?')} Tage | "
                f"{offer.get('payment_terms', '?')} | "
                f"Retry: {retry_str} | Valid: {valid_str} | Verletzung: {viol_str}"
            )

            with st.expander(header, expanded=False):
                just = offer.get("justification", "")
                if just:
                    st.markdown(f"**Begründung:** {just}")
                lev = offer.get("leverage_used")
                if lev:
                    st.markdown(f"**Leverage:** {lev}")

                if raw:
                    st.warning(
                        f"LLM-Rohangebot vor Clamping: "
                        f"€{raw.get('unit_price', '?'):.2f} | "
                        f"{raw.get('volume', '?')} Stk | "
                        f"{raw.get('delivery_days', '?')} Tage | "
                        f"{raw.get('payment_terms', '?')}"
                    )

                reasoning = rnd.get("agent_reasoning")
                if reasoning:
                    st.markdown("**Agent Reasoning:**")
                    st.json(reasoning)

                for v in violations_by_round.get(rn, []):
                    st.error(
                        f"Constraint-Verletzung | Feld: {v.get('field')} | "
                        f"{v.get('message')} (Wert: {v.get('current_value')}, Limit: {v.get('limit_value')})"
                    )
    else:
        st.info("Keine Round-Daten in diesem Run (älteres JSON-Format).")

    # ── Assertions ────────────────────────────────────────────────────────────
    assertions = sc.get("assertions", [])
    failures = sc.get("failures", [])
    if assertions:
        with st.expander("Test-Assertions"):
            for a in assertions:
                st.markdown(a)
    if failures:
        st.error("**Fehlgeschlagene Assertions:**")
        for f in failures:
            st.error(f"✗ {f}")


# ══════════════════════════════════════════════════════════════════════════════
# VIEW 3: HUMAN AS A JUDGE
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.active_view == "👤 Human as a Judge":
    st.header("👤 Human as a Judge")

    scenarios = selected_run.get("scenarios", [])
    haj_ratings = load_haj_ratings()

    # ── Fortschritt ───────────────────────────────────────────────────────────
    total = len(scenarios)
    rated = sum(1 for sc in scenarios if sc.get("scenario_id") in haj_ratings)
    st.progress(rated / total if total > 0 else 0, text=f"{rated} / {total} Szenarien bewertet")
    st.divider()

    # ── Bewertungsformular ────────────────────────────────────────────────────
    scenario_ids = [sc.get("scenario_id") for sc in scenarios]

    # Noch nicht bewertete Szenarien zuerst
    unrated = [sid for sid in scenario_ids if sid not in haj_ratings]
    rated_ids = [sid for sid in scenario_ids if sid in haj_ratings]
    ordered_ids = unrated + rated_ids

    chosen_sid = st.selectbox("Szenario bewerten", ordered_ids)
    sc = next((s for s in scenarios if s.get("scenario_id") == chosen_sid), None)

    if sc:
        col_info, col_form = st.columns([1, 2])

        with col_info:
            st.markdown("**Szenario-Info**")
            st.markdown(f"**{sc.get('scenario_id')}** — {sc.get('name', '')}")
            st.markdown(f"Kategorie: `{sc.get('category', '')}`")
            status = sc.get("actual_status", "")
            outcome_ok = sc.get("outcome_correct", False)
            st.markdown(f"Outcome: {_status_color(status)} `{status}` {'✅' if outcome_ok else '❌'}")
            if sc.get("final_price"):
                st.markdown(f"Preis: **€{sc['final_price']:.2f}**")
            st.markdown(f"Runden: **{sc.get('rounds_used', 0)}**")

            # Kurzer Preispfad
            price_path = sc.get("price_path", [])
            if price_path:
                prices = [f"R{p['round']} {p['role'][:3].upper()} €{p['price']:.0f}" for p in price_path]
                st.caption(" → ".join(prices))

        with col_form:
            existing = haj_ratings.get(chosen_sid, {})

            arg = st.slider(
                "Argumentationsqualität",
                1, 5,
                value=existing.get("arg", 3),
                help="Wie überzeugend und kohärent sind die Verhandlungsbegründungen des Agenten?",
            )
            strat = st.slider(
                "Strategieangemessenheit",
                1, 5,
                value=existing.get("strat", 3),
                help="Passt die gewählte Verhandlungsstrategie zum Szenario-Typ (ZOPA-Breite, No-ZOPA)?",
            )
            conc = st.slider(
                "Konzessionsmuster",
                1, 5,
                value=existing.get("conc", 3),
                help="Sind die Konzessionsschritte logisch und zielgerichtet?",
            )
            notes = st.text_area(
                "Anmerkungen (optional)",
                value=existing.get("notes", ""),
                height=80,
            )

            if st.button("💾 Bewertung speichern", type="primary"):
                save_haj_rating(chosen_sid, arg, strat, conc, notes)
                st.success(f"Bewertung für {chosen_sid} gespeichert.")
                st.rerun()

    st.divider()

    # ── Bewertungsübersicht ───────────────────────────────────────────────────
    if haj_ratings:
        st.subheader("Bewertungsübersicht")

        rows = []
        for sid, r in sorted(haj_ratings.items()):
            sc_match = next((s for s in scenarios if s.get("scenario_id") == sid), {})
            avg = round((r["arg"] + r["strat"] + r["conc"]) / 3, 2)
            rows.append({
                "ID": sid,
                "Name": sc_match.get("name", ""),
                "Arg.": r["arg"],
                "Strat.": r["strat"],
                "Konzess.": r["conc"],
                "Ø Score": avg,
                "Notiz": r.get("notes", "")[:60],
            })

        df_haj = pd.DataFrame(rows)
        st.dataframe(df_haj, hide_index=True, use_container_width=True)

        # Download-Button
        haj_json = json.dumps(haj_ratings, indent=2, ensure_ascii=False)
        st.download_button(
            label="📥 Als JSON exportieren",
            data=haj_json.encode("utf-8"),
            file_name="haj_ratings.json",
            mime="application/json",
        )
    else:
        st.info("Noch keine Bewertungen vorhanden.")


# ══════════════════════════════════════════════════════════════════════════════
# STARTEN-LOGIK
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import subprocess
    subprocess.run(["streamlit", "run", __file__] + sys.argv[1:])
