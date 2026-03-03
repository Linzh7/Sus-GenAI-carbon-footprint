---
marp: true
theme: default
paginate: true
title: Lifecycle Carbon Footprint Estimator
---

# Lifecycle Carbon Footprint Estimator

GenAI training + inference lifecycle estimator  
Project presentation  
Date: March 3, 2026

---

## Problem

- Teams need fast carbon/cost estimates early in planning.
- Raw infrastructure telemetry is often unavailable.
- Training and inference have different operational patterns.
- Stakeholders need both simplicity and scientific transparency.

---

## Core Equation (Kept Simple)

\[
E = N \cdot T \cdot \rho \cdot TDP \cdot PUE
\]
\[
CO2e = E \cdot CI
\]

- `N`: number of GPUs
- `T`: runtime hours
- `rho`: utilization factor
- `TDP`: GPU thermal design power
- `PUE`: datacenter overhead
- `CI`: carbon intensity

---

## What We Added Without Breaking Simplicity

- Workload profiles:
  - Training: `pretraining_dense`, `finetune_sft`, `rlhf_ppo`, `hyperparam_sweep`
  - Inference: `online_low_batch`, `offline_batch`, `rag_serving`, `cached_chat`
- Two inference modes:
  - GPU-hours mode
  - Request-based mode
- Time-resolved CI integration
- Uncertainty outputs + Monte Carlo (p10/p50/p90)

---

## Carbon Intensity Integrations

- Electricity Maps:
  - latest endpoint
  - historical window average (time-resolved)
- UK National Grid API:
  - latest
  - historical range average
- Source and mode are user-selectable in CLI.

---

## Request-Based Inference

Two options:

1. `kwh_per_1k_tokens`
\[
E_{infer} \approx \frac{tokens}{1000}\cdot kWh_{/1k}\cdot PUE
\]

2. Linear token model:
\[
E_{req} = a \cdot L_{in} + b \cdot L_{out}
\]

- Includes optional factors: quantization, batching, streaming overhead, context length, KV-cache hit rate, dense vs MoE family factor.

---

## Extended Operational Modeling

Added structured knobs for realism:

- NVLink/InfiniBand watts per GPU
- CPU and memory watts per GPU
- Idle provisioning fraction
- Power cap factor
- Thermal throttle factor
- Network overhead percentage

All optional and defaults are profile-driven.

---

## Calibration + Uncertainty

- `rho_training` and `rho_inference` sources:
  - profile/default
  - manual override
  - telemetry calibration (CSV/JSON)
- Uncertainty ranges always reported.
- Optional Monte Carlo simulation returns:
  - mean
  - p10
  - p50
  - p90

---

## Project Structure (Refactored)

```
main.py                # entry point
cli.py                 # interactive workflow
models.py              # dataclasses
carbon_clients.py      # Electricity Maps + UK Grid clients
estimator.py           # core calculations + uncertainty
data.json              # profiles + defaults + GPU/cloud catalogs
presentation/          # project slides
```

---

## Output Highlights

Returns structured JSON including:

- per-phase metrics (training, inference, embodied):
  - energy (kWh)
  - CO2e (kg)
  - electricity cost
  - GPU cost
  - carbon cost
- totals
- efficiency metrics (`TFLOPs/kWh`)
- uncertainty bands + optional Monte Carlo summary

---

## Practical Value

- Fast baseline for project planning.
- Clear assumptions for decision review.
- Supports region and hardware scenario comparisons.
- Works in default mode and advanced mode.

---

## Next Steps

1. Add automated benchmark harness for `kwh_per_1k_tokens`.
2. Add report export templates (PDF/CSV/JSON schema).
3. Add CI tests with fixture responses for API clients.
4. Add non-interactive CLI flags for pipeline automation.
