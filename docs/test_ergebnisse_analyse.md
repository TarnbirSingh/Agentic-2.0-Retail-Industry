# Test-Ergebnisse Analyse - Walk-Away Stagnation Detection

## Test 1: No-ZOPA Szenario

**Erwartung:** Autonomer Walk-Away nach ~5-6 Runden durch Konvergenz-Detection

**Tatsächliches Ergebnis:** ❌ FAILED
- Status: `max_rounds_reached` (15 Runden)
- Gap bleibt groß: €96.75 (Retailer) ↔ €148.00 (Supplier) = **€51.25**
- Keine Stagnation Detection getriggert

### Preisverlauf Analyse

| Runde | Retailer | Supplier | Gap |
|-------|----------|----------|-----|
| 1 | €95.00 | - | - |
| 2 | €95.00 | €155.00 | €60.00 |
| 3 | €96.00 | €155.00 | €59.00 |
| 4 | €96.00 | €154.00 | €58.00 |
| 5 | €97.00 | €154.00 | €57.00 |
| 6 | €97.00 | €152.00 | €55.00 |
| 7 | €95.50 | €152.00 | €56.50 |
| 8 | €95.50 | €151.00 | €55.50 |
| 9 | €96.50 | €151.00 | €54.50 |
| 10 | €96.50 | €150.00 | €53.50 |
| 11 | €96.50 | €150.00 | €53.50 |
| 12 | €96.50 | €149.00 | €52.50 |
| 13 | €95.75 | €149.00 | €53.25 |
| 14 | €95.75 | €148.00 | €52.25 |
| 15 | €96.75 | €148.00 | €51.25 |

**Konvergenz-Berechnung (Runden 2-5):**
- Gap Runde 2: €60.00
- Gap Runde 5: €57.00
- **Konvergenzrate: (60 - 57) / 3 = €1.00/Runde**

**Konvergenz-Berechnung (Runden 6-9):**
- Gap Runde 6: €55.00
- Gap Runde 9: €54.50
- **Konvergenzrate: (55 - 54.5) / 3 = €0.17/Runde** ← Sollte triggern!

**Konvergenz-Berechnung (Runden 11-14):**
- Gap Runde 11: €53.50
- Gap Runde 14: €52.25
- **Konvergenzrate: (53.5 - 52.25) / 3 = €0.42/Runde** ← Sollte triggern!

### Problem identifiziert

Die Konvergenz-Detection sollte bei Runde 9+ triggern:
- `convergence_rate < 0.5` ✅ (0.17 bzw. 0.42)
- `price_gap > 20.0` ✅ (>€50)

**Warum hat es nicht getriggert?**

Mögliche Ursachen:
1. **OpponentModel hat < 4 Runden History** - Konvergenzrate wird als `None` zurückgegeben
2. **my_current_price** nicht korrekt übergeben - Gap-Berechnung fehlerhaft
3. **Konvergenz wird vom Retailer berechnet** - aber Supplier-Preise werden genutzt?

---

## Test 2: Narrow-ZOPA Szenario

**Erwartung:** Erfolgreiche Verhandlung ohne false-positive Walk-Away

**Tatsächliches Ergebnis:** ✅ SUCCESS
- Status: `pending_approval` nach 12 Runden
- Finaler Preis: €121.50
- Position im ZOPA: 130% (außerhalb, aber akzeptiert)

### Preisverlauf

| Runde | Retailer | Supplier | Gap |
|-------|----------|----------|-----|
| 1 | €108.58 | - | - |
| 2 | €108.58 | €125.00 | €16.42 |
| 5 | €110.58 | €123.00 | €12.42 |
| 8 | €119.00 | €122.00 | €3.00 |
| 11 | €120.00 | €122.50 | €2.50 |
| 12 | €120.00 | €121.50 | €1.50 |

**Konvergenzrate (Runden 8-11):**
- Gap Runde 8: €3.00
- Gap Runde 11: €2.50
- **Konvergenzrate: (3.00 - 2.50) / 3 = €0.17/Runde**

Obwohl Konvergenzrate < €0.50, triggert es nicht weil:
- `price_gap = €2.50` < 20.0 ✅ (Schwellwert korrekt)

---

## Diagnose

### Vermutete Root Cause

Die Konvergenz-Berechnung in `OpponentModel.get_convergence_rate()` nutzt:

```python
old_gap = abs(self._price_history[-4] - my_last_price)
current_gap = abs(self._price_history[-1] - my_last_price)
```

**Problem:** `my_last_price` ist der **Agent's eigener Preis**, nicht der korrekte Referenzpreis für Gap-Berechnung!

**Im No-ZOPA Test:**
- Retailer berechnet: `abs(supplier_price - retailer_own_price)`
- Supplier berechnet: `abs(retailer_price - supplier_own_price)`

**Aber:** Beide sollten denselben Gap messen!

### Beispiel-Rechnung (Retailer Perspektive, Runde 9):

```python
# OpponentModel._price_history enthält Supplier-Preise
old_gap = abs(155.00 - 96.50)  # Runde 2 Supplier - Runde 9 Retailer = 58.50
current_gap = abs(151.00 - 96.50)  # Runde 9 Supplier - Runde 9 Retailer = 54.50
convergence_rate = (58.50 - 54.50) / 4 = 1.00
```

**Das ist falsch!** Wir vergleichen:
- `old_gap`: Supplier Runde 2 vs. Retailer Runde 9
- `current_gap`: Supplier Runde 9 vs. Retailer Runde 9

Die Zeitstempel stimmen nicht überein!

---

## Lösung

### Fix 1: Korrekter Gap-Berechnung

`OpponentModel.get_convergence_rate()` muss überarbeitet werden:

```python
def get_convergence_rate(self, my_rounds: list) -> Optional[float]:
    """
    Berechnet Konvergenzrate basierend auf parallelen Runden.
    """
    if len(self._price_history) < 4 or len(my_rounds) < 4:
        return None
    
    # Nehme letzte 4 opponent-Preise und entsprechende eigene Preise
    opp_old = self._price_history[-4]
    opp_current = self._price_history[-1]
    
    my_old = my_rounds[-4] if len(my_rounds) >= 4 else my_rounds[0]
    my_current = my_rounds[-1]
    
    old_gap = abs(opp_old - my_old)
    current_gap = abs(opp_current - my_current)
    
    return (old_gap - current_gap) / 4.0
```

### Fix 2: NegotiationAgent muss my_rounds übergeben

Aktuell:
```python
convergence_rate = self._opponent_model.get_convergence_rate(my_current_price)
```

Sollte sein:
```python
my_rounds = [r for r in history if r.role == self.role]
convergence_rate = self._opponent_model.get_convergence_rate(my_rounds)
```

---

## Zusammenfassung

**Test 1 (No-ZOPA):** ❌ Konvergenz-Detection hat **nicht** getriggert
- **Grund:** Fehlerhafte Gap-Berechnung durch Zeitstempel-Mismatch
- **Konvergenzrate wurde falsch berechnet** oder war `None`

**Test 2 (Narrow-ZOPA):** ✅ Erfolgreich ohne false-positive
- Korrekt, weil Gap < €20 (Schwellwert funktioniert)

**Fix benötigt:** `OpponentModel.get_convergence_rate()` + `NegotiationAgent` Aufruf