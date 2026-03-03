from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class PowerModel:
    idle_fraction: float
    idle_power_fraction: float
    nvlink_watts_per_gpu: float
    cpu_watts_per_gpu: float
    memory_watts_per_gpu: float
    power_cap_factor: float
    thermal_throttle_factor: float
    network_overhead_pct: float


@dataclass
class RequestInferenceModel:
    strategy: str  # kwh_per_1k_tokens | linear_tokens
    ci_window_hours: float
    total_tokens: Optional[float] = None
    kwh_per_1k_tokens: Optional[float] = None
    requests: Optional[int] = None
    avg_input_tokens: Optional[float] = None
    avg_output_tokens: Optional[float] = None
    coef_a_kwh_per_input_token: Optional[float] = None
    coef_b_kwh_per_output_token: Optional[float] = None
    gpu_cost_usd_per_1k_tokens: Optional[float] = None
    quantization_factor: float = 1.0
    batch_efficiency_factor: float = 1.0
    streaming_overhead_factor: float = 1.0
    context_length_tokens: int = 4096
    kv_cache_hit_rate: float = 0.0
    model_family: str = "dense"


@dataclass
class UserInput:
    gpu_model: str
    num_gpus: int
    country: str
    training_hours: float
    inference_hours: float
    include_embodied: bool
    ci_source: str
    ci_mode: str
    start_time_utc: datetime
    training_profile: str
    inference_profile: str
    inference_mode: str  # gpu_hours | request_based
    request_inference: Optional[RequestInferenceModel]
    rho_training_override: Optional[float]
    rho_inference_override: Optional[float]
    telemetry_path: Optional[str]
    training_tflops: Optional[float]
    inference_tflops: Optional[float]
    run_monte_carlo: bool
    monte_carlo_iterations: int
    power_model: PowerModel
    cloud_provider: Optional[str]
    cloud_instance: Optional[str]
    instance_count: Optional[int]
    gpu_hourly_price_override: Optional[float]
