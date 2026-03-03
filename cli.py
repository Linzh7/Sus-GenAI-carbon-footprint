import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from carbon_clients import ElectricityMapsClient, UKCarbonIntensityClient
from estimator import choose_rho, load_db, load_telemetry_rho, normalize_country, resolve_zone, clamp, compute
from models import PowerModel, RequestInferenceModel, UserInput


DB_PATH = Path(__file__).with_name("data.json")


def prompt_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty.")


def prompt_int(prompt: str, min_value: int = 1) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            value = int(raw)
            if value < min_value:
                raise ValueError
            return value
        except ValueError:
            print(f"Please enter an integer >= {min_value}.")


def prompt_float(prompt: str, min_value: float = 0.0) -> float:
    while True:
        raw = input(prompt).strip()
        try:
            value = float(raw)
            if value < min_value:
                raise ValueError
            return value
        except ValueError:
            print(f"Please enter a number >= {min_value}.")


def prompt_optional_float(prompt: str, min_value: float = 0.0) -> Optional[float]:
    while True:
        raw = input(prompt).strip()
        if raw == "":
            return None
        try:
            value = float(raw)
            if value < min_value:
                raise ValueError
            return value
        except ValueError:
            print(f"Please enter a number >= {min_value}, or Enter to skip.")


def prompt_optional_text(prompt: str) -> Optional[str]:
    value = input(prompt).strip()
    return value if value else None


def prompt_yes_no(prompt: str) -> bool:
    while True:
        raw = input(prompt).strip().lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please enter 'y' or 'n'.")


def prompt_choice(prompt: str, options: Dict[str, str]) -> str:
    while True:
        raw = input(prompt).strip().lower()
        if raw in options:
            return options[raw]
        print(f"Please choose one of: {', '.join(sorted(options.keys()))}")


def prompt_datetime_utc(prompt: str) -> datetime:
    while True:
        raw = input(prompt).strip()
        if raw == "":
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            print("Use ISO format, for example 2026-03-03T12:00:00Z")


def choose_profile(name: str, profiles: Dict[str, Any], default_profile: str) -> str:
    keys = sorted(profiles.keys())
    if not keys:
        return default_profile
    print(f"Available {name} profiles:")
    for idx, key in enumerate(keys, start=1):
        print(f"  {idx}. {key}")
    raw = input(f"Select {name} profile number (Enter for {default_profile}): ").strip()
    if not raw:
        return default_profile if default_profile in profiles else keys[0]
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(keys):
            return keys[idx - 1]
    if raw in profiles:
        return raw
    print(f"Unknown profile '{raw}', using default.")
    return default_profile if default_profile in profiles else keys[0]


def select_gpu_from_catalog(db: Dict[str, Any]) -> Tuple[str, int, Optional[float], Optional[str], Optional[str], Optional[int]]:
    cloud_map = db.get("cloud_instance_mapping", {})
    if cloud_map and prompt_yes_no("Use cloud instance mapping (AWS/GCP/Azure)? (y/n): "):
        providers = sorted(cloud_map.keys())
        for idx, provider in enumerate(providers, start=1):
            print(f"  {idx}. {provider}")
        provider_idx = prompt_int("Provider number: ", min_value=1)
        if provider_idx > len(providers):
            raise ValueError("Invalid provider number.")
        provider = providers[provider_idx - 1]

        instances = sorted(cloud_map[provider].keys())
        for idx, instance in enumerate(instances, start=1):
            print(f"  {idx}. {instance}")
        instance_idx = prompt_int("Instance number: ", min_value=1)
        if instance_idx > len(instances):
            raise ValueError("Invalid instance number.")
        instance = instances[instance_idx - 1]
        count = prompt_int("Number of instances: ", min_value=1)
        inst_cfg = cloud_map[provider][instance]
        gpu_model = inst_cfg["gpu_model"]
        gpus_per_instance = int(inst_cfg["gpus_per_instance"])
        gpu_hourly_override = float(inst_cfg["hourly_price_usd"]) / max(1, gpus_per_instance)
        return gpu_model, count * gpus_per_instance, gpu_hourly_override, provider, instance, count

    models = sorted(db["gpu_specs"].keys())
    print("Available GPU models:")
    for idx, model in enumerate(models, start=1):
        print(f"  {idx}. {model}")
    while True:
        selected = prompt_non_empty("GPU model (exact name or number): ")
        if selected.isdigit():
            idx = int(selected)
            if 1 <= idx <= len(models):
                gpu_model = models[idx - 1]
                break
        elif selected in db["gpu_specs"]:
            gpu_model = selected
            break
        print("Unknown model.")
    num_gpus = prompt_int("Number of GPUs: ", min_value=1)
    return gpu_model, num_gpus, None, None, None, None


