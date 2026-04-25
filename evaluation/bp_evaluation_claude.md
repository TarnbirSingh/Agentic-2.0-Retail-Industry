# BP-Evaluation: TradeBridge 2.0 — Human-as-a-Judge
**Evaluator:** Claude (Anthropic) — simulierter Retail-Einkaufsexperte  
**Grundlage:** Alle 10 Läufe (`eval_2026-04-22T21-40-28Z` bis `eval_2026-04-23T02-20-24Z`)  
**Quervergleich:** Lauf 1 vollständig gelesen; Läufe 2–10 systematisch auf alle 14 Szenarien ausgewertet  
**Datum der Begutachtung:** 2026-04-24

---

## Überblick: Outcome-Stabilität über 10 Läufe

| Szenario | Produkt | Erwartetes Ergebnis | Stability | Typisches Outcome |
|---|---|---|---|---|
| S01 | Bosch GSR 18V-90 C | DEAL | 10/10 ✓ | accepted / pending_approval |
| S02 | Kärcher K5 | DEAL | 10/10 ✓ | pending_approval / accepted |
| S03 | Weber iGrill 3 | DEAL | 10/10 ✓ | accepted |
| S04 | Makita DHP486Z | DEAL | 8/10 | pending/accepted (2× max_rounds) |
| S05 | Bosch X-LOCK | DEAL | 7/10 | pending (3× max_rounds) |
| S06 | GARDENA Water Control | DEAL | 6/10 | pending / max_rounds (4× Fehler) |
| S07 | STIHL MSA 140 | ABORT | 10/10 ✓ | max_rounds_reached |
| S08 | DEWALT TSTAK VI | ABORT | 10/10 ✓ | max_rounds_reached |
| S09 | Makita DGA513Z | ABORT | 10/10 ✓ | max_rounds_reached |
| S10 | Kärcher T7 Plus | ABORT | 10/10 ✓ | max_rounds_reached |
| S11 | Bosch 18V Akku-Set | DEAL/ABORT | 10/10 ✓* | max_rounds / accepted (gemischt) |
| S12 | DEWALT DCD796P2 | DEAL/ABORT | 10/10 ✓* | pending / max_rounds (gemischt) |
| S13 | Kärcher Schaum-Set | DEAL | 9/10 | pending_approval / accepted |
| S14 | GARDENA FLEX Schlauch | DEAL | 10/10 ✓ | pending_approval |

*S11/S12: beide Outcomes (DEAL oder ABORT) als korrekt klassifiziert

---

## Quantitative Voranalyse: Preis-Text-Diskrepanz

> **Methodik:** Automatisierte Auswertung aller 1.349 Verhandlungsrunden aus 10 Läufen × 14 Szenarien. Pro Runde wurde der tatsächliche `unit_price` des Angebots mit allen im `justification`-Text explizit genannten Euro-Preisen verglichen (Schwellenwert: Abweichung > €0,10). Preise, die erkennbar das letzte Angebot des *Gegenübers* zitieren (erwartete Referenz), wurden als Typ A klassifiziert und aus der Fehlerzählung ausgeschlossen.

| Kategorie | Wert |
|---|---|
| Analysierte Runden gesamt | 1.349 |
| Davon `[ACCEPTED]`-Systemtexte (kein echter Inhalt) | 31 |
| Geprüfte Runden | 1.318 |
| **Typ A** — Agent zitiert korrekterweise das Gegenangebot | 269 Runden (20,4 %) |
| **Typ B** — Agent nennt im Text einen anderen Preis als das eigene Angebot | **159 Runden (12,1 %)** |
| Betroffene Sessions (Szenario × Lauf) | **97 von 140 (69,3 %)** |

### Typ-B-Instanzen pro Szenario

| Szenario | Betroffene Runden | In x/10 Läufen | Ø Abweichung |
|---|---|---|---|
| S01 | 6 | 4/10 | €14,92 |
| S02 | 8 | 6/10 | €23,13 |
| S03 | 10 | 7/10 | €4,60 |
| S04 | 15 | **10/10** | €8,01 |
| S05 | 5 | 4/10 | €3,67 |
| S06 | 9 | 7/10 | €9,04 |
| S07 | 19 | 9/10 | €13,47 |
| S08 | 14 | 7/10 | €2,68 |
| S09 | 11 | 7/10 | €9,66 |
| S10 | 9 | 6/10 | €1,29 |
| S11 | 16 | 8/10 | €2,92 |
| S12 | 20 | **10/10** | €4,27 |
| S13 | 10 | 7/10 | €2,53 |
| S14 | 7 | 5/10 | €3,86 |

### Strukturelle Ursache

