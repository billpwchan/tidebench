"""Short scientific notation cannot cause unbounded fixed-point expansion."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.portfolio_registry import PortfolioDefinition
from tidebench.schemas import StrategyInput


@pytest.mark.parametrize("value", ["1e-5000", "1e-999999999", "0e-999999999", "0e+999999999"])
def test_portfolio_rejects_extreme_exponents_before_canonical_render(value):
    with pytest.raises(ValidationError, match="exponent domain"):
        PortfolioDefinition.model_validate(
            {
                "legs": [
                    {"inst_id": "BTC-USDT", "weight": value},
                    {"inst_id": "ETH-USDT", "weight": ".5"},
                ]
            }
        )


def test_nested_strategy_precision_is_checked_without_context_rounding():
    exact = ".1234567890123456789012345678901234567890123456789"
    assert StrategyInput(allocation=exact).allocation == Decimal(exact)
    with pytest.raises(ValidationError, match="precision"):
        StrategyInput(allocation=exact + "123")
    with pytest.raises(ValidationError, match="exponent domain"):
        PortfolioDefinition.model_validate(
            {
                "legs": [
                    {"inst_id": "BTC-USDT", "weight": ".5", "strategy": {"stop_loss_pct": "0e-999999999"}},
                    {"inst_id": "ETH-USDT", "weight": ".5"},
                ]
            }
        )


def test_extreme_decimal_http_input_is_rejected_before_persisting_jobs(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, auth_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/backtests", json={"source": "example", "fee_bps": "1e-999999999"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert client.get("/api/v1/backtests?source=example").json()["items"] == []
        response = client.post(
            "/api/v1/pro/portfolio-strategies",
            json={
                "name": "Bounded input",
                "hypothesis": "An oversized exponent must not be expanded.",
                "definition": {
                    "legs": [
                        {"inst_id": "BTC-USDT", "weight": "1e-999999999"},
                        {"inst_id": "ETH-USDT", "weight": ".5"},
                    ]
                },
            },
        )
        assert response.status_code == 422
        assert client.get("/api/v1/pro/portfolio-strategies").json()["items"] == []
