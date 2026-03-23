from __future__ import annotations

from dataclasses import dataclass
import os
import re
from pathlib import Path


PLACEHOLDER_VALUES = {
    "CHANGE_ME",
    "DUMMY",
    "YOUR_VALUE_HERE",
    "YOUR_PRIVATE_KEY",
    "YOUR_WALLET_ADDRESS",
    "YOUR_GAMMA_API_KEY",
}


def _load_simple_env_file(env_path: str | Path = ".env") -> None:
    p = Path(env_path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


@dataclass(frozen=True)
class LiveTradingConfig:
    gamma_base_url: str
    clob_base_url: str
    gamma_api_key: str
    private_key: str
    wallet_address: str
    chain_id: int = 137

    @classmethod
    def from_env(cls, env_path: str | Path = ".env") -> "LiveTradingConfig":
        _load_simple_env_file(env_path)
        cfg = cls(
            gamma_base_url=os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
            clob_base_url=os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com"),
            gamma_api_key=os.getenv("POLY_GAMMA_API_KEY", ""),
            private_key=os.getenv("TRADING_PRIVATE_KEY", ""),
            wallet_address=os.getenv("TRADING_WALLET_ADDRESS", ""),
            chain_id=int(os.getenv("POLYGON_CHAIN_ID", "137")),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        self._validate_non_dummy("POLY_GAMMA_API_KEY", self.gamma_api_key)
        self._validate_non_dummy("TRADING_PRIVATE_KEY", self.private_key)
        self._validate_non_dummy("TRADING_WALLET_ADDRESS", self.wallet_address)

        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", self.private_key):
            raise ValueError("TRADING_PRIVATE_KEY must be a 0x-prefixed 64-hex private key")
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", self.wallet_address):
            raise ValueError("TRADING_WALLET_ADDRESS must be a 0x-prefixed 40-hex address")

    @staticmethod
    def _validate_non_dummy(key: str, value: str) -> None:
        v = (value or "").strip()
        if not v:
            raise ValueError(f"Missing required env var: {key}")
        if v in PLACEHOLDER_VALUES or "DUMMY" in v.upper() or "CHANGE_ME" in v.upper():
            raise ValueError(f"Env var {key} contains a dummy/placeholder value")