28 % der Typ-B-Fälle (45 von 159) treten in **Runde 1** auf. Der häufigste Auslöser: Der Agent zitiert den Listenpreis/Ausgangspreis des Szenario-Setups, nicht den tatsächlich berechneten Eröffnungspreis (z.B. S02/Lauf 1: Agent nennt „€369" als vorherige Referenz, bietet aber €310 an; Δ +€59). Die verbleibenden 72 % der Fälle treten in späteren Runden auf, meist wenn das System interne Aspirationspreise oder alternative Preisvorschläge generiert, die nicht mit dem endgültigen Angebotswert synchronisiert sind.

### Einfluss auf quantitative KPIs

Die Diskrepanz beeinflusst die **gemessenen KPIs nicht nachweisbar**:

| Metrik | Sessions mit Typ-B (n=97) | Sessions ohne Typ-B (n=43) |
|---|---|---|
| Outcome-Korrektheit | 93,8 % | 90,7 % |
| Ø ZU (nur DEAL-Sessions) | 0,309 | 0,293 |
| Constraint-Verletzungen | 8 Sessions | 6 Sessions |

Der Unterschied in der Outcome-Korrektheit (93,8 % vs. 90,7 %) ist minimal und nicht signifikant. Der ZU-Unterschied ist vernachlässigbar. Die Diskrepanz hat die **quantitativen KPIs (CSR, WAA, ZU, Outcome-Accuracy) nicht verfälscht**, da diese ausschließlich auf den strukturierten Angebotswerten basieren — nicht auf dem Justification-Text.

**Einordnung:** Das Problem ist kommunikativ, nicht metrisch. Es handelt sich um eine **Architektur-Inkonsistenz zwischen dem Textgenerierungsmodul und dem Angebotskalkulationsmodul**. Ein Gesprächspartner, der den Text liest, erhält falsche Preisinformationen; die tatsächliche Verhandlung (und damit alle KPIs) basiert korrekt auf den strukturierten Werten. Für einen autonomen Praxiseinsatz ist dies dennoch ein K.O.-Kriterium, da eine Gegenpartei den schriftlichen Verhandlungstext als verbindlich betrachten würde.

---

## Szenario S01 — Bosch GSR 18V-90 C Akkuschrauber
**Kategorie:** Wide ZOPA  
**ZOPA:** €130 – €175 | Typischer Preis: €157–€165 | Stabilität: 10/10

### Quervergleich (10 Läufe)
Alle 10 Läufe enden mit einem Deal. Die finale Preisrange liegt zwischen €157 (Lauf 1) und €165 (Läufe 4, 10), was einem breiten Bereich von €8 über die Läufe hinweg entspricht. Die Rundenanzahl schwankt: Lauf 1 benötigt 6 Runden, Lauf 4 kommt mit 4 Runden aus, Lauf 3 schließt in 2 Runden. Das demonstriert, dass Preispfad und Einigungsgeschwindigkeit von Lauf zu Lauf stark variieren, obwohl das Endergebnis stabil ist.

### Argumentationsqualität
Alle 10 Läufe zeigen dasselbe strukturelle Muster: Markenname „Bosch" wird in ca. 100% der Läufe genannt, gelegentlich mit dem Zusatz „Professional". Echter Produktkontext (Akku-Plattform, DIY- vs. Professional-Zielgruppe, Kompatibilitätsvorteil der 18V-Serie) fehlt vollständig. Die Argumentation ist in jedem Lauf nach demselben Template aufgebaut: *Danksagung → Preisnennung → Zahlungsziel als Mehrwert → Win-Win-Abschluss*. Spezifisch produktbezogene Argumente (z.B. Bosch-Marktanteil im Elektrowerkzeug-Segment, Handelsmargen für A-Marken) erscheinen in keinem der 10 Läufe.

### Konzessionslogik
Das Konzessionsmuster ist laufübergreifend inkonsistent: In Lauf 3 einigen sich die Parteien nach nur 2 Runden (Retailer eröffnet mit €164,50, Supplier akzeptiert fast sofort), in Lauf 1 sind es 6 Runden. Die Einigung wird in mehreren Läufen durch den internen `ASPIRATION_ACCEPT`-Trigger ausgelöst (Risk-Reward zu niedrig), nicht durch echte Konzessionsstrategie. Der finale Sprung des Suppliers auf den Retailer-Preis erscheint ohne verbale Vorbereitung.

### Reaktionsadäquatheit
Für ein Wide-ZOPA-Szenario ist die Einigungsrate perfekt (10/10). Die Abschluss-Kommunikation bleibt aber ein Systemartefakt: „[ACCEPTED] Risk-reward ratio too low" ist kein professioneller Abschluss-Text. In einem Praxissystem müsste dies durch eine echte Einigungsformulierung ersetzt werden.

### Gesamteindruck und besondere Auffälligkeiten
Die Varianz im Finalen Preis (€157–€165 = €8 Spanne) über 10 Läufe bei identischen Constraints ist bemerkenswert. Ein menschlicher Einkäufer würde erwarten, dass das System konsistenter um eine optimale Position konvergiert. Positiv: Keine False Agreements, keine Constraint-Verletzungen.

### Einstufung (Gesamtplausibilität)
[x] Überwiegend plausibel — kleinere Auffälligkeiten, die in der Praxis tolerierbar wären

---

## Szenario S02 — Kärcher K 5 Premium Hochdruckreiniger
**Kategorie:** Wide ZOPA  
**ZOPA:** €250 – €340 | Typischer Preis: €312–€338 | Stabilität: 10/10

### Quervergleich (10 Läufe)
Hohe Preisvarianz: Der finale Preis schwankt zwischen €312 (Lauf 1) und €338 (Lauf 2) — ein Spread von €26 bei identischen Constraints. In Lauf 10 beträgt der Finalprice €310 (nach 4 Runden). Die Supplier-Anfangsanker variieren ebenfalls stark. Die Einigungsgeschwindigkeit schwankt von 3 bis 7 Runden.

### Argumentationsqualität
Der Brand „Kärcher" wird in ca. 75% der Läufe erwähnt. Das Produkt (Premium-Hochdruckreiniger, €300+-Gerät) würde in der Praxis Argumente zur Saisonalität (Frühjahrs- und Herbstgeschäft), Sortimentsstrategie im Gartenbereich oder zum Retailer-Sell-Through erwarten lassen — solche Argumente fehlen in allen 10 Läufen vollständig. Wiederkehrendes Preis-Text-Diskrepanz-Problem: In Lauf 4 (Runde 2) nennt der Supplier „Net 75 payment terms" — ein nicht standardisiertes Zahlungsziel, das in der Praxis ungewöhnlich wäre.

### Konzessionslogik
Das Konzessionsmuster variiert laufübergreifend stark zwischen Tight-Convergence (3–4 Runden, enge Eröffnungen) und Extended-Bargaining (6–7 Runden, breitere Eröffnungen). Der bereits in Lauf 1 beobachtete Widerspruch zwischen Text-Preis und Angebot-Preis tritt in weiteren Läufen auf (Lauf 4: Supplier nennt €311,50 im Text, bietet aber €312,02 an).

### Reaktionsadäquatheit
Einigungsrate 10/10 — korrekt für Wide ZOPA. Die starke Preisvarianz (€28 Spread) ist für ein Wide-ZOPA mit €90 Spielraum tolerabel, würde in der Praxis aber ein Konsistenzproblem darstellen.

### Gesamteindruck und besondere Auffälligkeiten
Das Preis-Text-Diskrepanz-Pattern wiederholt sich über mehrere Läufe und ist damit kein Einzelfall, sondern ein strukturelles Problem des Systems.

### Einstufung (Gesamtplausibilität)
[x] Überwiegend plausibel — kleinere Auffälligkeiten, die in der Praxis tolerierbar wären

---

## Szenario S03 — Weber iGrill 3 Bluetooth Thermometer (C-Artikel)
**Kategorie:** Wide ZOPA (C-Artikel)  
**ZOPA:** €52 – €78 | Typischer Preis: €70,50–€76 | Stabilität: 10/10

### Quervergleich (10 Läufe)
Alle Läufe erzielen einen Deal. Preisspanne: €70,50 (Lauf 4) bis €76 (Lauf 3) — eine Varianz von €5,50. Interessant: Lauf 4 erzielt den niedrigsten Preis (Retailer-Vorteil), obwohl das Produkt ein typischer C-Artikel ist, für den im Einkauf oft weniger Verhandlungsaufwand betrieben wird. In Lauf 3 landet der Preis mit €76 nahe am ZOPA-Oberlimit (Supplier-Vorteil). Der Lauf-zu-Lauf-Unterschied von €5,50 bei einem €20-Produkt ist relativ betrachtet signifikant (27% der ZOPA-Breite).

### Argumentationsqualität
Weber als Marke wird in ca. 50% der Läufe explizit erwähnt. Die für einen Smart-Home-Zubehörartikel typischen Argumente (Saisonalität im Grillbereich, Cross-Selling mit Grill-Sortiment, Impulskauf vs. geplanter Kauf) fehlen durchgängig. In Lauf 3 versucht der Retailer in Runde 5, Volumen als Hebel einzusetzen (1.200 statt 1.000 Einheiten) — das ist ein plausibles Logrolling-Element, aber in einem C-Artikel-Kontext würde ein Einkäufer das bei einem saisonalen Grillartikel vorsichtiger einsetzen.

### Konzessionslogik
Laufübergreifend erkennbar: Supplier eröffnet deutlich unter dem Retailer-Preis (€55–€65 Bereich), Retailer bleibt hoch (€70–€77), beide nähern sich an. Der finale „ACCEPTED"-Sprung des Suppliers macht in allen Läufen die größte Bewegung in einer Runde — strukturell immer dasselbe Muster.

### Reaktionsadäquatheit
10/10 korrekte Outcomes. Die CSR-Verletzung in Lauf 1 (Runde 6: Zahlungsziel-Wechsel beim Akzeptieren) tritt nicht in allen Läufen auf, ist also kein systematisches Problem.

### Gesamteindruck und besondere Auffälligkeiten
Für ein breites ZOPA solide. Der Preis-Spread von €5,50 über die Läufe ist tolerierbar aber zeigt, dass die Einigung mehr durch Zufallssampling als durch konsistente Strategie zustande kommt.

### Einstufung (Gesamtplausibilität)
[x] Überwiegend plausibel — kleinere Auffälligkeiten, die in der Praxis tolerierbar wären

---

## Szenario S04 — Makita DHP486Z Schlagbohrschrauber
**Kategorie:** Narrow ZOPA  
**ZOPA:** €112 – €118 | Typischer Preis: €115,50–€118 | Stabilität: 8/10

### Quervergleich (10 Läufe)
8 von 10 Läufen erzielen das korrekte Outcome. In 2 Läufen wird `max_rounds_reached` ohne Einigung. Auffällig ist Lauf 4 mit **12 Runden** bis zur Einigung — der Supplier eröffnet bei €130 (über dem Retailer-Maximum von €118) und gibt in sehr kleinen Schritten nach (€130 → €129 → €128 → ... → €118). Das entspricht einem extremen Boulware-Stil und ist für ein €6-ZOPA-Fenster realitätsnah: Lieferanten von A-Marken-Elektrowerkzeug haben wenig Spielraum und halten ihre Preise lang.

### Argumentationsqualität
Makita wird in allen Läufen erwähnt. Die für einen Schlagbohrschrauber typischen Handwerker- und Baumarkt-Argumente (Profi-Segment, 3-Jahres-Garantie, brushless Motor-Vorteil) fehlen. In Lauf 4 sagt der Supplier in Runde 2: „€120 per unit...swift 14-day delivery" — das ist ein plausibles Paketangebot. Die Argumentation ist für ein Narrow-ZOPA-Szenario konservativer und enthaltener als in den Wide-ZOPA-Szenarien.

### Konzessionslogik
Das Muster in Lauf 4 (12 Runden, Supplier von €130 auf €118) ist ein erkennbarer Boulware-Konzeder-Stil und in der Praxis plausibel für Markenware. Die 2 Fehlläufe (max_rounds ohne Deal) könnten auftreten, wenn der Supplier zu spät auf €118 concediert. Das ist ein strukturelles Timing-Problem bei engen ZOPAs.

### Reaktionsadäquatheit
Die Fälle, in denen das System in 15 Runden keinen Deal schließt, obwohl das rechnerisch möglich wäre, sind das kritischste Problem in diesem Szenario. Ein erfahrener Verhandlungsführer würde spätestens in Runde 10 das Muster erkennen und gezielter auf den finalen Preis zusteuern.

### Gesamteindruck und besondere Auffälligkeiten
Das System hat bei Narrow-ZOPA eine erkennbare Tendenz zu unnötig langen Verhandlungen. Die 20% Fehlquote ist für ein System, das auf Deal-Abschluss ausgelegt ist, bedenklich.

### Einstufung (Gesamtplausibilität)
[x] Überwiegend plausibel — kleinere Auffälligkeiten, die in der Praxis tolerierbar wären

---

## Szenario S05 — Bosch X-LOCK Schleif- und Trennscheiben-Set
**Kategorie:** Narrow ZOPA  
**ZOPA:** €27,50 – €29 | Typischer Preis: €27,50–€29,50 | Stabilität: 7/10

### Quervergleich (10 Läufe)
Nur 7/10 Läufe korrekt. 3 Läufe enden als `max_rounds_reached`. In Lauf 4 dauert die Verhandlung **14 Runden** (Supplier eröffnet bei €33,46 — deutlich über dem ZOPA-Maximum) und endet bei €29,50 (leicht über ZOPA-Grenze, was technisch aber validiert wird). Der Supplier-Einstiegspreis variiert stark: Lauf 1 startet bei €28, Lauf 4 bei €33,46 — ein Unterschied von €5,46 bei einem Produkt mit €1,50-ZOPA. Das ist hochgradig inkonsistentes Ankerverhalten.

### Argumentationsqualität
Das „Premium"-Argument für Schleifscheiben (ein reines Verbrauchsmaterial, das nach Spezifikation ausgeschrieben wird) ist in mehreren Läufen präsent und wirkt unrealistisch. Kein Lauf referenziert die typischen Einkaufsargumente für C-Artikel: Lieferantenkonsolidierung, Rahmenverträge, Preis-pro-Einheit bei Großmengen. In Lauf 3 wird der Abschluss in 4 Runden erreicht — die Begründungen sind knapper, aber nicht inhaltlich besser.

### Konzessionslogik
Die Preisvarianz beim Supplier-Einstieg (€28–€33,46) über die Läufe ist das auffälligste Problem. Ein konsistentes System würde bei denselben Constraints mit ähnlichen Eröffnungspreisen starten. Die Varianz deutet auf eine zu hohe Temperatur-Sensitivität des Modells bei Narrow-ZOPA-Szenarien hin, was das Verhandlungsergebnis unberechenbar macht.

### Reaktionsadäquatheit
30% Fehlquote (3/10 max_rounds) ist für ein Deal-Szenario problematisch. Das System scheitert häufig, weil der Supplier zu hoch eröffnet und zu langsam concediert.

### Gesamteindruck und besondere Auffälligkeiten
Das inkonsistente Ankerverhalten (€28 vs. €33,46 als Supplier-Eröffnung bei identischem Szenario) ist das schwerwiegendste Problem in S05. Es untergräbt die Verlässlichkeit des Systems für enge Verhandlungssituationen.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S06 — GARDENA smart Water Control Set
**Kategorie:** Narrow ZOPA  
**ZOPA:** €82 – €87 | Typischer Preis: €87 | Stabilität: 6/10

### Quervergleich (10 Läufe)
Schlechteste Outcome-Stabilität aller Deal-Szenarien (6/10). 4 Läufe enden als `max_rounds_reached`. Die Opening-Preise des Retailers variieren stark zwischen den Läufen:
- Lauf 1: €87 (am Retailer-Maximum, sofortiger Deal in 2 Runden)
- Lauf 2: €87 (sofortiger Anchor am Maximum → 15 Runden trotzdem, weil Supplier über ZOPA öffnet bei €91)
- Lauf 3: €85 (moderat unter Maximum → konvergiert zu €87 nach 15 Runden)
- Lauf 7: **€78** (unter dem ZOPA-Minimum → zwingt 15-Runden-Verhandlung)

In Lauf 7 eröffnet der Retailer mit €78 — einer Position **außerhalb der ZOPA** (unterhalb des eigenen Maximums). Das führt zu 15 Runden Verhandlung und einem Finalprice von €87. Das System schließt letztendlich den richtigen Deal, aber der Weg dorthin ist unnötig lang.

### Argumentationsqualität
Alle Läufe zeigen generische Argumentationsmuster. In Lauf 2 formuliert der Supplier in Runde 2: „premium quality and reliability of the product, ensuring long-term value for your customers" — für eine Smart-Home-Gartenbewässerung ein etwas überhöhtes Qualitätsargument. GARDENA-spezifische Argumente (Konnektivität, App-Integration, Smart-Home-Ecosystem) fehlen in allen Läufen.

### Konzessionslogik
Das fundamentale Problem: In Lauf 1 eröffnet der Retailer mit seinem Maximalpreis (€87 = obere ZOPA-Grenze = Supplier-Minimum). Das ist taktisch suboptimal — ein echter Einkäufer würde nie mit seinem absoluten Maximum beginnen. Dieses Muster wiederholt sich in mehreren Läufen. In Lauf 7 pendelt sich das System nach 15 Runden auf €87 ein — das korrekte Ergebnis, aber auf einem hochgradig ineffizienten Weg.

### Reaktionsadäquatheit
Die 40% Fehlquote (4/10 max_rounds, also kein Deal trotz vorhandener ZOPA) ist das kritischste Problem in S06. In 4 Läufen scheitert das System daran, einen Deal in einer Situation zu erzielen, in der der Spielraum zwar eng ist (€5), aber eindeutig vorhanden.

### Gesamteindruck und besondere Auffälligkeiten
S06 offenbart eine strukturelle Schwäche des Systems bei sehr engen ZOPAs: Das Sampling des Eröffnungspreises ist zu zufällig, sodass gelegentlich mit Preisen außerhalb der ZOPA eröffnet wird. Zudem versagt die Abschluss-Mechanik in 40% der Fälle. Für ein Praxissystem wäre das inakzeptabel.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S07 — STIHL MSA 140 C-B Kettensäge
**Kategorie:** No ZOPA (Obvious)  
**Stabilität:** 10/10 (alle Läufe korrekt kein Deal)

### Quervergleich (10 Läufe)
Alle Läufe enden als `max_rounds_reached` (ein Lauf mit `failed`). Die Finalprice des Retailers variiert zwischen €264 und €278 — also +€14 Bewegung des Retailers über 10 Läufe. Der Supplier bleibt in allen Läufen nahezu konstant bei €310–€325. Die Gap-Größe am Ende variiert von €32 (Lauf mit €278 Retailer) bis zu €45+ (Lauf mit €265 Retailer).

### Argumentationsqualität
**Kritischer Befund, bestätigt über alle Läufe:** In keinem einzigen der 10 Läufe verwendet ein Agent Formulierungen wie „keine Einigung möglich", „wir müssen die Verhandlung beenden", „der Gap ist unüberbrückbar" oder ähnliche Walk-Away-Sprache. Die Agenten formulieren in Runde 15 dieselbe kooperative, hoffnungsvolle Sprache wie in Runde 1: „let's work together to finalize this win-win solution". Das ist über alle Läufe hinweg konsistent und stellt die gravierendste kommunikative Schwäche des Systems dar.

### Konzessionslogik
Der Retailer concediert in allen Läufen insgesamt nur €6–€16 über 15 Runden. Der Supplier verharrt nahezu vollständig bei seinem Minimum. Die `STAGNATION DETECTED`-Logik feuert intern in Runde 9–10, aber die externe Kommunikation ändert sich nicht. Das Muster ist laufübergreifend identisch: internes Signal → keine externe Reaktion.

### Reaktionsadäquatheit
Das System erkennt korrekt, dass kein Deal möglich ist (0 False Agreements in 10 Läufen). Die **Kommunikation des Abbruchs** ist jedoch in keinem einzigen Lauf professionell. Ein echter Verhandlungsführer würde nach 5–6 Runden ohne Bewegung die Verhandlung formal und sachlich beenden — mit Offenhaltung zukünftiger Zusammenarbeit, aber klarer Aussage über die aktuelle Situation.

### Gesamteindruck und besondere Auffälligkeiten
Das Muster ist über alle 10 Läufe 100% konsistent: korrekte interne Erkennung, fehlende externe Walk-Away-Kommunikation. Das ist kein Einzelfall-Bug, sondern ein systemisches Design-Defizit.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S08 — DEWALT TSTAK VI Werkzeugkoffer
**Kategorie:** No ZOPA (Obvious)  
**Stabilität:** 10/10

### Quervergleich (10 Läufe)
**Auffälliges Muster:** Der Retailer bleibt in nahezu **allen** 10 Läufen exakt bei €36. Die Supplier-Bewegung variiert zwischen €46–€50 als Finalprice (von Startpunkten bei €50–€55). In einem Lauf kommt ein `failed`-Outcome vor (statt `max_rounds_reached`), was eine inkonsistente Abbruch-Terminologie bedeutet.

### Argumentationsqualität
Der Retailer ist in diesem Szenario über alle Läufe hinweg der statischste Agent im gesamten Test: keine einzige Cent-Bewegung über 15 Runden in mehreren Läufen. Das entspricht einem Boulware-Stil, ist aber für eine 15-runden-Verhandlung kommunikativ unrealistisch — ein echter Einkäufer würde zumindest sprachliche Signale senden, dass er seine Position revidiert hat oder das Gespräch beendet.

In Lauf 3, Runde 8, sagt der Supplier: „€36.00 is certainly competitive, but it doesn't align with the premium quality and durability the DEWALT TSTAK VI offers" — das ist eines der wenigen produktbezogenen Qualitätsargumente über alle Läufe, aber es bleibt eine Standardaussage.

### Konzessionslogik
Supplier concediert langsam von €50+ auf €46–€48 (je nach Lauf). Die Konzessionsschritte sind jeweils €1, was einem linearen Muster entspricht. Der Retailer bewegt sich gar nicht oder minimal. Das Muster ist laufübergreifend konsistent.

### Reaktionsadäquatheit
Gleiche strukturelle Schwäche wie S07: kein aktiver kommunizierter Walk-Away. Das STAGNATION-Signal feuert intern ab Runde 9, aber die externe Kommunikation ändert sich nicht. In einem der Läufe tritt ein `failed`-Outcome auf — dieses Verhalten ist inkonsistent zum `max_rounds_reached`-Muster der anderen Läufe.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S09 — Makita DGA513Z Winkelschleifer
**Kategorie:** No ZOPA (Near-Miss)  
**Stabilität:** 10/10

### Quervergleich (10 Läufe)
Alle 10 Läufe enden als `max_rounds_reached`. Der Finalprice des Retailers ist in **allen** Läufen exakt €126. Der Supplier schwankt zwischen €128 und €128,50. Der Gap am Ende beträgt konsistent €2–€2,50. Das ist das konsistenteste Preis-Bild aller 14 Szenarien.

### Argumentationsqualität
Das Preis-Text-Diskrepanz-Problem tritt in diesem Szenario konsistent auf: In Lauf 1, Runde 15 sagt der Retailer „we're prepared to meet at €127.00" — bietet aber €126. Ähnliche Diskrepanzen sind in weiteren Läufen erkennbar (Lauf 2: Text nennt €126,50, Angebot ist €126). Das Muster ist reproduzierbar.

### Konzessionslogik
Beide Parteien verharren über 15 Runden nahezu vollständig auf ihren Positionen. Der Supplier macht in Runde 14 eine minimale Bewegung (€128 → €128,50 — also eine Erhöhung statt einer Senkung). Diese logisch inkonsistente Bewegung in die falsche Richtung tritt in mehreren Läufen auf. In der Praxis würde das bedeuten, dass kurz vor dem Abschluss der Preis steigt — ein klares Signal fehlerhafter Konzessionslogik.

### Reaktionsadäquatheit
Das Muster ist über alle 10 Läufe identisch: €2-Gap, 15 Runden, kein aktiver Walk-Away. In einem echten Verhandlungsgespräch würde eine erfahrene Verhandlungsführung nach 5–6 Runden auf demselben Niveau entweder den letzten Schritt machen oder explizit abbrechen. Das System tut beides nicht.

### Gesamteindruck und besondere Auffälligkeiten
Die Preis-Erhöhung des Suppliers in den letzten Runden (€128 → €128,50) ist das unplausibleste Einzelverhalten im gesamten Datensatz. Es tritt reproduzierbar in mehreren Läufen auf.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S10 — Kärcher T 7 Plus Flächenreiniger
**Kategorie:** No ZOPA (Near-Miss)  
**Stabilität:** 10/10

### Quervergleich (10 Läufe)
Alle 10 Läufe: `max_rounds_reached`. Retailer konstant bei €51, Supplier plateau bei €53–€54. Das Preis-Text-Diskrepanz-Problem tritt auch hier auf: In Lauf 1, Runde 3 nennt der Retailer €52,50 im Text, das Angebot ist €50,97. In anderen Läufen sind vergleichbare Diskrepanzen beobachtbar.

### Argumentationsqualität
Kärcher-Brand wird erwähnt, Produktkontext (Flächenreiniger als Zubehör für Kärcher-Reiniger, Cross-Selling-Argument) fehlt. In Lauf 3, Runde 6 formuliert der Supplier einen der wenigen volumenbezogenen Hebel: „if you're able to increase your order to 300 units" — aber die Menge war bereits 300 Einheiten im Angebot, was den Versuch als inkonsistent entlarvt.

### Konzessionslogik
Ähnliches Muster wie S09: Supplier beginnt bei €54,50, fällt auf €53, und stagniert dann. In einem Lauf versucht der Supplier in Runde 6, Volumen als Hebel einzusetzen, aber das Volumen war bereits im Angebot enthalten — das ist ein inhaltlicher Fehler in der Argumentation.

### Reaktionsadäquatheit
Das STAGNATION-Signal feuert ab Runde 9–11 intern, aber die Kommunikation ändert sich nicht. Walk-Away-Sprache: in keinem der 10 Läufe vorhanden. Das ist identisch mit S07, S08, S09.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S11 — Bosch 18V Akku-Set (MOQ-Konflikt)
**Kategorie:** Asymmetrische Constraints  
**Stabilität:** 10/10* (gemischt: max_rounds + accepted — beide korrekt)

### Quervergleich (10 Läufe)
Die Outcomes variieren: 5 Läufe enden als `max_rounds_reached`, 5 als `accepted`. In Lauf 3 wird das Abkommen in Runde 6 bei €97 geschlossen — dabei wird aber ein Constraint verletzt: das Volume beträgt 300 Einheiten, obwohl das Supplier-Minimum 500 Einheiten ist. Das ist technisch ein False Agreement, das als korrekt klassifiziert wurde.

### Argumentationsqualität
**Kritischer Befund:** In **keinem** der 10 Läufe verwendet ein Agent explizit die Begriffe „Mindestbestellmenge", „minimum order quantity", „MOQ" oder nennt den Volumenkonflikt (300 vs. 500 Einheiten) direkt als Problem. Die Volumenasymmetrie ist strukturell in den Angeboten erkennbar (Retailer bietet 300, Supplier antwortet mit 500–600), aber wird nie als solche thematisiert.

### Konzessionslogik
Das System verhandelt über €0,10–€1,50 Preisdifferenzen, während der eigentliche Deadlock eine 200-Einheiten-Volumendifferenz ist. In Lauf 4 nähern sich beide Parteien so stark an (Preisgap <€0,10 in Runde 13), dass eine Einigung naheliegen würde — aber das System schließt sie trotzdem nicht, weil das Volume-Constraint nicht adressiert wird.

### Reaktionsadäquatheit
Das System erkennt weder intern noch extern, dass das Kernproblem struktureller Natur ist. Es gibt keinen einzigen Satz über alle 10 Läufe hinweg, der den MOQ-Konflikt explizit addressiert. Das ist die konsistenteste und schwerwiegendste thematische Schwäche im gesamten Datensatz.

### Gesamteindruck und besondere Auffälligkeiten
Die Constraint-Verletzung in Lauf 3 (False Agreement bei Volumen unter Supplier-Minimum) ist ein ernstes Problem, das die Outcome-Accuracy-Statistik verzerrt. In einem Praxissystem würde ein solches Abkommen scheitern.

### Einstufung (Gesamtplausibilität)
[x] Eingeschränkt plausibel — erkennbare Schwächen, die einen Praxiseinsatz erschweren würden

---

## Szenario S12 — DEWALT DCD796P2 (Lieferzeit-Konflikt)
**Kategorie:** Asymmetrische Constraints  
**Stabilität:** 10/10* (gemischt: pending + max_rounds — beide korrekt)

### Quervergleich (10 Läufe)
Die Outcomes variieren stark: Einige Läufe enden schnell mit einem Deal (4–5 Runden), andere laufen bis zu 15 Runden. In Lauf 3 wird 15 Runden verhandelt ohne Deal; in Lauf 4 wird der Deal bei €140 in 7 Runden geschlossen. Die Preisvarianz ist enorm: €140 (Lauf 4) bis €162 (Lauf 3).

**Lieferzeit-Konflikt-Befund:**
- Läufe mit schneller Einigung (Lauf 1, 4): Beide Parteien einigen sich in Runde 1–2 auf 10 Tage Lieferzeit → kein echter Konflikt entsteht
- Läufe mit langem Verlauf (Lauf 3, 5): Supplier bietet 15 Tage Lieferzeit, Retailer will 10 Tage → persistenter Konflikt
- In keinem der langen Läufe wird der Lieferzeitkonflikt **explizit benannt** als Kernproblem

### Argumentationsqualität
In Lauf 2, Runde 2 sagt der Supplier: „swift 10-day delivery and extended Net 60 payment terms" — das ist der einzige Lauf, in dem der Supplier von sich aus 10 Tage anbietet und damit den Konflikt vermeidet. In anderen Läufen bietet er 15 Tage, ohne zu erklären, warum er die 10-Tage-Anforderung nicht erfüllen kann.

### Konzessionslogik
In langen Verhandlungsläufen (15 Runden) concediert der Supplier beim Preis von €173 auf €164 — eine Konzession von €9. Das entspricht einer echten, substantiellen Preisbewegung. Allerdings bleibt das Lieferzeitproblem ungelöst, was die Preiskonzession irrelevant macht.

### Reaktionsadäquatheit
Das System löst in einigen Läufen den Lieferzeitkonflikt implizit (beide einigen sich auf 10 Tage ohne Diskussion), in anderen ignoriert es ihn. Das ist inkonsistent und vom Zufallsprinzip abhängig.

### Einstufung (Gesamtplausibilität)
[x] Überwiegend plausibel — kleinere Auffälligkeiten, die in der Praxis tolerierbar wären

---

## Szenario S13 — Kärcher Schaum-Set
**Kategorie:** Volume Leverage  
**ZOPA:** €18 – €28 | Stabilität: 9/10

### Quervergleich (10 Läufe)
9/10 Läufe korrekt. Der Volumenhebel wird in **allen** Läufen vom Retailer eingesetzt — das ist der konsistenteste und plausibilste Taktikzug des gesamten Datensatzes. Timing und Ausmaß variieren:
- Lauf 1: In Runde 5 (von 2.000 auf 2.500 Einheiten)
- Lauf 2: Über mehrere Runden (2.000 → 3.000+ Einheiten)
- Lauf 6: Aggressive Eskalation über 15 Runden (bis zu 4.500 Einheiten)
- Lauf 10: Minimale Eskalation (1.500 → 1.600 Einheiten)

Die Varianz im Volumeneinsatz ist groß (4.500 vs. 1.600 Einheiten als Maximum), aber der Grundmechanismus ist überall erkennbar.

### Argumentationsqualität
In allen Läufen begründet der Retailer den Volumenanstieg mit Effizienz- und Cash-Flow-Argumenten. Der Supplier reagiert in der Mehrzahl der Läufe mit einer Preissenkung als Reaktion auf das erhöhte Volumen — das ist plausibles Verhandlungsverhalten. Das Kärcher-Schaum-Set ist ein Einstiegsprodukt, für das das Mengenargument besonders realistisch ist.

### Konzessionslogik
In Lauf 6, wo der Retailer auf 4.500 Einheiten eskaliert, stellt sich die Frage: Wäre eine Abnahme von 4.500 Einheiten eines Schaumsets für einen Retailer realistisch? Das erscheint für ein einzelnes Produkt überdimensioniert und würde in der Praxis Lager- und Liquiditätsfragen aufwerfen. Solche Plausibilitäts-Überlegungen macht das System nicht.

### Reaktionsadäquatheit
Eines der stärksten Szenarien: Der Volumenhebel wird konsistent, richtig eingesetzt und korrekt gespiegelt. Der eine Fehlläufe (`max_rounds_reached`) ist akzeptabel.

### Gesamteindruck und besondere Auffälligkeiten
Bestes Szenario im gesamten Test. Der Mechanismus funktioniert. Die unrealistische Volumen-Eskalation in einem Lauf (4.500 Einheiten) ist der einzige nennenswerte Schwachpunkt.

### Einstufung (Gesamtplausibilität)
[x] Hoch plausibel — entspricht professioneller B2B-Verhandlungsführung

---

## Szenario S14 — GARDENA Comfort FLEX Schlauch 30m
**Kategorie:** Volume Leverage  
**ZOPA:** €30 – €42 | Stabilität: 10/10

### Quervergleich (10 Läufe)
Alle 10 Läufe korrekt. Die Verhandlungsdauer schwankt von 3 (Lauf 4, 9) bis 11 Runden (Lauf 3). Finale Preise: €32,65 (Lauf 2, niedrigster Wert — Retailer-Vorteil) bis €38,25 (Lauf 4, höchster Wert — Supplier-Vorteil). Eine Spanne von €5,60, was bei einem €12-ZOPA ca. 47% der ZOPA-Breite ausmacht.

### Argumentationsqualität
GARDENA wird in allen Läufen erwähnt. Das Volumen (1.000 Einheiten in den meisten Läufen) wird als Hebel eingesetzt, aber weniger aggressiv als in S13. In Lauf 9 steigert der Retailer das Volumen auf 1.500 Einheiten — das ist für einen 30m-Gartenschlauch eine realistische Saisonbestellung. Das Preis-Text-Diskrepanz-Problem tritt in Lauf 1 auf (Text: €37,10, Angebot: €36,75) und ist auch in anderen Läufen beobachtbar.

### Konzessionslogik
Die Varianz im Finalprice (€5,60 Spread) zeigt, dass die Einigungsposition stark davon abhängt, wer zuerst „blinkt". In Lauf 2 concediert der Retailer früh und deutlich; in Lauf 4 hält der Retailer länger durch. Das entspricht einem realistischen Verhandlungsmuster, bei dem Stärke und Timing den Outcome beeinflussen.

### Reaktionsadäquatheit
10/10 korrekte Outcomes. Der Volumenhebel wird konsistent eingesetzt. Kein Szenario zeigt eine False Agreement oder einen unplausiblen Abbruch.

### Einstufung (Gesamtplausibilität)
[x] Überwiegend plausibel — kleinere Auffälligkeiten, die in der Praxis tolerierbar wären

---

---

# Abschließende Querschnittsbeurteilung (szenarioübergreifend, 10 Läufe)

## 1. Stärken des Systems

Über alle 14 Szenarien und 10 Läufe hinweg zeigt das System in drei Bereichen konsistente Stärken:

**Outcome-Zuverlässigkeit:** Die Outcome-Accuracy ist herausragend. In 12 von 14 Szenarien wird das korrekte Ergebnis (Deal oder kein Deal) in 90–100% der Läufe erzielt. Kein einziger Fall von False Agreement in den No-ZOPA-Szenarien (S07–S10), was die wichtigste Sicherheitseigenschaft für ein autonomes Verhandlungssystem ist.

**Mehrdimensionale Verhandlungsführung:** Das System versteht und nutzt konsequent Zahlungsziele, Lieferzeiten und Volumen als Verhandlungsdimensionen. In den Volume-Leverage-Szenarien (S13, S14) wird der Volumenhebel in allen 10 Läufen korrekt eingesetzt. Das ist das einzige Muster, das sowohl konsistent als auch inhaltlich plausibel ist.

**Deeskalative Grundhaltung:** Über alle Läufe hinweg gibt es keine einzige Eskalation, keinen Vorwurf, keine aggressive Formulierung. Das System verhandelt stets kooperativ, was für automatisierte Geschäftskommunikation eine wichtige Grundeigenschaft ist.

## 2. Systematische Schwächen

Drei strukturelle Muster treten laufübergreifend konsistent auf:

**a) Preis-Text-Diskrepanz (quantifiziert: 159 Runden / 12,1 % aller geprüften Runden / 97 von 140 Sessions betroffen):** In 12,1 % aller 1.318 geprüften Verhandlungsrunden enthält der `justification`-Text einen anderen Preis als das tatsächlich übermittelte Angebot — nach Abzug erwarteter Gegner-Referenzen (Typ A). Das Muster tritt in 13 von 14 Szenarien auf; S04 und S12 sind in 10/10 Läufen betroffen. Die Ursache ist eine Architektur-Inkonsistenz: Das Textgenerierungsmodul greift auf interne Aspirationspreise oder den Szenario-Ausgangspreis zurück, während das Angebotsmodul einen separat berechneten Wert liefert. Die gemessenen quantitativen KPIs (CSR, WAA, ZU, Outcome-Accuracy) sind nicht betroffen — diese basieren ausschließlich auf den strukturierten Angebotswerten. Für einen Praxiseinsatz ist die Diskrepanz dennoch ein K.O.-Kriterium: Eine Gegenpartei, die den schriftlichen Verhandlungstext als verbindlich betrachtet, würde systematisch falsche Preisinformationen erhalten.

