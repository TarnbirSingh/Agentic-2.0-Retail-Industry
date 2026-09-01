# TradeBridge 2.0 — Was macht das System zu "Agentic 2.0"?

## Die kurze Antwort

"Agentic 2.0" heißt: Die KI wartet nicht auf Anweisungen. Sie denkt selbst, plant, bewertet Risiken, modelliert den Gegenüber — und handelt autonom bis zum Abschluss. Das ist kein Chatbot, der Fragen beantwortet. Das ist ein System, das eine echte B2B-Verhandlung von Anfang bis Ende durchführt, ohne dass ein Mensch eingreifen muss.

---

## Was passiert unter der Haube pro Verhandlungsrunde?

Jede einzelne Runde (Supplier oder Retailer ist am Zug) läuft so ab:

```
Eingehende Situation
       ↓
[1] Situationsanalyse   → LLM-Call: Was ist gerade los? Bin ich in einer guten Position?
       ↓
[2] Taktikwahl          → LLM-Call: Wie gehe ich jetzt vor? Konzession, Anker halten, Tradeoff?
       ↓
[3] Angebotsgenerierung → LLM-Call: Welchen konkreten Preis/Konditionen biete ich an?
       ↓
[4] Begründung          → LLM-Call: Wie erkläre ich dem Gegenüber meinen Schritt professionell?
       ↓
Validierung + Retry (bis 3x bei Constraint-Verletzung)
       ↓
Angebot geht raus
```

**4 LLM-Calls pro Runde, pro Agent.** Das ist kein "gib mir einen Preis" — das ist strukturiertes strategisches Denken.

---

## Die Komponenten — wer macht was?

### `NegotiationAgent` — Der Verhandlungsführer
Das Herzstück. Jeder Agent (Supplier + Retailer) hat eine eigene Instanz.
Er koordiniert alle Sub-Komponenten und führt die 4 LLM-Calls durch.
Er hat eine **Persönlichkeit** — per Session-Seed zufällig generiert:
- `toughness` (0–1): Wie hart hält er seinen Preis?
- `patience` (0–1): Wie lange wartet er ohne Konzession?
→ Jede Verhandlung fühlt sich anders an, obwohl es dasselbe System ist.

---

### `AspirationManager` — Was will ich eigentlich erreichen?
Kein Agent verhandelt einfach von seinem Minimum nach oben. Der AspirationManager setzt ein **Ziel** (Aspiration Level) und passt es dynamisch an:
- Gibt der Gegenüber nach? → Ziel hochhalten.
- Stagniert die Verhandlung? → Ziel realistisch anpassen.
- Zeitdruck steigt? → Konzessionsbereitschaft wächst.

Das ist das Prinzip aus der Verhandlungsforschung (Faratin et al., 1998): Agenten kämpfen für ihr Ziel, nicht für ihr Minimum.

---

### `OpponentModel` — Wer sitzt mir gegenüber?
Der Agent beobachtet jede Runde und baut ein Modell des Gegenübers:
- Wie groß sind seine Konzessionen? (Stubbornness-Score)
- Gibt er bei Preis nach, aber hält bei Lieferzeit? (Attribut-Gewichtung)
- Ist er ein "Conceder" oder ein "Boulware"? (Konzessionsmuster)
- Wie ist der Ton seiner Begründungen? (Sentiment-Analyse)

→ Nach 2–3 Runden weiß der Agent ungefähr, wen er vor sich hat.

---

### `RiskAssessor` — Ist dieser Zug es wert?
Bevor ein Angebot rausgeht, bewertet der RiskAssessor:
- Wie viel Deal-Wert steht auf dem Spiel wenn ich zu hart bleibe?
- Wie hoch ist die Wahrscheinlichkeit, dass der Gegenüber abspringt?
- Lohnt sich noch eine Runde, oder ist die Einigung jetzt optimal?

→ Das ist der Unterschied zwischen "Algorithmus rechnet Kompromiss" und "Agent entscheidet strategisch".

