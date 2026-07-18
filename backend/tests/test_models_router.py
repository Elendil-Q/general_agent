"""Tests for the models Gateway router (`GET /api/models`)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import deps
from app.gateway.routers import models as models_router
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_model(name: str, **overrides) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use="langchain_openai:ChatOpenAI",
        model=name,
        **overrides,
    )


def _make_app_config(models: list[ModelConfig]) -> AppConfig:
    return AppConfig(
        models=models,
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def _make_client(config: AppConfig) -> TestClient:
    app = FastAPI()
    app.include_router(models_router.router)
    app.dependency_overrides[deps.get_config] = lambda: config
    return TestClient(app)


def test_list_models_returns_context_window_when_configured() -> None:
    config = _make_app_config([_make_model("big-ctx", context_window=256000)])

    with _make_client(config) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    data = response.json()
    assert data["models"][0]["context_window"] == 256000


def test_list_models_returns_null_context_window_when_unset() -> None:
    config = _make_app_config([_make_model("no-ctx")])

    with _make_client(config) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    data = response.json()
    assert data["models"][0]["context_window"] is None


def test_get_model_returns_context_window() -> None:
    config = _make_app_config([_make_model("big-ctx", context_window=128000)])

    with _make_client(config) as client:
        response = client.get("/api/models/big-ctx")

    assert response.status_code == 200
    assert response.json()["context_window"] == 128000