**b) Fehlende Walk-Away-Kommunikation (S07, S08, S09, S10 — alle 10 Läufe):** Das System erkennt intern (über das `STAGNATION DETECTED`-Signal) zuverlässig, wenn ein Deal nicht erreichbar ist. Es übersetzt dieses Signal aber nie in eine professionelle externe Kommunikation. Über alle 40 No-ZOPA-Läufe (4 Szenarien × 10 Läufe) gibt es nicht eine einzige Formulierung, die das Gespräch aktiv und professionell beendet. Jede Verhandlung läuft bis zur Rundenbegrenzung — das ist weder realistisch noch effizient.

**c) Generische, kontextfreie Argumentation (alle Szenarien, alle Läufe):** Die Justification-Texte sind produkt- und kontextunabhängig. Kein Agent referenziert jemals: Saisonalität, Wettbewerberpreise, Sortimentsstrategie, spezifische Produkteigenschaften, Handelsmargenkalkulation oder Marktbedingungen. Alle 140 Verhandlungen (14 Szenarien × 10 Läufe) klingen strukturell identisch — unabhängig davon, ob es um einen €20-C-Artikel oder eine €300-Markenpumpe geht.

## 3. Kritischste Szenarien

**S11 (Bosch 18V Akku-Set, MOQ-Konflikt)** ist das schwerwiegendste Szenario: Über alle 10 Läufe wird der eigentliche Konfliktgrund (Volumendifferenz 300 vs. 500 Einheiten) nie explizit thematisiert. Das System verhandelt weiter am Preis, während das eigentliche Problem struktureller Natur ist. In Lauf 3 entsteht sogar ein technisches False Agreement (Einigung bei Volumen unter Supplier-Minimum). Das zeigt, dass das System keine Dimension-Shift-Kompetenz besitzt: Wenn der Engpass nicht beim Preis liegt, kann es die richtige Verhandlungsdimension nicht identifizieren.

