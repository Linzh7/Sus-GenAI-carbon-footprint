# Lifecycle Carbon Footprint Estimator for GenAI Models

## Overview

This repository provides a simplified but scientifically grounded estimator for the lifecycle carbon footprint of GenAI workloads, including:

- Training emissions
- Inference emissions
- Optional embodied hardware emissions

The model is intentionally simple:

[
\text{CO₂e} =
N \cdot T \cdot \rho \cdot \text{TDP} \cdot \text{PUE} \cdot \text{CI}
]

where:

- (N) = number of GPUs
- (T) = runtime (hours)
- (\rho) = utilization factor
- TDP = thermal design power (W)
- PUE = data center overhead factor
- CI = carbon intensity (kgCO₂e/kWh)

The goal is transparency, reproducibility, and easy implementation.

## Data Sources and Reliability Assessment

This section classifies each parameter by scientific reliability.

### 1. GPU TDP (`tdp_watts`)

#### Source

- Official NVIDIA product specification sheets
- Vendor documentation (A100, H100, H200, etc.)
- Manufacturer datasheets

#### Reliability

✔ High reliability as specification
⚠ Not equal to actual runtime power

TDP represents a maximum thermal envelope, not sustained draw.

Typical sustained draw:

- Training: 65–85% of TDP
- Inference: 20–60% of TDP

Error risk: moderate if misinterpreted as actual power.

### 2. Carbon Intensity (`CI`)

#### Source

- Electricity Maps API (real-time and historical grid intensity)
- National grid public APIs (e.g., UK Carbon Intensity API)

#### Reliability

✔ High for reported average grid intensity
⚠ Depends on:

- Zone mapping accuracy
- Time averaging
- Whether using average vs marginal emissions

Using time-averaged CI is acceptable for lifecycle accounting.
Marginal CI is preferred for carbon-aware scheduling studies.

Error risk: low to moderate depending on time resolution.

### 3. PUE (Power Usage Effectiveness)

#### Source

- Industry averages
- Cloud provider disclosures
- Academic infrastructure reports

#### Reliability

⚠ Moderate reliability
Varies significantly by:

- Cloud provider
- Season
- Facility design

Typical values:

- Hyperscale: 1.1–1.2
- Enterprise DC: 1.3–1.6

If unknown, default 1.2 introduces potential ±15% error.

### 4. Utilization Factor (`rho`)

#### Source

- Empirical measurements (NVML logs, academic energy papers)
- Engineering assumptions

#### Reliability

⚠ Low to moderate
Highly workload dependent:

- Compute-bound training: 0.7–0.85
- Memory-bound: 0.5–0.7
- Inference batch=1: 0.2–0.5

This is one of the largest uncertainty drivers.

Recommendation:

- Allow user override
- Provide uncertainty range

### 5. Embodied Emissions (`embodied_kgco2e`)

#### Source

- NVIDIA Product Carbon Footprint (PCF) summaries (HGX systems)
- Academic lifecycle assessment studies
- Server-level LCA allocation models

#### Reliability

⚠ Low to moderate
Embodied carbon per GPU is typically:

- Derived from server-level data
- Allocated proportionally
- Rarely directly measured per unit

Uncertainty range: ±30–50%.

Highly sensitive to assumed hardware lifetime.

### 6. Lifetime Assumption (`expected_lifetime_hours`)

#### Source

- Depreciation policy
- Industry cluster turnover patterns

#### Reliability

⚠ Low
Varies widely:

- Hyperscale training clusters: ~3 years
- Inference clusters: 3–5 years
- Research clusters: variable

Embodied allocation scales inversely with lifetime.

Major uncertainty contributor.

## Accuracy Ranking (Most to Least Reliable)

1. Carbon intensity (if time-resolved)
2. TDP specification
3. PUE
4. Utilization factor (ρ)
5. Embodied emissions
6. Lifetime assumption

## Workflow

### High-Level Flow

```
User Input
   |
   v
Validate GPU Model  -----> Lookup GPU TDP
   |
   v
Resolve Location  -----> Map to Electricity Maps Zone
   |
   v
Fetch Carbon Intensity (CI)
   |
   v
Lookup PUE + Defaults
   |
   v
Compute Energy:
    E = N * T * rho * TDP * PUE
   |
   v
Compute Emissions:
    CO2e = E * CI
   |
   v
(Optional)
Compute Embodied Allocation
   |
   v
Return:
  - Training emissions
  - Inference emissions
  - Embodied emissions
  - Total
```

### Detailed Computation Flow

```
                +------------------+
                |   User Inputs    |
                |------------------|
                | GPU model        |
                | ## GPUs           |
                | Training hours   |
                | Inference hours  |
                | Location         |
                +--------+---------+
                         |
                         v
         +-------------------------------+
         | Lookup GPU Database           |
         | - TDP                         |
         | - Embodied (optional)         |
         +---------------+---------------+
                         |
                         v
         +-------------------------------+
         | Fetch Carbon Intensity (CI)   |
         | from Electricity Maps API     |
         +---------------+---------------+
                         |
                         v
         +-------------------------------+
         | Determine Parameters          |
         | - rho                         |
         | - PUE                         |
         +---------------+---------------+
                         |
                         v
         +-------------------------------+
         | Energy Calculation            |
         | E = N * T * rho * TDP * PUE   |
         +---------------+---------------+
                         |
                         v
         +-------------------------------+
         | CO2 Calculation               |
         | CO2e = E * CI                 |
         +---------------+---------------+
                         |
                         v
         +-------------------------------+
         | Add Embodied (if enabled)     |
         +---------------+---------------+
                         |
                         v
                +------------------+
                |    Final Output  |
                +------------------+
```

## Assumptions

This estimator assumes:

- Linear scaling of energy with GPU-hours
- No modeling of communication network overhead
- No CPU power modeling (minor relative to GPU-heavy training)
- No dynamic workload power variation
- No time-shifting optimization

This is a simplified operational LCA model.

## Known Limitations

- Does not model:
  - NVLink / InfiniBand energy
  - CPU and memory energy
  - Idle provisioning
  - Power capping
  - Thermal throttling

- Training time must be supplied or externally estimated.

- Architecture performance differences are reflected through runtime, not artificial efficiency multipliers.

## Recommended Use

Appropriate for:

- Early-stage project footprint estimation
- Scenario comparison (A100 vs H100)
- Region comparison (DE vs FI)
- Training vs inference tradeoff analysis
- Research proposals and carbon reporting

Not appropriate for:

- Precise infrastructure carbon accounting
- Facility-level sustainability audits
- Legal reporting

## Suggested Extensions

- Add Monte Carlo uncertainty propagation
- Add TFLOPs-per-watt modeling
- Add telemetry-based rho calibration
- Add cloud instance mapping (AWS, GCP, Azure)
- Add time-resolved CI integration instead of average

## Transparency Statement

All numerical assumptions are documented.
Fields derived from manufacturer specifications are labeled as such.
Fields based on heuristic or allocation assumptions are explicitly identified as uncertain.

This estimator prioritizes transparency over hidden complexity.
