"""Instrument and strategy admission rules for research jobs."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with Path(path).open('r', encoding='utf-8') as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f'Expected YAML mapping: {path}')
    return value


def deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_instrument(registry: dict, symbol: str) -> dict:
    profile = (registry.get('symbols') or {}).get(symbol)
    if profile is None:
        raise ValueError(
            f'No instrument profile for {symbol}. Add its exact broker symbol to configs/instruments.yaml.'
        )
    if not profile.get('enabled', False):
        raise ValueError(f'Instrument profile for {symbol} is disabled.')
    execution = profile.get('execution')
    required = {'contract_size', 'point_size', 'spread_points', 'slippage_points',
                'commission_per_lot_round_turn_usd', 'intrabar_policy'}
    if not isinstance(execution, dict) or required.difference(execution):
        raise ValueError(f'Instrument profile for {symbol} has incomplete execution settings.')
    if not profile.get('asset_class'):
        raise ValueError(f'Instrument profile for {symbol} must define asset_class.')
    return deepcopy(profile)


def strategy_supports(strategy_class, instrument: dict, timeframe: str) -> tuple[bool, str]:
    """Use explicit class declarations; default is deny for safety in batch mode."""
    asset_classes = getattr(strategy_class, 'supported_asset_classes', ())
    timeframes = getattr(strategy_class, 'supported_timeframes', ())
    if not asset_classes or not timeframes:
        return False, 'strategy has no explicit supported_asset_classes/supported_timeframes declaration'
    asset_class = instrument['asset_class']
    if asset_class not in asset_classes:
        return False, f'strategy does not support asset class {asset_class}'
    if timeframe.upper() not in {value.upper() for value in timeframes}:
        return False, f'strategy does not support timeframe {timeframe}'
    return True, 'compatible'


def resolved_risk_params(base_risk: dict, instrument: dict) -> dict:
    """Create one immutable risk/execution configuration per research job."""
    return deep_merge(base_risk, {'execution': instrument['execution']})