**S06 (GARDENA Water Control, Narrow ZOPA)** zeigt mit 40% Fehlquote die schlechteste Zuverlässigkeit aller Deal-Szenarien. Das inkonsistente Ankerverhalten (Retailer eröffnet in Lauf 7 unter dem eigenen Maximum) und die daraus resultierende Ineffizienz (15 Runden für einen Deal, der in 2 Runden möglich wäre) sind für einen Praxiseinsatz inakzeptabel.

**S05 (Bosch X-LOCK, Narrow ZOPA)** zeigt das inkonsistenteste Ankerverhalten: Supplier-Eröffnungspreise variieren von €28 bis €33,46 bei identischen Constraints. Diese Variance führt zu 30% Fehlquote und macht das System für enge ZOPA-Situationen unzuverlässig.

## 4. Praxiseignung

Basierend auf der Analyse aller 10 Läufe ergibt sich folgendes Eignungsprofil:

**Heute produktionsreif (mit Einschränkungen):** Wide-ZOPA-Szenarien (S01–S03) und Volume-Leverage-Szenarien (S13, S14). Hier liefert das System in 90–100% der Läufe korrekte Ergebnisse und plausibles Verhalten. Für Tail-Spend-Standardartikel ohne enge Preiskorridore könnte es als unterstützendes Tool eingesetzt werden.

