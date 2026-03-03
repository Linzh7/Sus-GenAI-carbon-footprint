---
marp: true
theme: default
paginate: true
title: Lifecycle Carbon Footprint Estimator
style: |
  section {
    background: linear-gradient(135deg, #fff7ed 0%, #ffedd5 40%, #fef3c7 100%);
    color: #1f2937;
    font-family: "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
  }
  h1, h2, h3 {
    color: #9a3412;
    font-family: "Gill Sans", "Trebuchet MS", sans-serif;
  }
  strong { color: #b45309; }
  code {
    background: #1f2937;
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

# Lifecycle Carbon Footprint Estimator

Simple baseline, practical realism, transparent uncertainty

---

## Agenda

1. Why this project exists
2. Model philosophy and scope
3. System architecture and implementation
4. Key capabilities and scenarios
5. Limitations, validation, and roadmap

---

## 1) Problem Context

- AI teams must estimate carbon and cost **before** full telemetry is available.
- Existing options are often:
  - too simple (single guess, no assumptions)
  - too heavy (simulation complexity, low usability)
- Training and inference differ strongly in behavior and metrics.

---

## Why Existing Estimation Fails in Practice

- Treating all workloads as uniform GPU utilization.
- Ignoring regional and temporal grid carbon variation.
- No bridge from infra metrics to product metrics (tokens/requests).
- Single-value outputs without confidence or uncertainty ranges.

---

## 2) Design Principle

**Keep the core estimator simple.**
Add realism through optional, structured controls.

- Default mode: fast, defensible baseline.
- Advanced mode: calibration and deeper assumptions.
- No hidden heuristics; assumptions are explicit in config and output.

---

## Core Equation (Preserved)

$$
E = N \cdot T \cdot \rho \cdot TDP \cdot PUE
$$

$$
CO2e = E \cdot CI
$$

Where:

- `N`: GPU count
- `T`: runtime
- `rho`: utilization factor
- `CI`: carbon intensity

---

## What We Added Without Replacing the Core

- Workload profiles for training and inference modes.
- Two inference pathways:
  - GPU-hours
  - Request-based (token-driven)
- Time-resolved CI providers.
- Uncertainty bands + optional Monte Carlo summary.

---

## 3) Architecture Overview

```text
main.py
  -> cli.py             (interactive input flow)
  -> carbon_clients.py  (CI providers)
  -> estimator.py       (energy, CO2e, cost, uncertainty)
  -> models.py          (typed dataclasses)
  -> data.json          (profiles, defaults, mappings)
```

---

## Data-Driven Configuration Strategy

`data.json` centralizes:

- defaults (`rho`, PUE, carbon price)
- training/inference profiles
- uncertainty parameters
- cloud instance mapping
- GPU catalog (TDP, embodied, pricing)

Result: model behavior is editable without changing code paths.

---

## User Input Flow (CLI)

1. Select GPU model or cloud instance mapping.
2. Select region, CI source, and CI mode.
3. Select workload profiles.
4. Choose inference mode (GPU-hours or request-based).
5. Optional: telemetry calibration + advanced power model + Monte Carlo.

---

## CI Integration: Electricity and Time

Supported providers:

- **Electricity Maps**: latest + historical window average
- **UK National Grid API**: latest + historical interval average

CI mode can be:

- `latest` (single value)
- `time_resolved` (window average per phase)

---

## Workload Profiles (Training)

Included training profiles:

- `pretraining_dense`
- `finetune_sft`
- `rlhf_ppo`
- `hyperparam_sweep`

Each can tune:

- `rho`
- `idle_fraction`
- network/CPU overhead defaults

---

## Workload Profiles (Inference)

Included inference profiles:

- `online_low_batch`
- `offline_batch`
- `rag_serving`
- `cached_chat`

Each can include request defaults:

- `kwh_per_1k_tokens`
- linear token coefficients `a`, `b`
- quantization/batch/streaming factors

---

## Inference Modeling: Two Practical Modes

### A) GPU-hours mode

- best when infra runtime is measured

### B) Request-based mode

- best when product metrics are available

$$
E_{infer} \approx \frac{tokens}{1000}\cdot kWh_{1k}\cdot PUE
$$

$$
E_{req} = a\cdot L_{in} + b\cdot L_{out}
$$

---

## Extended Operational Knobs (Optional)

To keep realism practical, we add bounded factors:

- idle provisioning
- NVLink/InfiniBand overhead
- CPU and memory overhead
- power cap factor
- thermal throttle factor
- network overhead percentage

These modify energy while preserving transparent math.

---

## Telemetry-Based Calibration

`rho` source precedence:

1. user override
2. telemetry-derived estimate
3. profile/default

Telemetry input:

- CSV or JSON
- fields: `phase`, `gpu_power_watts`, optional `tdp_watts`

---

## Uncertainty Method

Two levels of uncertainty output:

1. Deterministic low/high ranges from configured percentages.
2. Optional Monte Carlo summary:
   - mean
   - p10
   - p50
   - p90

This improves scientific honesty for planning decisions.

---

## 4) Scenario A: Region Comparison

Example question:

- Same workload, same hardware, different grid region.

What changes:

- `CI` and electricity price by country.

What stays fixed:

- model, runtime, profile, power factors.

Outcome:

- separates operational efficiency from grid effect.

---

## Scenario B: Inference Strategy Comparison

Compare the same product workload under:

- GPU-hours inference mode
- request-based token mode

Purpose:

- reveal sensitivity to batching, context, cache hit rate, and quantization.

This is especially useful for product and infra alignment.

---

## Output Contract (Machine + Human Friendly)

Top-level output sections:

- `meta`
- `phases.training`
- `phases.inference`
- `phases.embodied`
- `efficiency`
- `uncertainty_ranges`
- `monte_carlo`
- `total`

---

## How Teams Can Use the Output

- PM/Finance: total cost and scenario tradeoffs
- Sustainability: carbon reporting with assumptions
- Infra: profile calibration and efficiency tuning
- Research: hardware/region strategy comparisons

---

## 5) Known Boundaries

This is a planning estimator, not a facility audit tool.

Not modeled in detail:

- full network topology power behavior
- all CPU/memory dynamics by micro-architecture
- legal-grade accounting workflows

---

## Validation and Quality

Implemented checks include:

- schema-driven defaults in `data.json`
- compile and smoke tests
- structured, explicit output fields

Planned quality upgrades:

- fixture-based API client tests
- benchmark harness for per-token calibration

---

## Roadmap

1. Add benchmark pipeline for `kwh_per_1k_tokens` estimation.
2. Add non-interactive CLI flags for automation.
3. Add report export templates (PDF/CSV/JSON profile).
4. Add CI tests and packaging for repeatable releases.

---

## Closing

- We keep the estimator simple by default.
- We provide realistic controls when needed.
- We expose assumptions and uncertainty clearly.

**Result:** fast decisions with defensible transparency.
