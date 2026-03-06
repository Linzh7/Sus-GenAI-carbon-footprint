# Local Machine Carbon Footprint and Comparative Grid-Carbon Analytics

## Abstract

This project implements a lightweight web-based decision-support tool for operational sustainability awareness. The system combines two complementary perspectives: (1) **device-level estimation** of electricity use and associated emissions for the host machine, and (2) **grid-level comparison** of carbon intensity and renewable-share indicators across user-selected countries/zones. The application is designed for transparency and pedagogical usability rather than high-precision life-cycle accounting.

## Research Motivation

Digital sustainability interventions often fail because users cannot directly connect personal device use with broader energy-system dynamics. This tool addresses that gap by integrating:

- local machine activity-derived estimates (uptime/sleep-informed), and
- external electricity-system signals (zone-level carbon and renewable metrics).

The intent is to support exploratory analysis, classroom demonstration, and early-stage sustainability reporting workflows.

## System Scope

The application exposes three routes:

- `/` — **Local machine estimator** (macOS-focused runtime signals + energy/emissions estimation)
- `/world-stats` — **Multi-country comparison dashboard** (text input of comma-separated zone codes; bar-chart style visual comparison)
- `/learn` — **Sustainability learning module** with SQLite-backed completion tracking

## Methodology

### 1) Local machine energy and carbon estimation

The system reads two host-level indicators:

- `sysctl -n kern.boottime` (boot timestamp)
- `pmset -g stats` (sleep count)

Derived quantities:

- $uptime\_hours = t_{now} - t_{boot}$
- $sleep\_hours_{est} = sleep\_count \times \frac{avg\_sleep\_minutes}{60}$
- $awake\_hours_{est} = \max(0, uptime\_hours - sleep\_hours_{est})$

Energy and emissions model:

- $E_{kWh} = \frac{awake\_hours_{est} \cdot P_{active} + sleep\_hours_{est} \cdot P_{sleep}}{1000}$
- $CO2e_{kg} = E_{kWh} \cdot \frac{CI_{gCO2e/kWh}}{1000}$

where $P_{active}$ and $P_{sleep}$ are user-defined power assumptions, and $CI$ is user-defined grid carbon intensity.

### 2) Comparative world metrics

For each zone code provided on `/world-stats` (e.g., `IN,DE,FR,US`), the app retrieves:

- latest carbon intensity, and
- latest renewable-energy percentage

from Electricity Maps API endpoints and renders comparative horizontal bars:

- Carbon bar normalized to the highest carbon intensity among selected zones
- Renewable bar represented on a 0–100% scale

## Data Sources

- **Host telemetry (local):** macOS CLI utilities (`sysctl`, `pmset`)
- **Grid metrics (external):** Electricity Maps API (`https://api.electricitymap.org`)

## Assumptions and Limitations

1. **Sleep duration is estimated**, not directly measured as cumulative duration in this implementation.
2. Power draw inputs are user assumptions; no direct hardware wattmeter integration is performed.
3. World metrics reflect API availability and token permissions.
4. The Electricity Maps public website may block third-party iframe embedding; therefore the app provides a direct “open in new tab” link for the live map.
5. This tool is suitable for educational and exploratory analysis, not audited emissions disclosure.

## Configuration

Environment variables are loaded from a local `.env` file (if present).

Add your token in `.env`:

```bash
ELECTRICITYMAPS_API_TOKEN="your_token_here"
```

Equivalent shell override:

```bash
export ELECTRICITYMAPS_API_TOKEN="your_token_here"
```

## Reproducible Run Procedure

### 1) Create and activate virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure optional API token

```bash
cp .env .env.local 2>/dev/null || true
# then set ELECTRICITYMAPS_API_TOKEN in .env
```

### 4) Run the application

```bash
python web_app.py
```

### 5) Open in browser

- `http://127.0.0.1:5000/` (local estimator)
- `http://127.0.0.1:5000/world-stats` (country comparison)
- `http://127.0.0.1:5000/learn` (learning page)

## Project Structure (Key Files)

- `web_app.py` — Flask application, estimation logic, API integration, visualization templates
- `.env` — local secrets/configuration (not committed)
- `learning.db` — SQLite store for learning progress
- `requirements.txt` — runtime Python dependencies
