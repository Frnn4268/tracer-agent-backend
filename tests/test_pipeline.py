import asyncio

from app.models.schemas import WebSource
from app.services.pipeline import PacketTracerPipeline, RouteDecision
from app.services.web_research import WebResearchResult


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_completion(self, messages, requires_vision=False, *, max_tokens=900, temperature=0.3):
        self.calls.append(
            {
                "messages": messages,
                "requires_vision": requires_vision,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.responses.pop(0)


def build_route(use_web_research=True):
    return RouteDecision(
        route="troubleshooting",
        route_label="Troubleshooting",
        route_reason="Consulta de conectividad.",
        specialist_focus="Pedir verificaciones antes de proponer cambios.",
        needs_clarification=False,
        use_web_research=use_web_research,
    )


def test_pipeline_runs_retry_validation_and_synthesis():
    client = StubClient(
        responses=[
            "Borrador inicial del especialista",
            "STATUS: revised\nRATIONALE: Falta corregir un comando\nRETRY_SPECIALIST: yes\nSPECIALIST_FEEDBACK:\n- Corrige la sintaxis de encapsulation dot1Q\nFINAL_RESPONSE:\nSe requiere un ajuste.",
            "Borrador corregido del especialista",
            "STATUS: validated\nRATIONALE: La respuesta corregida ya es consistente\nRETRY_SPECIALIST: no\nSPECIALIST_FEEDBACK:\nN/A\nFINAL_RESPONSE:\n## Paso 1\nVerifica trunk y VLAN.",
            "## Paso 1\nVerifica trunk y VLAN antes de seguir.",
        ]
    )
    pipeline = PacketTracerPipeline(client)

    async def fake_analyze_route(messages, requires_vision, web_research_mode):
        return build_route(use_web_research=True)

    async def fake_research(query):
        return WebResearchResult(
            status="used",
            sources=[WebSource(title="Cisco trunk guide", url="https://www.cisco.com/example", domain="cisco.com")],
            brief="Contexto web consultado con fuentes externas confiables:",
        )

    pipeline._analyze_route = fake_analyze_route
    pipeline.web_research.research = fake_research

    result = asyncio.run(
        pipeline.run(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "No hay conectividad entre VLAN y el trunk parece mal configurado"},
            ],
            requires_vision=False,
            web_research_mode="auto",
        )
    )

    assert result.reply == "## Paso 1\nVerifica trunk y VLAN antes de seguir."
    assert result.metadata.route == "troubleshooting"
    assert result.metadata.used_web_validation is True
    assert result.metadata.web_validation_status == "used"
    assert result.metadata.correction_attempted is True
    assert result.metadata.specialist_attempts == 2
    assert result.metadata.validation_status == "validated"
    assert result.metadata.synthesis_status == "used"
    assert result.metadata.stages == [
        "analizador",
        "investigador_web",
        "especialista",
        "especialista_reintento",
        "validador",
        "sintetizador",
    ]
    assert len(client.calls) == 5


def test_pipeline_gracefully_skips_validation_and_synthesis_when_they_fail():
    client = StubClient(responses=["Borrador inicial del especialista"])
    pipeline = PacketTracerPipeline(client)

    async def fake_analyze_route(messages, requires_vision, web_research_mode):
        return build_route(use_web_research=False)

    async def fake_validate_specialist_reply(messages, route, specialist_reply, research_result, *, allow_retry):
        raise RuntimeError("validator exploded")

    async def fake_synthesize_final_reply(messages, route, technical_reply, validation_status):
        raise RuntimeError("synth exploded")

    pipeline._analyze_route = fake_analyze_route
    pipeline._validate_specialist_reply = fake_validate_specialist_reply
    pipeline._synthesize_final_reply = fake_synthesize_final_reply

    result = asyncio.run(
        pipeline.run(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "Revisa este problema de conectividad"},
            ],
            requires_vision=False,
            web_research_mode="off",
        )
    )

    assert result.reply == "Borrador inicial del especialista"
    assert result.metadata.validation_status == "skipped"
    assert result.metadata.synthesis_status == "skipped"
    assert result.metadata.used_web_validation is False
    assert result.metadata.stages == ["analizador", "especialista", "validador"]