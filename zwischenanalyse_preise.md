# Zwischenanalyse: Endpreis-Verteilung (erste 4 Szenarien)

## Datenübersicht

| # | Szenario | ZOPA | Endpreis | Supplier Min | Position im ZOPA | Bewertung |
|---|----------|------|----------|--------------|------------------|-----------|
| 1 | Wide ZOPA (Bosch Drill) | €145–€220 | **€160.50** | €145 | 21% vom Min | ✅ Gut |
| 2 | Narrow ZOPA (Makita) | €115–€125 | **€124.00** | €115 | 90% vom Min | ⚠️ Sehr nah |
| 3 | STIHL Chainsaw | €239–€330 | **€297.00** | €239 | 64% vom Min | ✅ Gut |
| 4 | High Volume (Kärcher) | €289–€420 | **€361.85** | €289 | 56% vom Min | ✅ Gut |

---

## Analyse: Preisrestriktion-Exploitation

### ✅ **Positiv**: 3 von 4 Szenarien zeigen gute Verhandlungsambitio n

**Szenarien 1, 3, 4** landen bei **21–64% vom Supplier-Minimum entfernt** im ZOPA:
- **€160.50** statt €145 → +€15.50 (21% des ZOPA-Range)
- **€297.00** statt €239 → +€58.00 (64% des ZOPA-Range)
- **€361.85** statt €289 → +€72.85 (56% des ZOPA-Range)

Das bedeutet: Der Supplier **schützt erfolgreich** einen Puffer über der Resistance-Grenze.

---

### ⚠️ **Problem identifiziert**: Narrow ZOPA (€115–€125)

**Szenario 2** zeigt das kritische Verhalten:
- ZOPA-Breite: nur **€10** (€115–€125)
- Endpreis: **€124.00** 
- Position: **90% vom Supplier-Min entfernt** → fast an der Obergrenze

**Warum ist das problematisch?**
1. Bei schmalen ZOPAs konvergiert der Preis zu stark in Richtung Retailer-Maximum
2. Der Supplier hätte €124 statt €115 erreicht → gut, ABER der Retailer-Max war €125
3. Der Supplier landet bei 90% des ZOPA → **der Retailer hat gewonnen**

---

## Root Cause: Aspiration-Decay bei schmalen ZOPAs

Bei **Narrow ZOPA** passiert:
1. Supplier startet mit Aspiration bei Target-Preis (z.B. €160 in Scenario 2)
2. Retailer bietet €120 (innerhalb ZOPA €115–€125)
3. **OpponentModel** erkennt: "Retailer ist near their limit" (stubbornness hoch)
4. **RiskAssessor** berechnet: "Walk-away Probability hoch" + "Expected Gain gering"
5. **AspirationManager** senkt Aspiration aggressiv
6. Ergebnis: Supplier akzeptiert €124 statt weiter zu pushen

**Das Problem**: Bei schmalen ZOPAs ist die Aspiration-Decay-Rate zu hoch, weil:
- `opponent_pressure` wird hoch eingeschätzt (Retailer hat wenig Spielraum)
- `time_pressure` steigt mit Runden
- Beide Faktoren zusammen → starke Aspiration-Senkung

---

## Empfehlung

### Option 1: **Minimum Buffer erhöhen** (konservativ)
```python
# agents/aspiration_manager.py, Zeile 90
minimum_buffer_pct: float = 0.10,  # Von 3% auf 10%
```
→ Aspiration bleibt mindestens 10% über Resistance statt 3%

### Option 2: **Aspiration-Decay-Rate anpassen** (gezielt)
```python
# agents/aspiration_manager.py, Zeile 369
base_decay = remaining_to_min * 0.003  # Von 0.005 auf 0.003 (40% langsamer)
```
→ Aspiration sinkt langsamer, Supplier behält Ambition länger

### Option 3: **ZOPA-Width-Aware Decay** (intelligent)
```python
# Neue Logik: Bei schmalen ZOPAs (<€15) decay um 50% reduzieren
zopa_width = abs(self.target_price - self.resistance_price)
if zopa_width < 15.0:
    base_decay *= 0.5  # Halbe Decay-Rate bei schmalen ZOPAs
```
→ Bei Narrow ZOPA wird Ambition geschützt

---

## Zwischenfazit

✅ **System funktioniert grundsätzlich gut** (3/4 Szenarien)  
⚠️ **Schmale ZOPAs sind ein Edge Case**, der Tuning benötigt  
🎯 **Empfehlung**: Option 3 (ZOPA-Width-Aware Decay) implementieren

---

*Warten auf vollständige 16 Szenarien für finale Analyse...*