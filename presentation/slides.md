---
marp: true
theme: default
paginate: true
title: Local Machine Carbon Footprint and Grid-Carbon Comparison
style: |
  section {
    background: linear-gradient(135deg, #fff7ed 0%, #ffedd5 45%, #fef3c7 100%);
    color: #1f2937;
    font-family: "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
  }
  h1, h2, h3 {
    color: #9a3412;
    font-family: "Gill Sans", "Trebuchet MS", sans-serif;
  }
  strong { color: #b45309; }
  code {
    background: #111827;
    color: #ffffff;
    border-radius: 4px;
    padding: 1px 6px;
  }
  pre {
    background: #111827;
    color: #ffffff;
    border-radius: 10px;
    padding: 12px;
  }
---

# Local Machine Carbon Footprint and Grid-Carbon Comparison

An interactive sustainability analytics prototype

---

## Agenda

1. Problem statement and objective
2. System design and architecture
3. Methodology and formulas
4. Application walkthrough
5. Limitations, validity, and next steps

---

## 1) Problem Statement

- Most users do not see the climate impact of their everyday computing.
- Existing tools are often either:
  - too technical for regular users, or
  - too simplified to support informed comparisons.
- We need one tool that connects:
  - **local device usage**, and
  - **country-level electricity carbon context**.

---

## Project Objective

Build a web application that can:

- estimate local machine energy use and emissions,
- compare several countries by current grid-carbon signals,
- provide a sustainability learning page for awareness.

Design priorities:

- transparency,
- usability,
- practical educational value.

---

## 2) System Scope

### Route-level features

- `/` → Local machine carbon estimator
- `/world-stats` → Multi-country grid comparison with visual bars
- `/learn` → Sustainability learning modules with completion tracking

---

## Architecture Overview

```text
web_app.py
  -> Local machine signal extraction (macOS)
  -> Carbon/energy estimation logic
  -> Electricity Maps API integration
  -> World comparison visualization
  -> Learning module routes

learning.db
  -> lesson metadata
  -> completion tracking

.env
  -> API token configuration
```

---

## Technology Stack

- **Backend:** Python + Flask
- **Storage:** SQLite (`learning.db`)
- **External data:** Electricity Maps API
- **Frontend:** Server-rendered HTML/CSS templates
- **Runtime:** macOS CLI signals (`sysctl`, `pmset`) for local estimator

---

## 3) Methodology: Local Estimation

Local signals used:

- boot timestamp from `sysctl -n kern.boottime`
- sleep count from `pmset -g stats`

Derived time model:

$$
uptime\_hours = t_{now} - t_{boot}
$$

$$
sleep\_hours_{est} = sleep\_count \times \frac{avg\_sleep\_minutes}{60}
$$

$$
awake\_hours_{est} = \max(0, uptime\_hours - sleep\_hours_{est})
$$

---

## Energy and Carbon Formulation

$$
E_{kWh} = \frac{awake\_hours_{est}\cdot P_{active} + sleep\_hours_{est}\cdot P_{sleep}}{1000}
$$

$$
CO2e_{kg} = E_{kWh} \cdot \frac{CI_{gCO2e/kWh}}{1000}
$$

Where:

- $P_{active}$ = assumed active power draw (W)
- $P_{sleep}$ = assumed sleep power draw (W)
- $CI$ = grid carbon intensity assumption

---

## Methodology: World Comparison

For each user-entered zone code (e.g., `IN,DE,FR,US`), the app queries:

- `GET /v3/carbon-intensity/latest`
- `GET /v3/renewable-energy/latest`

Visualization logic:

- Carbon intensity bars normalized to the highest selected zone
- Renewable share bars shown on a 0–100% scale

---

## Why This Design Matters

- Connects personal behavior and system-level electricity context.
- Enables cross-country comparisons with low interaction cost.
- Works as a teaching and discussion aid in sustainability courses.
- Keeps assumptions explicit and editable by the user.

---

## 4) Application Walkthrough

### Demo flow (recommended)

1. Open `/` and inspect machine uptime/sleep-derived estimate.
2. Adjust power assumptions and observe estimate sensitivity.
3. Open `/world-stats` and input multiple codes (e.g., `IN,DE,SE,FR`).
4. Discuss differences using the bar chart.
5. Open `/learn` and show progress tracking workflow.

---

## Example Interpretation Slide

If Zone A has:

- higher carbon-intensity bar,
- lower renewable-share bar,

then the same electricity demand tends to imply higher marginal operational emissions.

This supports practical decisions such as:

- timing flexible workloads,
- selecting deployment regions,
- communicating sustainability trade-offs.

---

## 5) Limitations and Validity

- Sleep duration is estimated from count × average duration.
- Power draw is user-specified, not measured with hardware sensors.
- API values are time-sensitive and token-permission dependent.
- This is a planning and educational tool, not legal-grade carbon accounting.

---

## Reliability and Reproducibility

- Deterministic formulas for local estimation.
- Explicit configuration in `.env` and user-input assumptions.
- Reproducible startup through `venv` + `requirements.txt`.
- Route-level smoke tests used during development.

---

## Potential Extensions

1. Real-time local power telemetry integration.
2. Time-series storage and trend analytics.
3. Downloadable report export (CSV/PDF).
4. Wider platform support beyond macOS signal model.
5. Confidence intervals via uncertainty sampling.

---

## Conclusion

- The prototype delivers an interpretable bridge between **device-level use** and **grid-level carbon context**.
- The interface supports both exploration and comparative reasoning.
- The system is intentionally simple, transparent, and extensible.

**Outcome:** practical sustainability insight with low setup overhead.
