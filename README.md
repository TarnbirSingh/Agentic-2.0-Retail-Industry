# TradeBridge 2.0 — Agentenbasiertes B2B-Verhandlungssystem

> **Bachelorarbeit – Praxisphase 5**  
> Thema: _Einsatz von KI-Agenten für autonome B2B-Preisverhandlungen im Einzelhandel_  
> Backend: Python · FastAPI · SAP AI Core (GPT-4o)  
> Frontend: React · TypeScript · Vite

---

## Überblick

TradeBridge 2.0 ist ein Multi-Agent-System, das B2B-Verhandlungen zwischen Einzelhändlern und Lieferanten automatisiert. Retailer-Agenten und Supplier-Agenten handeln dabei autonom über Preis, Menge, Lieferfenster und Zahlungsbedingungen – gesteuert von strategischen Entscheidungsmodellen (Boulware, Linear, Conceder) und einem multi-attributiven Utility-Modell.

### Kernfunktionen
- **Retailer-Request-Flow**: Retailer stellt Anfrage → System findet passende Supplier → Angebotsrunde startet
- **Supplier-Proactive-Flow**: Supplier veröffentlicht Angebot → System leitet zu geeigneten Retailern weiter
- **Strategische Agenten**: 5-Phasen ReAct-inspirierter Entscheidungsfluss mit konfigurierbarem Konzessionsverhalten
- **Live-Verhandlungsmonitoring**: WebSocket-basiertes Echtzeit-Frontend mit Agent-Reasoning-Panel
- **KPI-Evaluation**: Vollständige Metrik-Berechnung (ZOPA-Utilization, Utility-Balance, Pareto-Effizienz)

---

## Projektstruktur

```
TradeBridge-2.0/
│
├── api/                         # FastAPI Backend
│   ├── main.py                  # App-Einstiegspunkt, CORS, Router
│   └── live_negotiation_endpoints.py  # REST + WebSocket Endpunkte
│
├── agents/                      # KI-Agenten
│   ├── simple_agent.py          # Hauptagent (5-Phasen ReAct-Flow)
│   ├── request_agent.py         # Request-Matching & Routing
│   └── strategy.py              # Konzessionsmuster (Boulware/Linear/Conceder)
│
├── orchestration/
│   └── simple_orchestrator.py   # Verhandlungssteuerung, ZOPA-Analyse
│
├── models/                      # Pydantic-Datenmodelle
│   ├── negotiation_models.py    # NegotiationSession, NegotiationResult, Offer
│   ├── constraints.py           # PartyLimits, Constraint-Konvertierung
│   ├── utility.py               # Multi-Attribut-Utility-Berechnung
│   └── agent_reasoning.py       # ReasoningStep, AgentThought-Modelle
│
├── llm/
│   └── ai_core_client.py        # SAP AI Core Client (LangChain-Wrapper)
│
├── evaluation/
│   └── kpi_tracker.py           # KPI-Metriken & Batch-Evaluation
│
├── config/
│   └── settings.py              # Zentrale Konfiguration (Pydantic BaseSettings)
│
├── data/
│   ├── products_catalog.json    # Produktkatalog
│   └── partners_directory.json  # Retailer- & Supplier-Verzeichnis
│
├── frontend/                    # React-Frontend (Vite)
│   └── src/
│       ├── pages/               # RetailerDashboard, SupplierDashboard
│       ├── components/          # LiveNegotiationView, AgentReasoningPanel, …
│       └── lib/                 # API-Client, Types, Hooks
│
└── tests/                       # Integrationstests
    ├── test_new_flow.py          # API-Integrationstests (requires running server)
    └── test_strategic_agent.py  # Unit-Tests für Strategie & Utility
```

---

## Setup & Start

### Voraussetzungen
- Python 3.11+
- Node.js 18+ / npm
- SAP AI Core Account **oder** OpenAI API Key (Fallback)

### 1. Python-Umgebung

```bash
# Virtuelle Umgebung erstellen
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Abhängigkeiten installieren
pip install -r requirements.txt
```

### 2. Umgebungsvariablen

```bash
cp .env.example .env
# .env mit SAP AI Core Credentials befüllen:
# AICORE_CLIENT_ID, AICORE_CLIENT_SECRET, AICORE_BASE_URL, etc.
# ODER: OPENAI_API_KEY für lokalen Fallback
```

### 3. Backend starten

```bash
# Aus dem Projektroot:
uvicorn api.main:app --reload --port 8000
```

API ist dann erreichbar unter: `http://localhost:8000`  
Swagger UI: `http://localhost:8000/docs`

### 4. Frontend starten

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

---