def gather_power_model(db: Dict[str, Any]) -> PowerModel:
    defaults = db.get("power_model_defaults", {})
    power = PowerModel(
        idle_fraction=float(defaults.get("idle_fraction", 0.05)),
        idle_power_fraction=float(defaults.get("idle_power_fraction", 0.25)),
        nvlink_watts_per_gpu=float(defaults.get("nvlink_watts_per_gpu", 0.0)),
        cpu_watts_per_gpu=float(defaults.get("cpu_watts_per_gpu", 40.0)),
        memory_watts_per_gpu=float(defaults.get("memory_watts_per_gpu", 20.0)),
        power_cap_factor=float(defaults.get("power_cap_factor", 1.0)),
        thermal_throttle_factor=float(defaults.get("thermal_throttle_factor", 1.0)),
        network_overhead_pct=float(defaults.get("network_overhead_pct", 0.0)),
    )
    if not prompt_yes_no("Customize advanced power model? (y/n): "):
        return power

    idle_fraction = prompt_optional_float(f"Idle fraction [default {power.idle_fraction}]: ", min_value=0.0)
    idle_power_fraction = prompt_optional_float(
        f"Idle GPU power fraction [default {power.idle_power_fraction}]: ",
        min_value=0.0,
    )
    nvlink = prompt_optional_float(f"NVLink/InfiniBand watts per GPU [default {power.nvlink_watts_per_gpu}]: ", min_value=0.0)
    cpu = prompt_optional_float(f"CPU watts per GPU [default {power.cpu_watts_per_gpu}]: ", min_value=0.0)
    memory = prompt_optional_float(f"Memory watts per GPU [default {power.memory_watts_per_gpu}]: ", min_value=0.0)
    cap = prompt_optional_float(f"Power cap factor 0-1 [default {power.power_cap_factor}]: ", min_value=0.0)
    throttle = prompt_optional_float(
        f"Thermal throttle factor 0-1 [default {power.thermal_throttle_factor}]: ",
        min_value=0.0,
    )
    network = prompt_optional_float(
        f"Network overhead pct as decimal [default {power.network_overhead_pct}]: ",
        min_value=0.0,
    )

    if idle_fraction is not None:
        power.idle_fraction = clamp(idle_fraction, 0.0, 0.95)
    if idle_power_fraction is not None:
        power.idle_power_fraction = clamp(idle_power_fraction, 0.0, 1.0)
    if nvlink is not None:
        power.nvlink_watts_per_gpu = nvlink
    if cpu is not None:
        power.cpu_watts_per_gpu = cpu
    if memory is not None:
        power.memory_watts_per_gpu = memory
    if cap is not None:
        power.power_cap_factor = clamp(cap, 0.1, 1.0)
    if throttle is not None:
        power.thermal_throttle_factor = clamp(throttle, 0.1, 1.0)
    if network is not None:
        power.network_overhead_pct = clamp(network, 0.0, 2.0)
    return power


