# Walk-Away Stagnation Detection - Analyse & Lösung

## Problem identifiziert ✓

### Testergebnisse (mit LLM)

**Test 1: No-ZOPA** (Retailer max €100 / Supplier min €130)
- ❌ FAILED: Erreichte `max_rounds` (15) statt autonomem Walk-Away
- Preisbewegungen beobachtet, aber keine Konvergenz

**Test 2: Narrow-ZOPA** (Retailer max €120 / Supplier min €115)  
- ⚠️ UNEXPECTED: Erreichte `max_rounds` (15) statt ACCEPTED
- Finale Position €117 (40% im ZOPA) - eigentlich akzeptabel

---

## Root Cause Analysis

### Aktuelle Stagnation Detection (risk_assessor.py)

```python
def _detect_stagnation(self, rounds_without_concession, expected_gain, opponent_room, price_gap):
    # Trigger: rounds_without_concession >= 4-5
    # Problem: rounds_without_concession zählt nur wenn Preisänderung < €0.10
```

### Wie rounds_without_concession gezählt wird (opponent_model.py)

```python
if abs(last_price_concession) < 0.10:  # < €0.10 = keine reale Konzession
    self._rounds_without_concession += 1
else:
    self._rounds_without_concession = 0  # RESET!
```

### Das Problem

Im **No-ZOPA Szenario** bewegen sich beide Parteien weiterhin (€1-2 pro Runde), daher:
- `rounds_without_concession` wird kontinuierlich auf 0 zurückgesetzt
- Stagnation Detection triggert NIE
- **Aber**: Es gibt keine Konvergenz! Die Preise divergieren.

**Beispiel aus Test 1:**
```
Runde  1: Retailer €100.00 | Supplier --
Runde  2: Retailer €100.00 | Supplier €155.00  (Gap: €55)
Runde  3: Retailer € 98.00 | Supplier €155.00  (Gap: €57) ← DIVERGENZ!
Runde  5: Retailer € 96.00 | Supplier €154.00  (Gap: €58)
Runde  7: Retailer € 97.00 | Supplier €153.00  (Gap: €56)
...
Runde 15: Retailer € 93.00 | Supplier €145.00  (Gap: €52)
```

Die Preise bewegen sich, aber der **Gap bleibt groß** und konvergiert nicht!

---

## Lösung: Konvergenz-basierte Stagnation Detection

### Neue Metrik: `convergence_rate`

Statt nur **Bewegung** zu tracken, müssen wir **Konvergenz** tracken:

```python
convergence_rate = (gap_4_rounds_ago - current_gap) / 4  # EUR pro Runde

if convergence_rate < threshold:
    # Keine sinnvolle Annäherung → Stagnation
```

### Implementierung

#### 1. OpponentModel erweitern (opponent_model.py)

Neue Methode hinzufügen:

```python
def get_convergence_rate(self, my_last_price: float) -> Optional[float]:
    """
    Berechnet die Konvergenzrate (EUR/Runde).
    Positiv = Annäherung, Negativ = Divergenz, ~0 = Stagnation
    """
    if len(self._price_history) < 4:
        return None
    
    # Gap vor 4 Runden
    old_gap = abs(self._price_history[-4] - my_last_price)
    
    # Aktueller Gap
    current_gap = abs(self._price_history[-1] - my_last_price)
    
    # Konvergenzrate (positive = gut, negative = divergenz)
    convergence_rate = (old_gap - current_gap) / 4
    
    return convergence_rate
```

#### 2. RiskAssessor erweitern (risk_assessor.py)

Neue Stagnation-Regel in `_detect_stagnation()`:

```python
def _detect_stagnation(
    self,
    rounds_without_concession: int,
    expected_gain: float,
    opponent_room: float,
    price_gap: Optional[float],
    convergence_rate: Optional[float] = None,  # NEU
) -> tuple[bool, str]:
    """Detect negotiation stagnation for autonomous walk-away."""
    
    # NEUE REGEL: Mangelnde Konvergenz bei großem Gap
    if convergence_rate is not None and price_gap is not None:
        if convergence_rate < 0.5 and price_gap > 20.0:
            # Weniger als €0.50/Runde Annäherung bei >€20 Gap
            return (
                True,
                f"Keine Konvergenz: Gap €{price_gap:.2f} schließt sich "
                f"nur mit €{convergence_rate:.2f}/Runde. "
                f"Parteien bewegen sich, aber keine Annäherung."
            )
    
    # Bestehende Regeln...
    if rounds_without_concession >= 5 and expected_gain < 0.50:
        ...
```

#### 3. NegotiationAgent anpassen (negotiation_agent.py)

Konvergenzrate berechnen und an RiskAssessor übergeben:

```python
# In _generate_counteroffer()
convergence_rate = None
if len(my_rounds) >= 4:
    convergence_rate = self._opponent_model.get_convergence_rate(
        my_last_price=my_rounds[-1].offer.unit_price
    )

assessment = self._risk_assessor.assess(
    # ... existing params ...
    convergence_rate=convergence_rate,  # NEU
)
```

---

## Erwartete Ergebnisse nach Fix

### Test 1: No-ZOPA (Gap €30)

**Vor Fix:**
- 15 Runden, erreicht max_rounds
- Kontinuierliche Bewegung, keine Stagnation Detection

**Nach Fix:**
- Nach ~5-6 Runden: Konvergenzrate < €0.50/Runde bei Gap > €20
- Trigger: "Keine Konvergenz" → WALK_AWAY_SIGNAL
- Autonomer Walk-Away **vor** max_rounds

### Test 2: Narrow-ZOPA (€115-120)

**Vor Fix:**
- 15 Runden, erreicht max_rounds bei €117

**Nach Fix:**
- Konvergenz sollte erkannt werden (Gap schließt sich)
- Keine false-positive Stagnation Detection
- Sollte mit ACCEPTED enden (nicht max_rounds)

---

## Nächste Schritte

1. ✅ Problem analysiert und Root Cause identifiziert
2. ⏳ Lösung implementieren:
   - `OpponentModel.get_convergence_rate()` hinzufügen
   - `RiskAssessor._detect_stagnation()` erweitern
   - `NegotiationAgent` anpassen
3. ⏳ Tests erneut ausführen
4. ⏳ Ergebnisse dokumentieren

---

## Technische Details

### Konvergenz-Schwellenwerte

```python
# Gute Konvergenz: > €2/Runde bei großem Gap
# Moderate Konvergenz: €0.5 - €2/Runde
# Stagnation: < €0.5/Runde bei Gap > €20
# Divergenz: negative Rate (Gap wird größer)
```

### Zeitfenster

- 4 Runden rückblickend für stabile Messung
- Min. 4 Runden History erforderlich
- Kombiniert mit existing rounds_without_concession für robuste Detection

### Edge Cases

1. **Frühe Runden** (< 4): Keine Konvergenz-Detection
2. **Small Gaps** (< €10): Keine false positives
3. **Volatile Bewegungen**: 4-Runden-Average glättet