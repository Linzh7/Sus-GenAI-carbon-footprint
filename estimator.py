import csv
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models import PowerModel, RequestInferenceModel, UserInput


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def load_db(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_country(raw_country: str, db: Dict[str, Any]) -> str:
    raw = raw_country.strip()
    if not raw:
        raise ValueError("Country cannot be empty.")
    upper = raw.upper()
    aliases = db.get("country_aliases", {})
    if upper in db.get("electricitymaps_zone_by_country", {}):
        return upper
    if upper in db.get("electricity_price_usd_per_kwh_by_country", {}):
        return upper
    alias_match = aliases.get(raw.lower())
    if alias_match:
        return alias_match
    return upper


def resolve_zone(country: str, db: Dict[str, Any]) -> str:
    return db["electricitymaps_zone_by_country"].get(country, country)


def load_telemetry_rho(path: str, tdp_watts: float) -> Dict[str, float]:
    fp = Path(path).expanduser()
    if not fp.exists():
        raise FileNotFoundError(f"Telemetry file not found: {fp}")

    grouped: Dict[str, List[float]] = {"training": [], "inference": []}
    if fp.suffix.lower() == ".json":
        with fp.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload if isinstance(payload, list) else payload.get("samples", [])
        if not isinstance(rows, list):
            raise ValueError("Telemetry JSON must be a list or contain a 'samples' list.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            phase = str(row.get("phase", "training")).strip().lower()
            watts = row.get("gpu_power_watts")
            sample_tdp = row.get("tdp_watts", tdp_watts)
            if phase in grouped and isinstance(watts, (int, float)) and isinstance(sample_tdp, (int, float)) and sample_tdp > 0:
                grouped[phase].append(clamp(float(watts) / float(sample_tdp), 0.0, 1.2))
    else:
        with fp.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                phase = str(row.get("phase", "training")).strip().lower()
                watts_raw = row.get("gpu_power_watts")
                tdp_raw = row.get("tdp_watts")
                try:
                    watts = float(watts_raw) if watts_raw else None
                    sample_tdp = float(tdp_raw) if tdp_raw else tdp_watts
                except ValueError:
                    continue
                if phase in grouped and watts is not None and sample_tdp > 0:
                    grouped[phase].append(clamp(watts / sample_tdp, 0.0, 1.2))

    out: Dict[str, float] = {}
    for phase in ("training", "inference"):
        if grouped[phase]:
            out[phase] = sum(grouped[phase]) / len(grouped[phase])
    return out


def choose_rho(
    explicit: Optional[float],
    telemetry: Dict[str, float],
    phase: str,
    default_value: float,
) -> float:
    if explicit is not None:
        return clamp(explicit, 0.0, 1.0)
    if phase in telemetry:
        return clamp(telemetry[phase], 0.0, 1.0)
    return clamp(default_value, 0.0, 1.0)


def apply_profile_to_power_model(base: PowerModel, profile_cfg: Dict[str, Any]) -> PowerModel:
    return PowerModel(
        idle_fraction=float(profile_cfg.get("idle_fraction", base.idle_fraction)),
        idle_power_fraction=float(profile_cfg.get("idle_power_fraction", base.idle_power_fraction)),
        nvlink_watts_per_gpu=float(profile_cfg.get("nvlink_watts_per_gpu", base.nvlink_watts_per_gpu)),
        cpu_watts_per_gpu=float(profile_cfg.get("cpu_watts_per_gpu", base.cpu_watts_per_gpu)),
        memory_watts_per_gpu=float(profile_cfg.get("memory_watts_per_gpu", base.memory_watts_per_gpu)),
        power_cap_factor=float(profile_cfg.get("power_cap_factor", base.power_cap_factor)),
        thermal_throttle_factor=float(profile_cfg.get("thermal_throttle_factor", base.thermal_throttle_factor)),
        network_overhead_pct=float(profile_cfg.get("network_overhead_pct", base.network_overhead_pct)),
    )


def phase_cost_gpu_hours(
    hours: float,
    num_gpus: int,
    tdp_watts: float,
    rho: float,
    pue: float,
    ci_g_per_kwh: float,
    electricity_usd_per_kwh: float,
    gpu_usd_per_hour: float,
    carbon_usd_per_ton: float,
    power_model: PowerModel,
) -> Dict[str, float]:
    if hours <= 0.0:
        return {
            "hours_requested": hours,
            "effective_hours": 0.0,
            "energy_kwh": 0.0,
            "co2e_kg": 0.0,
            "electricity_cost_usd": 0.0,
            "gpu_cost_usd": 0.0,
            "carbon_cost_usd": 0.0,
            "total_cost_usd": 0.0,
        }

    cap = clamp(power_model.power_cap_factor, 0.1, 1.0)
    throttle = clamp(power_model.thermal_throttle_factor, 0.1, 1.0)
    throughput = cap * throttle
    effective_hours = hours / throughput
    idle_fraction = clamp(power_model.idle_fraction, 0.0, 0.95)

    active_hours = effective_hours * (1.0 - idle_fraction)
    idle_hours = effective_hours * idle_fraction
    active_gpu_kw = (rho * tdp_watts * cap * throttle) / 1000.0
    idle_gpu_kw = (clamp(power_model.idle_power_fraction, 0.0, 1.0) * tdp_watts) / 1000.0
    overhead_kw = (
        power_model.nvlink_watts_per_gpu
        + power_model.cpu_watts_per_gpu
        + power_model.memory_watts_per_gpu
    ) / 1000.0

    energy_active = num_gpus * active_gpu_kw * active_hours
    energy_idle = num_gpus * idle_gpu_kw * idle_hours
    energy_overhead = num_gpus * overhead_kw * effective_hours

    energy_kwh = (energy_active + energy_idle + energy_overhead) * pue
    energy_kwh *= 1.0 + clamp(power_model.network_overhead_pct, 0.0, 2.0)

    co2e_kg = energy_kwh * (ci_g_per_kwh / 1000.0)
    electricity_cost_usd = energy_kwh * electricity_usd_per_kwh
    gpu_cost_usd = num_gpus * gpu_usd_per_hour * effective_hours
    carbon_cost_usd = (co2e_kg / 1000.0) * carbon_usd_per_ton
    total_cost_usd = electricity_cost_usd + gpu_cost_usd + carbon_cost_usd

    return {
        "hours_requested": hours,
        "effective_hours": effective_hours,
        "energy_kwh": energy_kwh,
        "co2e_kg": co2e_kg,
        "electricity_cost_usd": electricity_cost_usd,
        "gpu_cost_usd": gpu_cost_usd,
        "carbon_cost_usd": carbon_cost_usd,
        "total_cost_usd": total_cost_usd,
    }


def phase_cost_request_based(
    request_cfg: RequestInferenceModel,
    pue: float,
    ci_g_per_kwh: float,
    electricity_usd_per_kwh: float,
    carbon_usd_per_ton: float,
    db: Dict[str, Any],
) -> Dict[str, float]:
    if request_cfg.strategy == "kwh_per_1k_tokens":
        if request_cfg.total_tokens is None or request_cfg.kwh_per_1k_tokens is None:
            raise ValueError("Request mode 'kwh_per_1k_tokens' requires total_tokens and kwh_per_1k_tokens.")
        total_tokens = float(request_cfg.total_tokens)
        base_energy_kwh = (total_tokens / 1000.0) * float(request_cfg.kwh_per_1k_tokens) * pue
    elif request_cfg.strategy == "linear_tokens":
        required = (
            request_cfg.requests,
            request_cfg.avg_input_tokens,
            request_cfg.avg_output_tokens,
            request_cfg.coef_a_kwh_per_input_token,
            request_cfg.coef_b_kwh_per_output_token,
        )
        if any(v is None for v in required):
            raise ValueError("Linear request mode requires requests, avg_input_tokens, avg_output_tokens, coef_a, coef_b.")
        total_tokens = float(request_cfg.requests) * (
            float(request_cfg.avg_input_tokens) + float(request_cfg.avg_output_tokens)
        )
        per_request_kwh = (
            float(request_cfg.coef_a_kwh_per_input_token) * float(request_cfg.avg_input_tokens)
            + float(request_cfg.coef_b_kwh_per_output_token) * float(request_cfg.avg_output_tokens)
        )
        base_energy_kwh = float(request_cfg.requests) * per_request_kwh * pue
    else:
        raise ValueError(f"Unknown request inference strategy '{request_cfg.strategy}'.")

    family_factor = float(db.get("model_family_factors", {}).get(request_cfg.model_family, 1.0))
    context_factor = 1.0 + max(0, request_cfg.context_length_tokens - 4096) / 4096.0 * 0.03
    kv_factor = 1.0 - 0.35 * clamp(request_cfg.kv_cache_hit_rate, 0.0, 1.0)

    multiplier = (
        family_factor
        * context_factor
        * kv_factor
        * float(request_cfg.quantization_factor)
        * float(request_cfg.batch_efficiency_factor)
        * float(request_cfg.streaming_overhead_factor)
    )

    energy_kwh = base_energy_kwh * multiplier
    co2e_kg = energy_kwh * (ci_g_per_kwh / 1000.0)
    electricity_cost_usd = energy_kwh * electricity_usd_per_kwh
    gpu_cost_usd = 0.0
    if request_cfg.gpu_cost_usd_per_1k_tokens is not None:
        gpu_cost_usd = (total_tokens / 1000.0) * float(request_cfg.gpu_cost_usd_per_1k_tokens)
    carbon_cost_usd = (co2e_kg / 1000.0) * carbon_usd_per_ton
    total_cost_usd = electricity_cost_usd + gpu_cost_usd + carbon_cost_usd

    return {
        "hours_requested": request_cfg.ci_window_hours,
        "effective_hours": request_cfg.ci_window_hours,
        "energy_kwh": energy_kwh,
        "co2e_kg": co2e_kg,
        "electricity_cost_usd": electricity_cost_usd,
        "gpu_cost_usd": gpu_cost_usd,
        "carbon_cost_usd": carbon_cost_usd,
        "total_cost_usd": total_cost_usd,
        "tokens_total": total_tokens,
    }


def tflops_metrics(total_tflops: Optional[float], energy_kwh: float) -> Optional[Dict[str, float]]:
    if total_tflops is None or total_tflops < 0:
        return None
    if energy_kwh <= 0:
        return {"total_tflops": total_tflops, "tflops_per_kwh": 0.0}
    return {"total_tflops": total_tflops, "tflops_per_kwh": total_tflops / energy_kwh}


def percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = (len(sorted_values) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def uncertainty_ranges(
    total_energy_kwh: float,
    total_co2e_kg: float,
    total_cost_usd: float,
    uncertainty_cfg: Dict[str, float],
) -> Dict[str, Dict[str, float]]:
    rho_pct = float(uncertainty_cfg.get("rho_pct", 0.0))
    pue_pct = float(uncertainty_cfg.get("pue_pct", 0.0))
    ci_pct = float(uncertainty_cfg.get("ci_pct", 0.0))
    electricity_pct = float(uncertainty_cfg.get("electricity_price_pct", 0.0))
    request_pct = float(uncertainty_cfg.get("request_energy_pct", 0.0))

    energy_pct = rho_pct + pue_pct + request_pct
    co2_pct = energy_pct + ci_pct
    cost_pct = energy_pct + max(ci_pct, electricity_pct)

    def bounds(value: float, pct: float) -> Dict[str, float]:
        return {"low": max(0.0, value * (1.0 - pct)), "high": value * (1.0 + pct)}

    return {
        "energy_kwh": bounds(total_energy_kwh, energy_pct),
        "co2e_kg": bounds(total_co2e_kg, co2_pct),
        "total_cost_usd": bounds(total_cost_usd, cost_pct),
    }


def monte_carlo(
    iterations: int,
    base_total_cost: float,
    base_total_co2e: float,
    uncertainty_cfg: Dict[str, float],
) -> Dict[str, Any]:
    rng = random.Random(42)
    rho_pct = float(uncertainty_cfg.get("rho_pct", 0.0))
    pue_pct = float(uncertainty_cfg.get("pue_pct", 0.0))
    ci_pct = float(uncertainty_cfg.get("ci_pct", 0.0))
    electricity_pct = float(uncertainty_cfg.get("electricity_price_pct", 0.0))
    embodied_pct = float(uncertainty_cfg.get("embodied_pct", 0.0))
    request_pct = float(uncertainty_cfg.get("request_energy_pct", 0.0))

    cost_scale = rho_pct + pue_pct + max(ci_pct, electricity_pct) + request_pct
    co2_scale = rho_pct + pue_pct + ci_pct + embodied_pct + request_pct

    costs: List[float] = []
    co2s: List[float] = []
    for _ in range(iterations):
        sampled_cost = base_total_cost * rng.uniform(max(0.0, 1.0 - cost_scale), 1.0 + cost_scale)
        sampled_co2 = base_total_co2e * rng.uniform(max(0.0, 1.0 - co2_scale), 1.0 + co2_scale)
        costs.append(sampled_cost)
        co2s.append(sampled_co2)
    costs.sort()
    co2s.sort()
    return {
        "iterations": iterations,
        "total_cost_usd": {
            "mean": sum(costs) / len(costs),
            "p10": percentile(costs, 0.10),
            "p50": percentile(costs, 0.50),
            "p90": percentile(costs, 0.90),
        },
        "total_co2e_kg": {
            "mean": sum(co2s) / len(co2s),
            "p10": percentile(co2s, 0.10),
            "p50": percentile(co2s, 0.50),
            "p90": percentile(co2s, 0.90),
        },
    }


def compute(
    inp: UserInput,
    db: Dict[str, Any],
    ci_train: float,
    ci_infer: float,
    rho_training: float,
    rho_inference: float,
) -> Dict[str, Any]:
    defaults = db["defaults"]
    gpu_spec = db["gpu_specs"].get(inp.gpu_model)
    if not gpu_spec:
        raise ValueError(f"Unknown GPU model '{inp.gpu_model}'.")

    tdp_watts = float(gpu_spec["tdp_watts"])
    country = inp.country
    pue = float(db["pue_by_country"].get(country, defaults["pue_default"]))
    electricity_price = float(
        db["electricity_price_usd_per_kwh_by_country"].get(country, defaults["electricity_price_usd_per_kwh"])
    )
    carbon_price = float(defaults["carbon_price_usd_per_ton_co2e"])
    gpu_hourly_price = float(gpu_spec["hourly_price_usd"])
    if inp.gpu_hourly_price_override is not None:
        gpu_hourly_price = float(inp.gpu_hourly_price_override)

    training_profile_cfg = db.get("training_profiles", {}).get(inp.training_profile, {})
    inference_profile_cfg = db.get("inference_profiles", {}).get(inp.inference_profile, {})
    training_power_model = apply_profile_to_power_model(inp.power_model, training_profile_cfg)
    inference_power_model = apply_profile_to_power_model(inp.power_model, inference_profile_cfg)

    training = phase_cost_gpu_hours(
        hours=inp.training_hours,
        num_gpus=inp.num_gpus,
        tdp_watts=tdp_watts,
        rho=rho_training,
        pue=pue,
        ci_g_per_kwh=ci_train,
        electricity_usd_per_kwh=electricity_price,
        gpu_usd_per_hour=gpu_hourly_price,
        carbon_usd_per_ton=carbon_price,
        power_model=training_power_model,
    )

    if inp.inference_mode == "request_based":
        if inp.request_inference is None:
            raise ValueError("inference_mode=request_based requires request_inference config.")
        request_cfg = inp.request_inference
        profile_request_defaults = inference_profile_cfg.get("request_defaults", {})
        if request_cfg.kwh_per_1k_tokens is None and "kwh_per_1k_tokens" in profile_request_defaults:
            request_cfg.kwh_per_1k_tokens = float(profile_request_defaults["kwh_per_1k_tokens"])
        if request_cfg.gpu_cost_usd_per_1k_tokens is None and "gpu_cost_usd_per_1k_tokens" in profile_request_defaults:
            request_cfg.gpu_cost_usd_per_1k_tokens = float(profile_request_defaults["gpu_cost_usd_per_1k_tokens"])
        if request_cfg.coef_a_kwh_per_input_token is None and "coef_a_kwh_per_input_token" in profile_request_defaults:
            request_cfg.coef_a_kwh_per_input_token = float(profile_request_defaults["coef_a_kwh_per_input_token"])
        if request_cfg.coef_b_kwh_per_output_token is None and "coef_b_kwh_per_output_token" in profile_request_defaults:
            request_cfg.coef_b_kwh_per_output_token = float(profile_request_defaults["coef_b_kwh_per_output_token"])
        inference = phase_cost_request_based(
            request_cfg=request_cfg,
            pue=pue,
            ci_g_per_kwh=ci_infer,
            electricity_usd_per_kwh=electricity_price,
            carbon_usd_per_ton=carbon_price,
            db=db,
        )
    else:
        inference = phase_cost_gpu_hours(
            hours=inp.inference_hours,
            num_gpus=inp.num_gpus,
            tdp_watts=tdp_watts,
            rho=rho_inference,
            pue=pue,
            ci_g_per_kwh=ci_infer,
            electricity_usd_per_kwh=electricity_price,
            gpu_usd_per_hour=gpu_hourly_price,
            carbon_usd_per_ton=carbon_price,
            power_model=inference_power_model,
        )

    embodied = {"co2e_kg": 0.0, "carbon_cost_usd": 0.0, "total_cost_usd": 0.0}
    if inp.include_embodied:
        embodied_kgco2e = gpu_spec.get("embodied_kgco2e")
        lifetime_hours = gpu_spec.get("expected_lifetime_hours")
        if embodied_kgco2e is not None and lifetime_hours is not None and float(lifetime_hours) > 0:
            used_hours = training["effective_hours"] + inference["effective_hours"]
            embodied_kg = inp.num_gpus * float(embodied_kgco2e) * (used_hours / float(lifetime_hours))
            embodied_cost = (embodied_kg / 1000.0) * carbon_price
            embodied = {
                "co2e_kg": embodied_kg,
                "carbon_cost_usd": embodied_cost,
                "total_cost_usd": embodied_cost,
            }

    total_energy = training["energy_kwh"] + inference["energy_kwh"]
    total_co2e = training["co2e_kg"] + inference["co2e_kg"] + embodied["co2e_kg"]
    total_cost = training["total_cost_usd"] + inference["total_cost_usd"] + embodied["total_cost_usd"]

    training_eff = tflops_metrics(inp.training_tflops, training["energy_kwh"])
    inference_eff = tflops_metrics(inp.inference_tflops, inference["energy_kwh"])
    total_tflops = None
    if inp.training_tflops is not None or inp.inference_tflops is not None:
        total_tflops = float(inp.training_tflops or 0.0) + float(inp.inference_tflops or 0.0)
    total_eff = tflops_metrics(total_tflops, total_energy)

    uncertainty_cfg = db.get("uncertainty", {})
    ranges = uncertainty_ranges(total_energy, total_co2e, total_cost, uncertainty_cfg)
    mc = None
    if inp.run_monte_carlo:
        mc = monte_carlo(inp.monte_carlo_iterations, total_cost, total_co2e, uncertainty_cfg)

    return {
        "meta": {
            "country": country,
            "zone": resolve_zone(country, db),
            "ci_source": inp.ci_source,
            "ci_mode": inp.ci_mode,
            "ci_training_g_per_kwh": ci_train,
            "ci_inference_g_per_kwh": ci_infer,
            "gpu_model": inp.gpu_model,
            "num_gpus": inp.num_gpus,
            "training_profile": inp.training_profile,
            "inference_profile": inp.inference_profile,
            "inference_mode": inp.inference_mode,
            "rho_training": rho_training,
            "rho_inference": rho_inference,
            "pue": pue,
            "electricity_price_usd_per_kwh": electricity_price,
            "gpu_hourly_price_usd": gpu_hourly_price,
            "carbon_price_usd_per_ton_co2e": carbon_price,
            "cloud_provider": inp.cloud_provider,
            "cloud_instance": inp.cloud_instance,
            "instance_count": inp.instance_count,
        },
        "phases": {
            "training": training,
            "inference": inference,
            "embodied": embodied,
        },
        "efficiency": {
            "training": training_eff,
            "inference": inference_eff,
            "total": total_eff,
        },
        "uncertainty_ranges": ranges,
        "monte_carlo": mc,
        "total": {
            "energy_kwh": total_energy,
            "co2e_kg": total_co2e,
            "total_cost_usd": total_cost,
        },
    }
