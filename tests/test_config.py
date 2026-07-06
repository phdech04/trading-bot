"""Config loading: valid config loads; missing/invalid keys fail loudly."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trading_bot.config import ConfigError, load_settings
from trading_bot.models import Timeframe

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = REPO_ROOT / "config" / "config.yaml"


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
def valid_yaml_dict() -> dict:
    """A minimal valid config.yaml as a dict, for mutating in tests."""
    return {
        "symbols": ["SPY"],
        "timeframe": "1d",
        "risk": {
            "max_position_pct": 0.25,
            "max_total_exposure_pct": 0.8,
            "max_daily_loss_pct": 0.03,
            "stop_loss_pct": 0.05,
        },
        "backtest": {"start": "2024-01-02", "end": "2024-06-28"},
    }


def test_real_repo_config_loads(empty_env_file: Path) -> None:
    """The committed config/config.yaml must always be valid."""
    settings = load_settings(REAL_CONFIG, env_file=empty_env_file)
    assert settings.symbols  # non-empty universe
    assert settings.timeframe == Timeframe.DAY_1
    assert settings.dry_run is True  # safety default
    assert settings.env.trading_mode == "paper"  # safety default


def test_minimal_config_and_defaults(
    tmp_path: Path, empty_env_file: Path, valid_yaml_dict: dict
) -> None:
    config = _write_yaml(tmp_path / "config.yaml", valid_yaml_dict)
    settings = load_settings(config, env_file=empty_env_file)
    assert settings.env.ibkr_port == 7497  # paper port by default
    assert settings.fees.min_commission == 1.0  # optional section defaulted
    assert settings.backtest.initial_cash == 100_000.0


def test_missing_risk_section_fails_loudly(
    tmp_path: Path, empty_env_file: Path, valid_yaml_dict: dict
) -> None:
    del valid_yaml_dict["risk"]
    config = _write_yaml(tmp_path / "config.yaml", valid_yaml_dict)
    with pytest.raises(ConfigError) as excinfo:
        load_settings(config, env_file=empty_env_file)
    # The error must name the missing field so the user can fix it.
    assert "risk" in str(excinfo.value)


def test_bad_timeframe_fails_loudly(
    tmp_path: Path, empty_env_file: Path, valid_yaml_dict: dict
) -> None:
    valid_yaml_dict["timeframe"] = "42parsecs"
    config = _write_yaml(tmp_path / "config.yaml", valid_yaml_dict)
    with pytest.raises(ConfigError) as excinfo:
        load_settings(config, env_file=empty_env_file)
    assert "timeframe" in str(excinfo.value)


def test_missing_config_file_fails_loudly(tmp_path: Path, empty_env_file: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path / "nope.yaml", env_file=empty_env_file)
    assert "not found" in str(excinfo.value)


def test_env_file_values_are_read(tmp_path: Path, valid_yaml_dict: dict) -> None:
    config = _write_yaml(tmp_path / "config.yaml", valid_yaml_dict)
    env_file = tmp_path / ".env"
    env_file.write_text("IBKR_PORT=4002\nTRADING_MODE=paper\nIBKR_ACCOUNT=DU1234567\n")
    settings = load_settings(config, env_file=env_file)
    assert settings.env.ibkr_port == 4002
    assert settings.env.ibkr_account == "DU1234567"


def test_invalid_trading_mode_rejected(tmp_path: Path, valid_yaml_dict: dict) -> None:
    config = _write_yaml(tmp_path / "config.yaml", valid_yaml_dict)
    env_file = tmp_path / ".env"
    env_file.write_text("TRADING_MODE=yolo\n")
    with pytest.raises(ConfigError) as excinfo:
        load_settings(config, env_file=env_file)
    assert "trading_mode" in str(excinfo.value)