---

### `TradeoffEngine` — Wenn Preis allein nicht mehr reicht
Stagniert die Verhandlung, schlägt der Agent Logrolling vor:
- "Ich gebe dir beim Preis 5 EUR nach, wenn du die Lieferzeit von 14 auf 10 Tage verkürzt."
- Jeder Dimension (Preis, Volumen, Lieferzeit, Zahlungsziel) hat Gewichtungen — Agent kalkuliert, welche Kombination für ihn besser ist als ein reiner Preisnachlass.

---

### `RequestAgent` — Der Türöffner (Szenario 2)
Bevor überhaupt verhandelt wird: Retailer schreibt einen Freitext.
Der RequestAgent (LLM-basiert) extrahiert daraus strukturierte Daten:
Kategorie, Volumen, Budget, Lieferfenster, Zahlungsziel, Qualitätsniveau.
Der Supplier-Agent matched dann semantisch die passenden Produkte aus dem Katalog.

→ Kein Formular. Kein Dropdown. Einfach Text rein, Angebot raus.

---

### `SimpleOrchestrator` — Der Dirigent
Er sieht das große Ganze. Kein Agent sieht die Limits des anderen — nur der Orchestrator weiß, ob eine ZOPA existiert.

Er entscheidet:
- Runde für Runde wechseln (Supplier → Retailer → Supplier…)
- Nach jeder Runde: Konvergenz erreicht? (Preislücke < 1,50 EUR → Einigung)
- **HITL-Trigger**: Läuft etwas schief? → Mensch wird eingebunden:
  - Agent bietet unter seinem eigenen Minimum an (ZOPA Breach)
  - Verhandlung stagniert (keine Bewegung über mehrere Runden)
  - Max-Runden wird überschritten

---

## Warum ist das "2.0" und nicht "1.0"?

| Agentic 1.0 | Agentic 2.0 (dieses System) |
|---|---|
| Regelbasiert: "Wenn Runde 3, dann X% Nachlass" | Situationsbasiert: Agent analysiert selbst |
| Ein LLM-Call: "Gib mir ein Angebot" | 4 strukturierte LLM-Calls pro Runde |
| Kein Gedächtnis über Runden hinweg | OpponentModel wächst über die gesamte Session |
| Symmetrische Agenten | Persönlichkeits-System: jede Session einzigartig |
| Mensch entscheidet alles | Mensch wird nur bei echten Eskalationen eingebunden (HITL) |
| Nur Preis | Multi-dimensionale Tradeoffs (Preis, Volumen, Lieferzeit, Zahlung) |

---

## Der Datenfluss in 30 Sekunden

```
Retailer tippt Freitext
    → RequestAgent strukturiert
    → Supplier sieht Anfrage, matched Produkt, setzt Angebot + private Untergrenze
    → Retailer setzt private Obergrenze
    → Orchestrator prüft ZOPA (intern, nicht sichtbar für Agenten)
    → NegotiationAgent (Supplier) → 4 LLM-Calls → Angebot
    → NegotiationAgent (Retailer) → 4 LLM-Calls → Gegenangebot
    → ... Runden laufen autonom ...
    → Preislücke < 1,50 EUR → Einigung
    → Beide Parteien bestätigen im UI
```

---

## Die eine Zahl für das Meeting

**140 Verhandlungssitzungen, 14 Szenarien, 10 Läufe je Szenario.**

- **98,4% CSR** — Agenten halten ihre eigenen Preislimits (fast) immer ein
- **FAR = 0,000** — System hat nie einen unmöglichen Deal als möglich eingestuft
- **Ø 0,303 ZU** — Einigungen landen näher am Supplier (Ankerstrategie bekannt, korrigierbar)
- **8 von 14 Szenarien** vollständig plausibel aus Sicht eines echten Einkäufers

---

*Gebaut von Tarnbir Singh, DHBW Mannheim / SAP SE, 2026*