def gather_request_inference(inference_profile_cfg: Dict[str, Any]) -> RequestInferenceModel:
    strategy = prompt_choice(
        "Request-based strategy [1=kwh_per_1k_tokens, 2=linear_tokens]: ",
        {"1": "kwh_per_1k_tokens", "2": "linear_tokens"},
    )
    ci_window = prompt_float("Inference CI averaging window hours (for history mode): ", min_value=0.1)

    request_defaults = inference_profile_cfg.get("request_defaults", {})
    default_quant = float(request_defaults.get("quantization_factor", 1.0))
    default_batch = float(request_defaults.get("batch_efficiency_factor", 1.0))
    default_stream = float(request_defaults.get("streaming_overhead_factor", 1.0))
    default_family = str(request_defaults.get("model_family", "dense"))

    quant = prompt_optional_float(f"Quantization factor [default {default_quant}]: ", min_value=0.1)
    batch = prompt_optional_float(f"Batch efficiency factor [default {default_batch}]: ", min_value=0.1)
    stream = prompt_optional_float(f"Streaming overhead factor [default {default_stream}]: ", min_value=0.1)
    context_len = int(prompt_optional_float("Context length tokens [default 4096]: ", min_value=1.0) or 4096)
    kv_hit = prompt_optional_float("KV cache hit rate 0-1 [default 0]: ", min_value=0.0) or 0.0
    family = prompt_optional_text(f"Model family [default {default_family}]: ") or default_family

    if strategy == "kwh_per_1k_tokens":
        total_tokens = prompt_float("Total generated+processed tokens: ", min_value=1.0)
        default_kwh = request_defaults.get("kwh_per_1k_tokens")
        kwh_per_1k = prompt_optional_float(
            f"kWh per 1k tokens [default {default_kwh if default_kwh is not None else 'required'}]: ",
            min_value=0.0000001,
        )
        if kwh_per_1k is None:
            if default_kwh is None:
                raise ValueError("kWh per 1k tokens is required for this strategy.")
            kwh_per_1k = float(default_kwh)
        gpu_cost = prompt_optional_float(
            "GPU USD per 1k tokens (optional, Enter to skip): ",
            min_value=0.0,
        )
        return RequestInferenceModel(
            strategy=strategy,
            ci_window_hours=ci_window,
            total_tokens=total_tokens,
            kwh_per_1k_tokens=kwh_per_1k,
            gpu_cost_usd_per_1k_tokens=gpu_cost,
            quantization_factor=quant or default_quant,
            batch_efficiency_factor=batch or default_batch,
            streaming_overhead_factor=stream or default_stream,
            context_length_tokens=context_len,
            kv_cache_hit_rate=clamp(kv_hit, 0.0, 1.0),
            model_family=family,
        )

    requests = prompt_int("Number of requests: ", min_value=1)
    avg_in = prompt_float("Average input tokens per request: ", min_value=1.0)
    avg_out = prompt_float("Average output tokens per request: ", min_value=1.0)
    default_a = request_defaults.get("coef_a_kwh_per_input_token")
    default_b = request_defaults.get("coef_b_kwh_per_output_token")
    coef_a = prompt_optional_float(
        f"Coefficient a (kWh per input token) [default {default_a if default_a is not None else 'required'}]: ",
        min_value=0.0,
    )
    coef_b = prompt_optional_float(
        f"Coefficient b (kWh per output token) [default {default_b if default_b is not None else 'required'}]: ",
        min_value=0.0,
    )
    if coef_a is None:
        if default_a is None:
            raise ValueError("Coefficient a is required for linear token mode.")
        coef_a = float(default_a)
    if coef_b is None:
        if default_b is None:
            raise ValueError("Coefficient b is required for linear token mode.")
        coef_b = float(default_b)

    gpu_cost = prompt_optional_float(
        "GPU USD per 1k tokens (optional, Enter to skip): ",
        min_value=0.0,
    )
    return RequestInferenceModel(
        strategy=strategy,
        ci_window_hours=ci_window,
        requests=requests,
        avg_input_tokens=avg_in,
        avg_output_tokens=avg_out,
        coef_a_kwh_per_input_token=coef_a,
        coef_b_kwh_per_output_token=coef_b,
        gpu_cost_usd_per_1k_tokens=gpu_cost,
        quantization_factor=quant or default_quant,
        batch_efficiency_factor=batch or default_batch,
        streaming_overhead_factor=stream or default_stream,
        context_length_tokens=context_len,
        kv_cache_hit_rate=clamp(kv_hit, 0.0, 1.0),
        model_family=family,
    )


def gather_user_input(db: Dict[str, Any]) -> UserInput:
    gpu_model, num_gpus, gpu_hourly_override, provider, instance, instance_count = select_gpu_from_catalog(db)
    country = normalize_country(prompt_non_empty("Country/location code (US, DE, GB, ...): "), db)

    ci_source = prompt_choice(
        "CI source [1=electricity_maps, 2=uk_national_grid]: ",
        {"1": "electricity_maps", "2": "uk_national_grid"},
    )
    ci_mode = prompt_choice(
        "CI mode [1=latest, 2=time_resolved]: ",
        {"1": "latest", "2": "time_resolved"},
    )
    start_time = prompt_datetime_utc("Workload start time UTC (ISO, Enter for now): ")

    training_profiles = db.get("training_profiles", {})
    inference_profiles = db.get("inference_profiles", {})
    training_profile = choose_profile("training", training_profiles, "pretraining_dense")
    inference_profile = choose_profile("inference", inference_profiles, "online_low_batch")

    training_hours = prompt_float("Training duration hours: ", min_value=0.0)
    inference_mode = prompt_choice(
        "Inference mode [1=gpu_hours, 2=request_based]: ",
        {"1": "gpu_hours", "2": "request_based"},
    )

    request_inference = None
    if inference_mode == "request_based":
        inference_hours = 0.0
        request_inference = gather_request_inference(inference_profiles.get(inference_profile, {}))
    else:
        inference_hours = prompt_float("Inference duration hours: ", min_value=0.0)

    include_embodied = prompt_yes_no("Include embodied emissions? (y/n): ")
    rho_train = prompt_optional_float("Override training rho (0-1, Enter to skip): ", min_value=0.0)
    rho_infer = prompt_optional_float("Override inference rho (0-1, Enter to skip): ", min_value=0.0)
    telemetry = prompt_optional_text("Telemetry file path for rho calibration (CSV/JSON, Enter to skip): ")
    training_tflops = prompt_optional_float("Training total TFLOPs (Enter to skip): ", min_value=0.0)
    inference_tflops = prompt_optional_float("Inference total TFLOPs (Enter to skip): ", min_value=0.0)
    run_mc = prompt_yes_no("Run Monte Carlo uncertainty? (y/n): ")
    mc_iter = prompt_int("Monte Carlo iterations: ", min_value=10) if run_mc else 0
    power_model = gather_power_model(db)

    return UserInput(
        gpu_model=gpu_model,
        num_gpus=num_gpus,
        country=country,
        training_hours=training_hours,
        inference_hours=inference_hours,
        include_embodied=include_embodied,
        ci_source=ci_source,
        ci_mode=ci_mode,
        start_time_utc=start_time,
        training_profile=training_profile,
        inference_profile=inference_profile,
        inference_mode=inference_mode,
        request_inference=request_inference,
        rho_training_override=rho_train,
        rho_inference_override=rho_infer,
        telemetry_path=telemetry,
        training_tflops=training_tflops,
        inference_tflops=inference_tflops,
        run_monte_carlo=run_mc,
        monte_carlo_iterations=mc_iter,
        power_model=power_model,
        cloud_provider=provider,
        cloud_instance=instance,
        instance_count=instance_count,
        gpu_hourly_price_override=gpu_hourly_override,
    )


