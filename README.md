# PGx·Guardian

**Real-time Pharmacogenomics Clinical Decision Support Voice Agent**

> Built for the [Gemini Live Agent Challenge](https://geminiliveagentchallenge.devpost.com/) · March 2026

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Cloud%20Run-teal)](https://pgx-guardian-333275264485.europe-west1.run.app)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-1.26.0-blue)](https://google.github.io/adk-docs/)
[![Gemini](https://img.shields.io/badge/Gemini-2.5%20Flash%20Native%20Audio-orange)](https://ai.google.dev/)

---

## What It Does

PGx-Guardian is a voice-first clinical decision support system that helps clinicians identify dangerous **drug-gene** and **drug-drug interactions** in real time.

A clinician speaks naturally:
> *"CYP2D6 star 4 star 4, CYP2C19 star 2 star 2. Patient is on clopidogrel, codeine, omeprazole, and fluoxetine."*

PGx-Guardian responds with a complete pharmacogenomics safety report — spoken aloud and displayed visually — within seconds.

---

## Key Features

- 🎙️ **Live voice interface** — real-time bidirectional audio via Gemini Native Audio
- 🧬 **19-gene CPIC panel** — CYP2D6, CYP2C19, CYP2C9, DPYD, TPMT, HLA-B/A, G6PD, RYR1, VKORC1, and more
- 💊 **222,000+ drug-drug interaction pairs** from DDInter
- 🔬 **Genetically-escalated DDI scoring** — baseline interactions escalated when pharmacogenomics confirms risk
- 🗣️ **Conversational follow-up** — ask "why is this dangerous?" without re-running analysis
- 📋 **Visual safety report** — colour-coded by severity, downloadable as HTML
- 🔄 **Auto-reconnect** — graceful handling of connection drops with session replay

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| LLM + Voice | Gemini 2.5 Flash Native Audio (`gemini-2.5-flash-native-audio-latest`) |
| Agent Framework | Google ADK 1.26.0 |
| Web Server | FastAPI + uvicorn (WebSocket) |
| Database | Supabase (PostgreSQL) — `cpic_cache` + `mechanism_knowledge_base` |
| DDI Data | DDInter (222,383 pairs) + DGIdb (1,897 interactions) |
| Frontend | Vanilla HTML/JS — single file, no framework |
| Deployment | Google Cloud Run (europe-west1) |
| Runtime | Python 3.11 |

---

## Architecture

```
Clinician (voice/form)
        │
        ▼
voice_ui.html (WebSocket client)
        │ PCM audio / text
        ▼
voice_server.py (FastAPI WebSocket)
        │ Gemini Live API session
        ▼
pgx_voice_agent.py (Google ADK Agent)
        │ analyze_medications() tool call
        ▼
┌─────────────────────────────────┐
│  dgi_analyzer.py                │ ← Supabase: cpic_cache
│  ddi_checker.py                 │ ← DDInter flat files
│  dosing_advisor.py              │ ← CPIC recommendations
└─────────────────────────────────┘
        │
        ▼
Safety Report (spoken + visual)
```

---

## Live Demo

🌐 **[https://pgx-guardian-333275264485.europe-west1.run.app](https://pgx-guardian-333275264485.europe-west1.run.app)**

### Demo Scenarios

**Scenario 1 — CYP2D6 + CYP2C19 Poor Metabolizer:**
> *"CYP2D6 star 4 star 4, CYP2C19 star 2 star 2. Medications: clopidogrel, codeine, omeprazole, fluoxetine."*

**Scenario 2 — TPMT + CYP2D6:**
> *"CYP2D6 star 4 star 4, TPMT star 3A star 3A. Medications: codeine, azathioprine."*

**Scenario 3 — DPYD + VKORC1 + CYP2C9:**
> *"DPYD star 2A star 2A, VKORC1 AA, CYP2C9 star 2 star 3. Medications: fluorouracil, warfarin."*

**Scenario 4 — HLA-B:**
> *"HLA-B abacavir risk, CYP2D6 star 4 star 4. Medications: abacavir, codeine."*

---

## Local Setup

### Prerequisites
- Python 3.11
- Google Gemini API key ([get one here](https://aistudio.google.com/))
- Supabase account with `cpic_cache` and `mechanism_knowledge_base` tables

### Installation

```bash
git clone https://github.com/LayalEit/pgx-guardian.git
cd pgx-guardian

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your keys:
# GEMINI_API_KEY=your_key
# SUPABASE_URL=your_url
# SUPABASE_KEY=your_key
```

### One-time data setup

```bash
# Populate PHENOTYPE_MAP and cpic_cache from CPIC API
python3 cpic_sync.py

# Inject DPYD hardcoded diplotypes
python3 dpyd_patch.py

# Add G6PD/RYR1/CACNA1S mechanism KB entries
export $(cat .env | xargs) && python3 patch_mechanism_kb.py
```

### Run

```bash
uvicorn agents.voice.voice_server:app --host 0.0.0.0 --port 8000
```

Open `voice_ui.html` in Firefox (or navigate to `http://localhost:8000`).

---

## Cloud Run Deployment

Automated deployment script included:

```bash
export $(cat .env | xargs)
./deploy.sh
```

The script builds the container image, pushes to Google Container Registry, and deploys to Cloud Run with min-instances=1 to avoid cold starts.

---

## Gene Coverage

19 pharmacogenomics genes covered:

| Category | Genes |
|----------|-------|
| CYP Metabolizers | CYP2D6, CYP2C19, CYP2C9, CYP2B6, CYP3A4, CYP3A5, CYP1A2 |
| Thiopurine | TPMT, NUDT15 |
| Fluoropyrimidine | DPYD |
| Transporter | SLCO1B1 |
| Conjugation | UGT1A1, NAT2 |
| Immune/HLA | HLA-B, HLA-A |
| Oxidative Stress | G6PD |
| Malignant Hyperthermia | RYR1, CACNA1S |
| Warfarin Sensitivity | VKORC1 |
| Interferon Response | IFNL3 |

---

## Data Sources

- **CPIC** — Clinical Pharmacogenomics Implementation Consortium guidelines
- **DDInter** — 222,383 drug-drug interaction pairs with severity classification
- **DGIdb** — Drug-Gene Interaction database
- **NLM RxNav** — Drug name normalization
- **Supabase** — Hosted PostgreSQL for CPIC cache and mechanism knowledge base

---

## Project Structure

```
pgx-guardian/
├── agents/
│   ├── voice/
│   │   ├── voice_server.py      # FastAPI WebSocket server
│   │   └── pgx_voice_agent.py   # ADK agent + normalization engine
│   ├── dgi_analyzer.py          # Gene-drug interaction analysis
│   ├── ddi_checker.py           # Drug-drug interaction scoring
│   ├── dosing_advisor.py        # CPIC dosing recommendations
│   ├── genotype_parser.py       # PHENOTYPE_MAP (42k+ diplotypes)
│   └── ddi_loader.py            # Flat-file DDI data loader
├── data/
│   ├── ddinter/                 # DDInter interaction files
│   └── dgidb/                   # DGIdb interaction files
├── voice_ui.html                # Single-file frontend
├── Dockerfile                   # Container definition
├── deploy.sh                    # Automated Cloud Run deployment
└── requirements.txt             # Python dependencies
```

---

*For clinical decision support only. All recommendations require physician review before any clinical action is taken.*

*Built with ❤️ for the Gemini Live Agent Challenge · #GeminiLiveAgentChallenge*