## API-Endpunkte (Übersicht)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/health` | Health Check |
| `POST` | `/api/retailer/request` | Retailer stellt neue Kaufanfrage |
| `GET` | `/api/retailer/{id}/requests` | Offene Anfragen eines Retailers |
| `POST` | `/api/supplier/offer` | Supplier erstellt Angebot |
| `GET` | `/api/supplier/{id}/matches` | Passende Retailer-Anfragen |
| `POST` | `/api/negotiation/start` | Verhandlungssession starten |
| `GET` | `/api/negotiation/{id}/status` | Session-Status abfragen |
| `WS` | `/ws/negotiation/{id}` | WebSocket: Live-Updates |

---

## Agenten-Architektur

### 5-Phasen ReAct-Flow (`simple_agent.py`)

```
Phase 1: PERCEIVE    → Marktkontext & Verhandlungshistorie analysieren
Phase 2: REASON      → Utility berechnen, ZOPA-Gap bewerten
Phase 3: STRATEGIZE  → Konzessionsstrategie wählen (Boulware/Linear/Conceder)
Phase 4: GENERATE    → LLM erstellt Angebotstext & Begründung
Phase 5: ACT         → Angebot senden oder Deal akzeptieren
```

### Konzessionsmuster

| Muster | Beschreibung | Anwendungsfall |
|--------|-------------|----------------|
| `BOULWARE` | Langsame Konzessionen, hält Position lange | Starke Verhandlungsposition |
| `LINEAR` | Gleichmäßige Konzessionen | Ausgewogene Verhandlung |
| `CONCEDER` | Schnelle Konzessionen zum Kompromiss | Zeitkritische Deals |

---

## KPI-Metriken (Evaluation)

Die Klasse `NegotiationKPITracker` (`evaluation/kpi_tracker.py`) berechnet:

| Metrik | Beschreibung |
|--------|-------------|
| **Konvergenzrate** | Anteil Sessions mit erfolgreichem Deal |
| **Rounds-Effizienz** | `1 - (tatsächliche_runden / max_runden)` |
| **ZOPA-Utilization** | Position des Finanzpreises im ZOPA (0=Retailer-opt., 1=Supplier-opt.) |
| **Combined Utility** | Summe der Utility-Scores beider Parteien (Pareto-Maß) |
| **Utility-Balance** | `|supplier_utility - retailer_utility|` (0=perfekt fair) |
| **Preis-Bewegung** | Absolute Preisveränderung über die Verhandlung |

```python
from evaluation.kpi_tracker import quick_evaluate

kpi = quick_evaluate(session, result, save_to="kpi_results.json")
# → Druckt strukturierten Report, speichert JSON
```

---

## Technologie-Stack

| Schicht | Technologie |
|---------|-------------|
| LLM | SAP AI Core (GPT-4o) via `generative-ai-hub-sdk` |
| LLM Fallback | OpenAI via `langchain-openai` |
| LLM Framework | LangChain 0.3 |
| Backend | FastAPI + Uvicorn |
| Datenmodelle | Pydantic v2 |
| Konfiguration | `pydantic-settings` (liest `.env`) |
| Frontend | React 18 + TypeScript + Vite |
| Echtzeit | WebSocket (native FastAPI) |
| Tests | pytest + httpx |

---

## Tests ausführen

```bash
# Unit-Tests (kein laufender Server nötig)
.venv/bin/python tests/test_strategic_agent.py

# Integrationstests (Server muss laufen: uvicorn api.main:app)
.venv/bin/python tests/test_new_flow.py
```

---

## Konfiguration

Alle Werte aus `.env` werden via `config/settings.py` (`AICoreSettings`) geladen.  
Kein anderes Modul liest `os.environ` direkt.

| Variable | Beschreibung |
|----------|-------------|
| `AICORE_CLIENT_ID` | SAP AI Core OAuth2 Client ID |
| `AICORE_CLIENT_SECRET` | SAP AI Core OAuth2 Client Secret |
| `AICORE_BASE_URL` | SAP AI Core API Base URL |
| `AICORE_AUTH_URL` | SAP AI Core Token-Endpunkt |
| `AICORE_RESOURCE_GROUP` | Resource Group (default: `default`) |
| `AICORE_DEPLOYMENT_ID` | Deployment ID des GPT-4o Modells |
| `AICORE_MODEL_NAME` | Modellname (default: `gpt-4o`) |
| `OPENAI_API_KEY` | Fallback für lokale Entwicklung |
| `LOG_LEVEL` | Logging-Level (default: `INFO`) |

---

## Lizenz / Hinweis

Dieses Projekt wurde im Rahmen einer Bachelorarbeit an der SAP SE entwickelt.  
Alle Daten sind synthetisch generiert und enthalten keine realen Geschäftsdaten.