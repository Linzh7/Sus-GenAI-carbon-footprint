import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ELECTRICITY_MAPS_BASE_URL = "https://api.electricitymap.org/v3"
UK_CARBON_INTENSITY_BASE_URL = "https://api.carbonintensity.org.uk"


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ElectricityMapsClient:
    def __init__(self, api_key: str, timeout_seconds: int = 20) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_latest_ci_g_per_kwh(self, zone: str) -> float:
        payload = self._get_json("/carbon-intensity/latest", {"zone": zone})
        values = self._extract_ci_values(payload)
        if not values:
            raise ValueError(f"Unexpected latest CI response for zone '{zone}': {payload}")
        return values[-1]

    def fetch_avg_ci_g_per_kwh(self, zone: str, start: datetime, end: datetime) -> float:
        if end <= start:
            raise ValueError("Invalid CI window: end must be after start.")

        params = {"zone": zone, "start": _to_utc_iso(start), "end": _to_utc_iso(end)}
        history_endpoints = ("/carbon-intensity/past", "/carbon-intensity/history", "/carbon-intensity/past-range")
        last_error: Optional[Exception] = None

        for endpoint in history_endpoints:
            try:
                payload = self._get_json(endpoint, params)
                values = self._extract_ci_values(payload)
                if values:
                    return sum(values) / len(values)
            except Exception as exc:
                last_error = exc

        if last_error:
            raise RuntimeError(f"Failed to fetch historical CI for zone '{zone}': {last_error}") from last_error
        raise RuntimeError(f"No CI history points found for zone '{zone}'.")

    def _extract_ci_values(self, payload: Any) -> List[float]:
        values: List[float] = []

        def maybe_add(item: Any) -> None:
            if isinstance(item, dict):
                for key in ("carbonIntensity", "carbon_intensity", "intensity"):
                    val = item.get(key)
                    if isinstance(val, (int, float)):
                        values.append(float(val))
                        return

        if isinstance(payload, list):
            for item in payload:
                maybe_add(item)
            return values

        if isinstance(payload, dict):
            for key in ("history", "data", "carbonIntensityHistory", "carbon_intensity_history", "values"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    for item in rows:
                        maybe_add(item)
                    if values:
                        return values
            maybe_add(payload)
        return values

    def _get_json(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{ELECTRICITY_MAPS_BASE_URL}{endpoint}?{urlencode(params)}"
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


class UKCarbonIntensityClient:
    def __init__(self, timeout_seconds: int = 20) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_latest_ci_g_per_kwh(self, country: str) -> float:
        self._validate_country(country)
        payload = self._get_json("/intensity")
        values = self._extract_ci_values(payload)
        if not values:
            raise RuntimeError("No UK CI points in latest response.")
        return values[-1]

    def fetch_avg_ci_g_per_kwh(self, country: str, start: datetime, end: datetime) -> float:
        self._validate_country(country)
        if end <= start:
            raise ValueError("Invalid CI window: end must be after start.")
        start_token = quote(start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), safe="")
        end_token = quote(end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), safe="")
        payload = self._get_json(f"/intensity/{start_token}/{end_token}")
        values = self._extract_ci_values(payload)
        if not values:
            raise RuntimeError("No UK CI points in historical response.")
        return sum(values) / len(values)

    def _validate_country(self, country: str) -> None:
        if country not in {"GB", "UK"}:
            raise ValueError("UK National Grid API can only be used for GB/UK.")

    def _extract_ci_values(self, payload: Any) -> List[float]:
        values: List[float] = []
        if not isinstance(payload, dict):
            return values
        rows = payload.get("data")
        if not isinstance(rows, list):
            return values
        for row in rows:
            if not isinstance(row, dict):
                continue
            intensity = row.get("intensity")
            if not isinstance(intensity, dict):
                continue
            actual = intensity.get("actual")
            forecast = intensity.get("forecast")
            if isinstance(actual, (int, float)):
                values.append(float(actual))
            elif isinstance(forecast, (int, float)):
                values.append(float(forecast))
        return values

    def _get_json(self, endpoint: str) -> Dict[str, Any]:
        req = Request(
            url=f"{UK_CARBON_INTENSITY_BASE_URL}{endpoint}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.load(resp)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"UK Grid HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Failed to reach UK Grid API: {exc.reason}") from exc
