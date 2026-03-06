import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from estimator import (
    clamp,
    load_db,
    phase_cost_gpu_hours,
)
from models import PowerModel


DB_PATH = Path(__file__).with_name("data.json")
LOG_PATH = Path(__file__).with_name("usage_log.jsonl")


def _load_db() -> Dict[str, Any]:
    return load_db(DB_PATH)


def _select_gpu(db: Dict[str, Any], gpu_model: str | None) -> str:
    specs = db.get("gpu_specs", {})
    if not specs:
        raise SystemExit("No GPU specs found in data.json.")
    if gpu_model:
        if gpu_model not in specs:
            available = ", ".join(sorted(specs.keys()))
            raise SystemExit(f"Unknown GPU model '{gpu_model}'. Available: {available}")
        return gpu_model
    models = sorted(specs.keys())
    print("Available GPU models:")
    for idx, name in enumerate(models, start=1):
        print(f"  {idx}. {name}")
    while True:
        raw = input("Select GPU model by number: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(models):
                return models[idx - 1]
        print("Please enter a valid number from the list.")


def _default_power_model(db: Dict[str, Any]) -> PowerModel:
    defaults = db.get("power_model_defaults", {})
    return PowerModel(
        idle_fraction=float(defaults.get("idle_fraction", 0.05)),
        idle_power_fraction=float(defaults.get("idle_power_fraction", 0.25)),
        nvlink_watts_per_gpu=float(defaults.get("nvlink_watts_per_gpu", 0.0)),
        cpu_watts_per_gpu=float(defaults.get("cpu_watts_per_gpu", 40.0)),
        memory_watts_per_gpu=float(defaults.get("memory_watts_per_gpu", 20.0)),
        power_cap_factor=float(defaults.get("power_cap_factor", 1.0)),
        thermal_throttle_factor=float(defaults.get("thermal_throttle_factor", 1.0)),
        network_overhead_pct=float(defaults.get("network_overhead_pct", 0.0)),
    )


def track_run(args: argparse.Namespace) -> None:
    db = _load_db()
    gpu_model = _select_gpu(db, args.gpu_model)
    specs = db["gpu_specs"][gpu_model]

    num_gpus = max(1, args.num_gpus)
    hours = max(0.0, args.hours)
    phase = args.phase

    defaults = db["defaults"]
    country = args.country.upper()
    pue = float(db.get("pue_by_country", {}).get(country, defaults["pue_default"]))
    electricity_price = float(
        db.get("electricity_price_usd_per_kwh_by_country", {}).get(
            country, defaults["electricity_price_usd_per_kwh"]
        )
    )
    carbon_price = float(defaults["carbon_price_usd_per_ton_co2e"])

    tdp_watts = float(specs["tdp_watts"])
    gpu_hourly_price = float(specs["hourly_price_usd"])

    if phase == "training":
        rho_default = float(db.get("training_profiles", {}).get("pretraining_dense", {}).get("rho", defaults["rho_training"]))
    else:
        rho_default = float(db.get("inference_profiles", {}).get("online_low_batch", {}).get("rho", defaults["rho_inference"]))
    rho = clamp(args.rho if args.rho is not None else rho_default, 0.0, 1.0)

    ci_g_per_kwh = args.ci
    if ci_g_per_kwh is None:
        ci_g_per_kwh = 300.0

    power_model = _default_power_model(db)

    result = phase_cost_gpu_hours(
        hours=hours,
        num_gpus=num_gpus,
        tdp_watts=tdp_watts,
        rho=rho,
        pue=pue,
        ci_g_per_kwh=ci_g_per_kwh,
        electricity_usd_per_kwh=electricity_price,
        gpu_usd_per_hour=gpu_hourly_price,
        carbon_usd_per_ton=carbon_price,
        power_model=power_model,
    )

    record = {
        "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "phase": phase,
        "gpu_model": gpu_model,
        "num_gpus": num_gpus,
        "hours": hours,
        "country": country,
        "rho": rho,
        "ci_g_per_kwh": ci_g_per_kwh,
        "pue": pue,
        "metrics": result,
        "notes": args.notes or "",
    }

    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    co2 = result["co2e_kg"]
    energy = result["energy_kwh"]
    total_cost = result["total_cost_usd"]
    car_km_equiv = co2 / 0.12 if co2 > 0 else 0.0

    print("\n=== Run Summary ===")
    print(f"GPU: {gpu_model} x{num_gpus}")
    print(f"Phase: {phase}")
    print(f"Hours: {hours:.2f}")
    print(f"Country: {country} (PUE={pue:.2f}, CI={ci_g_per_kwh:.0f} gCO2e/kWh)")
    print(f"Energy: {energy:.2f} kWh")
    print(f"Emissions: {co2:.2f} kgCO2e")
    print(f"Total cost (energy+GPU+carbon): ${total_cost:.2f}")
    print(f"Approx. equivalent: {car_km_equiv:.1f} km driven by a petrol car")
    if args.notes:
        print(f"Notes: {args.notes}")
    print(f"\nSaved to log file: {LOG_PATH.name}")


def summarize_runs(_args: argparse.Namespace) -> None:
    if not LOG_PATH.exists():
        print("No usage log found yet. Track a run first.")
        return

    total_energy = 0.0
    total_co2 = 0.0
    total_cost = 0.0

    by_phase: Dict[str, Dict[str, float]] = {}

    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            metrics = rec.get("metrics", {})
            phase = rec.get("phase", "unknown")

            energy = float(metrics.get("energy_kwh", 0.0))
            co2 = float(metrics.get("co2e_kg", 0.0))
            cost = float(metrics.get("total_cost_usd", 0.0))

            total_energy += energy
            total_co2 += co2
            total_cost += cost

            ph = by_phase.setdefault(phase, {"energy_kwh": 0.0, "co2e_kg": 0.0, "total_cost_usd": 0.0})
            ph["energy_kwh"] += energy
            ph["co2e_kg"] += co2
            ph["total_cost_usd"] += cost

    print("\n=== Aggregated GenAI Footprint ===")
    print(f"Total energy: {total_energy:.2f} kWh")
    print(f"Total emissions: {total_co2:.2f} kgCO2e")
    print(f"Total cost (energy+GPU+carbon): ${total_cost:.2f}")
    car_km_equiv = total_co2 / 0.12 if total_co2 > 0 else 0.0
    print(f"Approx. equivalent distance: {car_km_equiv:.1f} km driven by a petrol car")

    if by_phase:
        print("\nBy phase:")
        for phase, stats in by_phase.items():
            print(
                f"  {phase}: {stats['energy_kwh']:.2f} kWh, "
                f"{stats['co2e_kg']:.2f} kgCO2e, "
                f"${stats['total_cost_usd']:.2f}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simple GenAI carbon footprint tracker using the lifecycle estimator.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_track = sub.add_parser("track", help="Track a single GenAI training or inference run.")
    p_track.add_argument("--gpu-model", type=str, help="GPU model name from data.json (if omitted, choose interactively).")
    p_track.add_argument("--num-gpus", type=int, default=1, help="Number of GPUs used.")
    p_track.add_argument("--country", type=str, default="FI", help="Country code, e.g., FI, DE, US, GB.")
    p_track.add_argument("--phase", type=str, choices=["training", "inference"], default="inference", help="Workload phase.")
    p_track.add_argument("--hours", type=float, required=True, help="Wall-clock hours the GPUs were allocated.")
    p_track.add_argument(
        "--rho",
        type=float,
        help="Optional utilization factor 0–1 (overrides defaults).",
    )
    p_track.add_argument(
        "--ci",
        type=float,
        help="Grid carbon intensity in gCO2e/kWh (if omitted, uses 300 gCO2e/kWh as a rough average).",
    )
    p_track.add_argument("--notes", type=str, help="Free-text notes (experiment name, model, etc.).")
    p_track.set_defaults(func=track_run)

    p_summary = sub.add_parser("summary", help="Summarize all logged GenAI runs so far.")
    p_summary.set_defaults(func=summarize_runs)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

