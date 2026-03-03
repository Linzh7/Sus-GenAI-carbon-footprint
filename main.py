import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DB_PATH = Path(__file__).with_name("data.json")
ELECTRICITY_MAPS_BASE_URL = "https://api.electricitymap.org/v3"


@dataclass
class UserInput:
    gpu_model: str
    num_gpus: int
    country: str
    training_hours: float
    inference_hours: float
    include_embodied: bool


class ElectricityMapsClient:
    def __init__(self, api_key: str, timeout_seconds: int = 20) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_latest_ci_g_per_kwh(self, zone: str) -> float:
        payload = self._get_json("/carbon-intensity/latest", {"zone": zone})
        for key in ("carbonIntensity", "carbon_intensity"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        raise ValueError(f"Unexpected Electricity Maps response for zone '{zone}': {payload}")

    def _get_json(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        query = urlencode(params)
        url = f"{ELECTRICITY_MAPS_BASE_URL}{endpoint}?{query}"
        req = Request(
            url=url,
            headers={"auth-token": self.api_key, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.load(resp)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Electricity Maps HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Failed to reach Electricity Maps: {exc.reason}") from exc


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


def phase_cost(
    hours: float,
    num_gpus: int,
    tdp_watts: float,
    rho: float,
    pue: float,
    ci_g_per_kwh: float,
    electricity_usd_per_kwh: float,
    gpu_usd_per_hour: float,
    carbon_usd_per_ton: float,
) -> Dict[str, float]:
    power_kw = (rho * tdp_watts) / 1000.0
    energy_kwh = num_gpus * power_kw * hours * pue
    co2e_kg = energy_kwh * (ci_g_per_kwh / 1000.0)
    electricity_cost_usd = energy_kwh * electricity_usd_per_kwh
    gpu_cost_usd = num_gpus * gpu_usd_per_hour * hours
    carbon_cost_usd = (co2e_kg / 1000.0) * carbon_usd_per_ton
    total_cost_usd = electricity_cost_usd + gpu_cost_usd + carbon_cost_usd
    return {
        "hours": hours,
        "energy_kwh": energy_kwh,
        "co2e_kg": co2e_kg,
        "electricity_cost_usd": electricity_cost_usd,
        "gpu_cost_usd": gpu_cost_usd,
        "carbon_cost_usd": carbon_cost_usd,
        "total_cost_usd": total_cost_usd,
    }


def compute_costs(inp: UserInput, db: Dict[str, Any], ci_g_per_kwh: float) -> Dict[str, Any]:
    gpu_spec = db["gpu_specs"].get(inp.gpu_model)
    if not gpu_spec:
        available = ", ".join(sorted(db["gpu_specs"].keys()))
        raise ValueError(f"Unknown GPU model '{inp.gpu_model}'. Available: {available}")

    country = inp.country
    defaults = db["defaults"]
    rho_training = float(defaults["rho_training"])
    rho_inference = float(defaults["rho_inference"])
    pue = float(db["pue_by_country"].get(country, defaults["pue_default"]))
    electricity_price = float(
        db["electricity_price_usd_per_kwh_by_country"].get(country, defaults["electricity_price_usd_per_kwh"])
    )
    carbon_price = float(defaults["carbon_price_usd_per_ton_co2e"])
    tdp_watts = float(gpu_spec["tdp_watts"])
    gpu_hourly_price = float(gpu_spec["hourly_price_usd"])

    training = phase_cost(
        hours=inp.training_hours,
        num_gpus=inp.num_gpus,
        tdp_watts=tdp_watts,
        rho=rho_training,
        pue=pue,
        ci_g_per_kwh=ci_g_per_kwh,
        electricity_usd_per_kwh=electricity_price,
        gpu_usd_per_hour=gpu_hourly_price,
        carbon_usd_per_ton=carbon_price,
    )

    inference = phase_cost(
        hours=inp.inference_hours,
        num_gpus=inp.num_gpus,
        tdp_watts=tdp_watts,
        rho=rho_inference,
        pue=pue,
        ci_g_per_kwh=ci_g_per_kwh,
        electricity_usd_per_kwh=electricity_price,
        gpu_usd_per_hour=gpu_hourly_price,
        carbon_usd_per_ton=carbon_price,
    )

    embodied = {
        "co2e_kg": 0.0,
        "carbon_cost_usd": 0.0,
        "total_cost_usd": 0.0,
    }
    if inp.include_embodied:
        embodied_kgco2e = gpu_spec.get("embodied_kgco2e")
        lifetime_hours = gpu_spec.get("expected_lifetime_hours")
        if embodied_kgco2e is not None and lifetime_hours is not None:
            total_runtime_hours = inp.training_hours + inp.inference_hours
            embodied_kg = (
                inp.num_gpus
                * float(embodied_kgco2e)
                * (total_runtime_hours / float(lifetime_hours))
            )
            embodied_cost = (embodied_kg / 1000.0) * carbon_price
            embodied = {
                "co2e_kg": embodied_kg,
                "carbon_cost_usd": embodied_cost,
                "total_cost_usd": embodied_cost,
            }

    total_energy = training["energy_kwh"] + inference["energy_kwh"]
    total_co2e = training["co2e_kg"] + inference["co2e_kg"] + embodied["co2e_kg"]
    total_cost = training["total_cost_usd"] + inference["total_cost_usd"] + embodied["total_cost_usd"]

    return {
        "meta": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "country": country,
            "zone": resolve_zone(country, db),
            "gpu_model": inp.gpu_model,
            "num_gpus": inp.num_gpus,
            "ci_g_per_kwh": ci_g_per_kwh,
            "rho_training": rho_training,
            "rho_inference": rho_inference,
            "pue": pue,
            "electricity_price_usd_per_kwh": electricity_price,
            "gpu_hourly_price_usd": gpu_hourly_price,
            "carbon_price_usd_per_ton_co2e": carbon_price,
        },
        "phases": {
            "training": training,
            "inference": inference,
            "embodied": embodied,
        },
        "total": {
            "energy_kwh": total_energy,
            "co2e_kg": total_co2e,
            "total_cost_usd": total_cost,
        },
    }


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


def prompt_yes_no(prompt: str) -> bool:
    while True:
        raw = input(prompt).strip().lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please enter 'y' or 'n'.")


def gather_user_input(db: Dict[str, Any]) -> UserInput:
    models = sorted(db["gpu_specs"].keys())
    print("Available GPU models:")
    for idx, model in enumerate(models, start=1):
        print(f"  {idx}. {model}")

    while True:
        selected = prompt_non_empty("GPU model (type exact name or number): ")
        if selected.isdigit():
            model_idx = int(selected)
            if 1 <= model_idx <= len(models):
                gpu_model = models[model_idx - 1]
                break
        elif selected in db["gpu_specs"]:
            gpu_model = selected
            break
        print("Unknown GPU model. Choose a listed number or exact name.")

    num_gpus = prompt_int("Number of GPUs: ", min_value=1)
    raw_country = prompt_non_empty("Country or location code (e.g. US, DE, Finland): ")
    country = normalize_country(raw_country, db)
    training_hours = prompt_float("Training duration (hours): ", min_value=0.0)
    inference_hours = prompt_float("Inference duration (hours, use 0 if none): ", min_value=0.0)
    include_embodied = prompt_yes_no("Include embodied emissions cost? (y/n): ")

    return UserInput(
        gpu_model=gpu_model,
        num_gpus=num_gpus,
        country=country,
        training_hours=training_hours,
        inference_hours=inference_hours,
        include_embodied=include_embodied,
    )


def main() -> None:
    db = load_db(DB_PATH)
    api_key = os.environ.get("ELECTRICITY_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("Set ELECTRICITY_MAPS_API_KEY before running this CLI.")

    user_input = gather_user_input(db)
    zone = resolve_zone(user_input.country, db)
    client = ElectricityMapsClient(api_key=api_key)
    ci = client.fetch_latest_ci_g_per_kwh(zone)

    result = compute_costs(user_input, db, ci_g_per_kwh=ci)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
