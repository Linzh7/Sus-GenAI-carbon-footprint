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

**Agenda:** Problem & Objective → Architecture → Methodology → Demo → Limitations & Next Steps

---

## Problem Statement & Objective

**Problem:** Most users don't see the climate impact of everyday computing. Existing tools are either too technical or too simplified.

**Objective:** Build a web application that can:

- Estimate **local machine** energy use and CO₂ emissions
- Compare countries by **real-time grid-carbon** signals (Electricity Maps API)
- Provide a **sustainability learning** page with completion tracking

| Route          | Feature                                        |
| -------------- | ---------------------------------------------- |
| `/`            | Local machine carbon estimator                 |
| `/world-stats` | Multi-country grid comparison with visual bars |
| `/learn`       | Sustainability learning modules                |

---

## System Design & Technology Stack

```text
web_app.py
  → Local signal extraction (macOS: sysctl, pmset)
  → Carbon/energy estimation logic
  → Electricity Maps API integration
  → World comparison visualization
  → Learning module routes
```

| Layer             | Technology                    |
| ----------------- | ----------------------------- |
| **Backend**       | Python + Flask                |
| **Storage**       | SQLite (`learning.db`)        |
| **External data** | Electricity Maps API          |
| **Frontend**      | Server-rendered HTML/CSS      |
| **Local signals** | macOS CLI (`sysctl`, `pmset`) |

---

## Methodology: Local Estimation

**Inputs:** boot timestamp (`sysctl`), sleep count (`pmset`)

**Time model:**

$$
awake\_hours = \max\!\Big(0,\;(t_{now} - t_{boot}) - sleep\_count \times \tfrac{avg\_sleep\_min}{60}\Big)
$$

**Energy & carbon:**

$$
E_{kWh} = \frac{awake \cdot P_{active} + sleep \cdot P_{sleep}}{1000}
\qquad
CO2e_{kg} = E_{kWh} \cdot \frac{CI_{gCO2/kWh}}{1000}
$$

- $P_{active}$, $P_{sleep}$ = user-specified power draw (W)
- $CI$ = grid carbon intensity (gCO₂/kWh)

---

## Methodology: World Comparison

For each zone code (e.g., `IN,DE,FR,US`), the app queries:

- `GET /v3/carbon-intensity/latest`
- `GET /v3/renewable-energy/latest`

**Visualization:** carbon intensity bars normalized to the highest zone; renewable share on 0–100% scale.

**Why this matters:**

- Connects **personal device behavior** with **system-level electricity context**
- Enables cross-country comparison with minimal interaction cost
- Supports decisions like timing flexible workloads or selecting deployment regions

---

## Application Walkthrough

### Recommended demo flow

1. Open `/` — inspect machine uptime/sleep-derived estimate
2. Adjust power assumptions — observe estimate sensitivity
3. Open `/world-stats` — input zone codes (e.g., `IN,DE,SE,FR`)
4. Compare bars — higher carbon intensity + lower renewables → higher marginal emissions
5. Open `/learn` — show progress tracking workflow

---

## Interpreting results

| Zone pattern            | Implication                                |
| ----------------------- | ------------------------------------------ |
| High CI, low renewables | Same demand → higher operational emissions |
| Low CI, high renewables | Cleaner grid → lower marginal footprint    |

---

## Limitations & Next Steps

**Limitations:**

- Sleep duration estimated from count × average (not measured)
- Power draw is user-specified, not hardware-metered
- API values are time-sensitive and token-permission dependent

**Potential extensions:**

1. Real-time local power telemetry integration
2. Time-series storage and trend analytics
3. Confidence intervals via uncertainty sampling

**Conclusion:** The prototype bridges **device-level use** and **grid-level carbon context** with transparency, simplicity, and extensibility.