def fetch_phase_ci(inp: UserInput, db: Dict[str, Any]) -> Tuple[float, float]:
    train_start = inp.start_time_utc
    train_end = train_start + timedelta(hours=inp.training_hours)
    infer_hours_for_ci = inp.inference_hours
    if inp.inference_mode == "request_based" and inp.request_inference is not None:
        infer_hours_for_ci = inp.request_inference.ci_window_hours
    infer_end = train_end + timedelta(hours=infer_hours_for_ci)

    if inp.ci_source == "electricity_maps":
        api_key = os.environ.get("ELECTRICITY_MAPS_API_KEY")
        if not api_key:
            raise RuntimeError("Set ELECTRICITY_MAPS_API_KEY for Electricity Maps mode.")
        zone = resolve_zone(inp.country, db)
        client = ElectricityMapsClient(api_key)
        if inp.ci_mode == "latest":
            ci = client.fetch_latest_ci_g_per_kwh(zone)
            return ci, ci
        train_ci = client.fetch_avg_ci_g_per_kwh(zone, train_start, train_end) if inp.training_hours > 0 else 0.0
        infer_ci = client.fetch_avg_ci_g_per_kwh(zone, train_end, infer_end) if infer_hours_for_ci > 0 else train_ci
        return train_ci, infer_ci

    if inp.ci_source == "uk_national_grid":
        client = UKCarbonIntensityClient()
        if inp.ci_mode == "latest":
            ci = client.fetch_latest_ci_g_per_kwh(inp.country)
            return ci, ci
        train_ci = client.fetch_avg_ci_g_per_kwh(inp.country, train_start, train_end) if inp.training_hours > 0 else 0.0
        infer_ci = client.fetch_avg_ci_g_per_kwh(inp.country, train_end, infer_end) if infer_hours_for_ci > 0 else train_ci
        return train_ci, infer_ci

    raise ValueError(f"Unknown ci_source '{inp.ci_source}'")


def run() -> None:
    db = load_db(DB_PATH)
    user_input = gather_user_input(db)

    gpu_spec = db["gpu_specs"][user_input.gpu_model]
    telemetry: Dict[str, float] = {}
    if user_input.telemetry_path:
        telemetry = load_telemetry_rho(user_input.telemetry_path, float(gpu_spec["tdp_watts"]))

    defaults = db["defaults"]
    training_profile_cfg = db.get("training_profiles", {}).get(user_input.training_profile, {})
    inference_profile_cfg = db.get("inference_profiles", {}).get(user_input.inference_profile, {})
    rho_train_default = float(training_profile_cfg.get("rho", defaults["rho_training"]))
    rho_infer_default = float(inference_profile_cfg.get("rho", defaults["rho_inference"]))

    rho_training = choose_rho(user_input.rho_training_override, telemetry, "training", rho_train_default)
    rho_inference = choose_rho(user_input.rho_inference_override, telemetry, "inference", rho_infer_default)
    ci_train, ci_infer = fetch_phase_ci(user_input, db)
    result = compute(
        inp=user_input,
        db=db,
        ci_train=ci_train,
        ci_infer=ci_infer,
        rho_training=rho_training,
        rho_inference=rho_inference,
    )
    result["rho_sources"] = {
        "training": "override" if user_input.rho_training_override is not None else ("telemetry" if "training" in telemetry else "profile/default"),
        "inference": "override" if user_input.rho_inference_override is not None else ("telemetry" if "inference" in telemetry else "profile/default"),
    }
    print(json.dumps(result, indent=2))
