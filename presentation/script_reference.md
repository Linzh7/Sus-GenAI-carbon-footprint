# Presenter Script (Reference)

Use this as a spoken guide for the project presentation.

## 0) 30-second opener

"This project is a lifecycle carbon and cost estimator for GenAI workloads.  
It keeps the core model intentionally simple, then adds optional realism through profiles, request-based inference, and uncertainty ranges.  
The goal is to be useful on day one, but still defensible when decisions matter."

## 1) Problem framing

"Teams usually have two bad options: a rough guess with no traceability, or a heavy simulation that is too slow to use in planning.  
We solve this by keeping one transparent baseline equation and making every extra assumption explicit."

## 2) Core model explanation

"At the center, energy is GPU count times runtime times utilization times TDP times PUE.  
Then CO2e is energy multiplied by grid carbon intensity.  
Everything else in the system is a controlled adjustment around this core."

## 3) What is new in this implementation

"We implemented three major upgrades without replacing the core model:
1. Time-resolved carbon intensity from Electricity Maps and UK National Grid.
2. Operational profiles for common training and inference modes.
3. Uncertainty output, including optional Monte Carlo p10/p50/p90."

## 4) Explain profiles clearly

"Profiles are the abstraction layer that keeps complexity manageable.  
Instead of hardcoding per-model behavior in code, we keep defaults in `data.json`.  
For training, profiles capture typical utilization and overhead patterns.  
For inference, profiles also include request-level defaults."

## 5) Explain inference modes

"Inference can be modeled in two ways:
1. GPU-hours mode, when infra runtime is known.
2. Request-based mode, when product traffic metrics are known.

Request mode supports:
- kWh per 1k tokens
- a linear token model with separate input and output token coefficients.

This makes the estimator usable by both infra teams and product teams."

## 6) Explain calibration and trust

"`rho` can come from three sources in priority order:
1. User override
2. Telemetry calibration
3. Profile default

This is important because utilization is one of the largest uncertainty drivers.  
We show ranges and optional Monte Carlo so output is honest about confidence."

## 7) Explain output contract

"The output is structured JSON with:
- per-phase training, inference, embodied
- energy, CO2e, electricity cost, GPU cost, carbon cost
- efficiency metrics like TFLOPs per kWh
- uncertainty ranges and optional simulation summary.

So it is presentation-friendly and automation-friendly."

## 8) Demo narration script (2-3 minutes)

"First, we run a baseline in GPU-hours mode with a training profile.  
Second, we switch to request-based inference and show token-driven behavior.  
Third, we enable Monte Carlo and compare p10/p50/p90.  
Finally, we change region and show CI impact directly."

## 9) Close

"The project is intentionally not a full simulator.  
It is a decision tool: quick, transparent, and extensible.  
You can start simple, then add calibration only when needed."

## 10) Short Q&A answers

Q: "Why not model everything in detail?"  
A: "Because planning needs speed and transparency. We keep complexity optional."

Q: "How accurate is it?"  
A: "Good for scenario comparison and planning, not for legal-grade facility audits."

Q: "How do we improve accuracy?"  
A: "Use telemetry for rho and benchmark-derived per-token energy."
