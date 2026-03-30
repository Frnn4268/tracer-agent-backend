import base64

import httpx
from fastapi.testclient import TestClient

from app.models.schemas import PipelineMetadata
from app.routers import chat as chat_router
from app.services.pipeline import PipelineResult
from app.services.openrouter import OpenRouterConfigurationError
from main import app


client = TestClient(app)


def test_chat_endpoint_returns_pipeline_reply(monkeypatch):
    captured = {}

    async def fake_run(messages, requires_vision=False, web_research_mode="auto"):
        captured["messages"] = messages
        captured["requires_vision"] = requires_vision
        captured["web_research_mode"] = web_research_mode
        return PipelineResult(
            reply="## Diagnostico\nTodo correcto.",
            metadata=PipelineMetadata(
                route="troubleshooting",
                route_label="Troubleshooting",
                route_reason="Consulta de conectividad.",
                validation_status="validated",
                used_vision=False,
                used_web_validation=True,
                web_validation_status="used",
                correction_attempted=False,
                specialist_attempts=1,
                synthesis_status="used",
                stages=["analizador", "especialista", "validador", "sintetizador"],
            ),
        )

    monkeypatch.setattr(chat_router.pipeline, "run", fake_run)

    response = client.post(
        "/api/chat/",
        json={
            "messages": [{"role": "user", "content": "No hay conectividad entre VLAN 10 y 20", "attachments": []}],
            "options": {"web_research_mode": "force"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "## Diagnostico\nTodo correcto."
    assert payload["metadata"]["route"] == "troubleshooting"
    assert captured["requires_vision"] is False
    assert captured["web_research_mode"] == "force"
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"


def test_chat_endpoint_rejects_more_than_allowed_attachments():
    encoded_text = base64.b64encode(b"show ip interface brief").decode("ascii")
    attachments = [
        {
            "name": f"evidence-{index}.txt",
            "mime_type": "text/plain",
            "data_url": f"data:text/plain;base64,{encoded_text}",
        }
        for index in range(5)
    ]

    response = client.post(
        "/api/chat/",
        json={
            "messages": [{"role": "user", "content": "Revisa estos archivos", "attachments": attachments}],
            "options": {"web_research_mode": "auto"},
        },
    )

    assert response.status_code == 400
    assert "Solo se permiten hasta 4 adjuntos" in response.json()["detail"]


def test_chat_endpoint_marks_vision_when_image_is_attached(monkeypatch):
    captured = {}
    image_payload = base64.b64encode(b"fake-image-bytes").decode("ascii")

    async def fake_run(messages, requires_vision=False, web_research_mode="auto"):
        captured["messages"] = messages
        captured["requires_vision"] = requires_vision
        return PipelineResult(
            reply="## Analisis\nSe detecto una captura adjunta.",
            metadata=PipelineMetadata(
                route="evidence_analysis",
                route_label="Analisis de evidencia",
                route_reason="Hay imagen adjunta.",
                validation_status="validated",
                used_vision=True,
                used_web_validation=False,
                web_validation_status="skipped",
                correction_attempted=False,
                specialist_attempts=1,
                synthesis_status="skipped",
                stages=["analizador", "especialista", "validador"],
            ),
        )

    monkeypatch.setattr(chat_router.pipeline, "run", fake_run)

    response = client.post(
        "/api/chat/",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Analiza la topologia de la captura",
                    "attachments": [
                        {
                            "name": "topologia.png",
                            "mime_type": "image/png",
                            "data_url": f"data:image/png;base64,{image_payload}",
                        }
                    ],
                }
            ],
            "options": {"web_research_mode": "auto"},
        },
    )

    assert response.status_code == 200
    assert captured["requires_vision"] is True
    assert isinstance(captured["messages"][1]["content"], list)


def test_chat_endpoint_maps_openrouter_configuration_error_to_500(monkeypatch):
    async def fake_run(messages, requires_vision=False, web_research_mode="auto"):
        raise OpenRouterConfigurationError("Falta OPENROUTER_API_KEY")

    monkeypatch.setattr(chat_router.pipeline, "run", fake_run)

    response = client.post(
        "/api/chat/",
        json={
            "messages": [{"role": "user", "content": "Necesito ayuda con OSPF", "attachments": []}],
            "options": {"web_research_mode": "auto"},
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Falta OPENROUTER_API_KEY"


def test_chat_endpoint_maps_openrouter_http_status_error_to_502(monkeypatch):
    async def fake_run(messages, requires_vision=False, web_research_mode="auto"):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        response = httpx.Response(429, request=request, text="rate limit")
        raise httpx.HTTPStatusError("too many requests", request=request, response=response)

    monkeypatch.setattr(chat_router.pipeline, "run", fake_run)

    response = client.post(
        "/api/chat/",
        json={
            "messages": [{"role": "user", "content": "Valida este comando", "attachments": []}],
            "options": {"web_research_mode": "auto"},
        },
    )

    assert response.status_code == 502
    assert "OpenRouter devolvió un error HTTP: rate limit" == response.json()["detail"]


def test_chat_endpoint_maps_openrouter_connection_error_to_502(monkeypatch):
    async def fake_run(messages, requires_vision=False, web_research_mode="auto"):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        raise httpx.ConnectError("network down", request=request)

    monkeypatch.setattr(chat_router.pipeline, "run", fake_run)

    response = client.post(
        "/api/chat/",
        json={
            "messages": [{"role": "user", "content": "Explica VTP", "attachments": []}],
            "options": {"web_research_mode": "auto"},
        },
    )

    assert response.status_code == 502
    assert "No se pudo contactar con OpenRouter:" in response.json()["detail"]