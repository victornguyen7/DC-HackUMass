"""DC What-If simulation engine.

Public entry points:
    load_baseline()                 -> ScenarioConfig built from data files
    run_night(cfg, seed)            -> NightResult for one simulated night
    run_scenario(cfg, n_nights)     -> ScenarioResult aggregated over nights
    compare(baseline, scenario)     -> paired comparison (same seeds)
"""

from .config import ENGINE_VERSION, ScenarioConfig, config_hash
from .data import load_baseline
from .runner import compare, run_night, run_scenario

__all__ = [
    "ENGINE_VERSION",
    "ScenarioConfig",
    "compare",
    "config_hash",
    "load_baseline",
    "run_night",
    "run_scenario",
]