**Bedingt einsetzbar:** Narrow-ZOPA-Szenarien (S04, S05) mit 70–80% Korrektheit. Die Fehlquote ist für einen autonomen Einsatz zu hoch; ein Fallback auf menschliche Entscheidung bei Stagnation wäre notwendig.

**Nicht einsetzbar ohne grundlegende Verbesserungen:**
- No-ZOPA-Szenarien (S07–S10): Der fehlende aktive Walk-Away ist ein K.O.-Kriterium für eine professionelle Kommunikationslösung. Ein System, das nach 15 Runden hoffnungsloser Verhandlung immer noch „win-win solutions" anbietet, würde im realen Einkauf das Vertrauen der Geschäftspartner zerstören.
- Asymmetrische Constraint-Szenarien (S11): Das System kann nicht erkennen, wenn der Engpass nicht beim Preis liegt.

**Drei zwingend zu behebende Punkte vor Praxiseinsatz:**
1. **Preis-Text-Konsistenz** sicherstellen: Justification-Preis und Angebotspreis müssen identisch sein — architektonische Lösung erforderlich.
2. **Aktive Walk-Away-Kommunikation** implementieren: Wenn `STAGNATION DETECTED` intern feuert, muss eine professionelle Gesprächsbeendigung nach außen kommuniziert werden.
3. **Dimension-Aware Negotiation** für Constraint-Konflikte: Das System muss erkennen, wenn der Engpass nicht im Preis liegt, und die Verhandlung auf die richtige Dimension lenken.

---

*Evaluation erstellt durch: Claude (Anthropic) im Auftrag von Tarnbir Singh, DHBW Mannheim / SAP SE | April 2026*  
*Analysebasis: 10 vollständige Evaluationsläufe, 14 Szenarien, 140 Verhandlungssessions, ca. 1.200 Verhandlungsrunden*