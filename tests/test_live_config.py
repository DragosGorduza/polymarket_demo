from __future__ import annotations

from pathlib import Path

import pytest

from execution.live_config import LiveTradingConfig


def test_live_config_raises_for_dummy_values(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "POLY_GAMMA_API_KEY=YOUR_GAMMA_API_KEY",
                "TRADING_PRIVATE_KEY=YOUR_PRIVATE_KEY",
                "TRADING_WALLET_ADDRESS=YOUR_WALLET_ADDRESS",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        LiveTradingConfig.from_env(env)


def test_live_config_raises_for_wrong_private_key_format(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "POLY_GAMMA_API_KEY=real_key_123",
                "TRADING_PRIVATE_KEY=0x1234",
                "TRADING_WALLET_ADDRESS=0x1111111111111111111111111111111111111111",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        LiveTradingConfig.from_env(env)
