import logging
import re
from dataclasses import dataclass

from app.models.schemas import PipelineMetadata
from app.services.openrouter import OpenRouterClient, OpenRouterError
from app.services.web_research import WebResearchResult, WebResearchService

logger = logging.getLogger(__name__)

MAX_VALIDATION_CONTEXT_CHARS = 8000
ROUTE_OPTIONS = {
    "evidence_analysis",
    "troubleshooting",
    "lab_planning",
    "command_validation",
    "concept_explanation",
    "general_packet_tracer",
}


@dataclass
class RouteDecision:
    route: str
    route_label: str
    route_reason: str
    specialist_focus: str
    needs_clarification: bool
    use_web_research: bool


@dataclass
class PipelineResult:
    reply: str
    metadata: PipelineMetadata


@dataclass
class ValidationDecision:
    status: str
    final_response: str
    retry_specialist: bool
    specialist_feedback: str


class PacketTracerPipeline:
    def __init__(self, client: OpenRouterClient):
        self.client = client
        self.web_research = WebResearchService()

    async def run(
        self,
        messages: list[dict],
        requires_vision: bool = False,
        web_research_mode: str = "auto",
    ) -> PipelineResult:
        route = await self._analyze_route(messages, requires_vision, web_research_mode)
        research_result = await self._run_web_research(messages, route, requires_vision, web_research_mode)
        stages = ["analizador"]
        specialist_attempts = 1
        correction_attempted = False
        synthesis_status = "skipped"

        if research_result.status == "used":
            stages.append("investigador_web")

        specialist_reply = await self.client.chat_completion(
            self._build_specialist_messages(messages, route, research_result),
            requires_vision=requires_vision,
            max_tokens=1100,
            temperature=0.25,
        )
        stages.append("especialista")

        validation_status = "skipped"
        final_reply = specialist_reply

        try:
            validation_decision = await self._validate_specialist_reply(
                messages,
                route,
                specialist_reply,
                research_result,
                allow_retry=True,
            )

            if validation_decision.retry_specialist and validation_decision.specialist_feedback:
                correction_attempted = True
                specialist_attempts = 2
                retry_reply = await self.client.chat_completion(
                    self._build_specialist_retry_messages(
                        messages,
                        route,
                        research_result,
                        specialist_reply,
                        validation_decision.specialist_feedback,
                    ),
                    requires_vision=requires_vision,
                    max_tokens=1100,
                    temperature=0.2,
                )
                final_reply = retry_reply
                stages.append("especialista_reintento")

                retry_validation_decision = await self._validate_specialist_reply(
                    messages,
                    route,
                    retry_reply,
                    research_result,
                    allow_retry=False,
                )
                validation_status = retry_validation_decision.status
                final_reply = retry_validation_decision.final_response or retry_reply
            else:
                validation_status = validation_decision.status
                final_reply = validation_decision.final_response or specialist_reply
        except Exception:
            logger.exception("Fallo en la etapa de validacion del pipeline multiagente.")
            validation_status = "skipped"

        stages.append("validador")

        try:
            synthesized_reply = await self._synthesize_final_reply(
                messages,
                route,
                final_reply,
                validation_status,
            )
            if synthesized_reply:
                final_reply = synthesized_reply
                synthesis_status = "used"
                stages.append("sintetizador")
        except Exception:
            logger.exception("Fallo en la etapa de sintesis final del pipeline multiagente.")
            synthesis_status = "skipped"

        metadata = PipelineMetadata(
            route=route.route,
            route_label=route.route_label,
            route_reason=route.route_reason,
            validation_status=validation_status,
            used_vision=requires_vision,
            used_web_validation=research_result.status == "used",
            web_validation_status=research_result.status,
            web_sources=research_result.sources,
            correction_attempted=correction_attempted,
            specialist_attempts=specialist_attempts,
            synthesis_status=synthesis_status,
            stages=stages,
        )

        return PipelineResult(reply=final_reply, metadata=metadata)

    async def _validate_specialist_reply(
        self,
        messages: list[dict],
        route: RouteDecision,
        specialist_reply: str,
        research_result: WebResearchResult,
        *,
        allow_retry: bool,
    ) -> ValidationDecision:
        validator_reply = await self.client.chat_completion(
            self._build_validator_messages(
                messages,
                route,
                specialist_reply,
                research_result,
                allow_retry=allow_retry,
            ),
                requires_vision=False,
                max_tokens=1200,
                temperature=0.15,
            )
        return self._parse_validator_reply(validator_reply, specialist_reply)

    async def _analyze_route(
        self,
        messages: list[dict],
        requires_vision: bool,
        web_research_mode: str,
    ) -> RouteDecision:
        fallback_route = self._analyze_route_fallback(messages, requires_vision, web_research_mode)
        latest_user_text = self._extract_latest_user_text(messages)
        router_prompt = """Eres el agente analizador de un asistente de Cisco Packet Tracer.

Clasifica la consulta en una sola ruta usando exactamente uno de estos valores:
- evidence_analysis
- troubleshooting
- lab_planning
- command_validation
- concept_explanation
- general_packet_tracer

Devuelve SIEMPRE este formato exacto:
ROUTE: <valor>
LABEL: <nombre corto>
REASON: <una frase breve>
FOCUS: <una frase breve>
CLARIFY: yes|no
USE_WEB: yes|no

Reglas:
- Usa USE_WEB=yes solo si conviene contrastar la respuesta con documentacion publica para reducir errores.
- Si hay imagenes o adjuntos tecnicos, prioriza evidence_analysis.
- Si el usuario pide configurar algo grande, usa lab_planning.
- Si el usuario reporta un fallo, usa troubleshooting.
- Si pide explicar teoria, usa concept_explanation.
"""
        router_input = (
            f"Consulta mas reciente:\n{latest_user_text[:1200]}\n\n"
            f"Hay evidencia visual: {'yes' if requires_vision else 'no'}"
        )

        try:
            router_reply = await self.client.chat_completion(
                [
                    {"role": "system", "content": router_prompt},
                    {"role": "user", "content": router_input},
                ],
                requires_vision=False,
                max_tokens=240,
                temperature=0.0,
            )
            return self._parse_route_reply(router_reply, fallback_route, requires_vision, web_research_mode)
        except OpenRouterError:
            return fallback_route

    def _analyze_route_fallback(
        self,
        messages: list[dict],
        requires_vision: bool,
        web_research_mode: str,
    ) -> RouteDecision:
        latest_user_text = self._extract_latest_user_text(messages).lower()
        has_attachments_context = "adjunta" in latest_user_text or "archivo" in latest_user_text or "imagen" in latest_user_text

        if requires_vision or has_attachments_context:
            route = RouteDecision(
                route="evidence_analysis",
                route_label="Analisis de evidencia",
                route_reason="La consulta incluye archivos o indicios de evidencia adjunta que deben incorporarse al diagnostico.",
                specialist_focus="Analiza primero la evidencia, extrae señales tecnicas y luego propone una accion concreta o una aclaracion minima.",
                needs_clarification=False,
                use_web_research=False,
            )
            return self._apply_web_research_override(route, requires_vision, web_research_mode)

        if self._contains_any(latest_user_text, [
            "ping", "no conecta", "no hay conectividad", "no me da", "falla", "error", "problema", "show ip route",
            "interface brief", "traceroute", "ospf", "eigrp", "rip", "vlan", "router-on-a-stick"
        ]):
            route = RouteDecision(
                route="troubleshooting",
                route_label="Troubleshooting",
                route_reason="La redaccion apunta a fallo de conectividad, routing o switching y requiere diagnostico guiado.",
                specialist_focus="Prioriza hipotesis tecnicas, verificaciones y comandos de diagnostico antes de proponer cambios definitivos.",
                needs_clarification=False,
                use_web_research=True,
            )
            return self._apply_web_research_override(route, requires_vision, web_research_mode)

        if self._contains_any(latest_user_text, [
            "configura", "configurar", "crear", "montar", "hacer", "topologia", "topología", "laboratorio", "paso a paso", "planning"
        ]):
            route = RouteDecision(
                route="lab_planning",
                route_label="Laboratorio guiado",
                route_reason="La consulta parece pedir construccion o resolucion completa de un ejercicio.",
                specialist_focus="Entrega un plan breve y desarrolla solo el primer paso con comandos y validaciones puntuales.",
                needs_clarification=False,
                use_web_research=False,
            )
            return self._apply_web_research_override(route, requires_vision, web_research_mode)

        if self._contains_any(latest_user_text, [
            "comando", "sintaxis", "ios", "corrige", "esta bien", "está bien", "running-config", "ip route", "router ospf"
        ]):
            route = RouteDecision(
                route="command_validation",
                route_label="Validacion de comandos",
                route_reason="La consulta esta orientada a revisar sintaxis o correccion de comandos Cisco IOS.",
                specialist_focus="Centra la respuesta en validar sintaxis, contexto de ejecucion y errores tipicos en Packet Tracer.",
                needs_clarification=False,
                use_web_research=True,
            )
            return self._apply_web_research_override(route, requires_vision, web_research_mode)

        if self._contains_any(latest_user_text, [
            "que es", "qué es", "explica", "diferencia", "como funciona", "cómo funciona", "concepto"
        ]):
            route = RouteDecision(
                route="concept_explanation",
                route_label="Explicacion tecnica",
                route_reason="La consulta busca aclarar un concepto o comparar tecnologias de red.",
                specialist_focus="Explica el concepto con precision, ejemplos de Packet Tracer y comandos solo si aportan valor.",
                needs_clarification=False,
                use_web_research=True,
            )
            return self._apply_web_research_override(route, requires_vision, web_research_mode)

        needs_clarification = len(latest_user_text.strip()) < 18
        route = RouteDecision(
            route="general_packet_tracer",
            route_label="Consulta general",
            route_reason="La consulta no encaja claramente en una categoria mas especifica y se trata como asistencia general de Packet Tracer.",
            specialist_focus="Responde con enfoque tecnico y, si falta contexto critico, pide solo los datos minimos necesarios.",
            needs_clarification=needs_clarification,
            use_web_research=not needs_clarification,
        )
        return self._apply_web_research_override(route, requires_vision, web_research_mode)

    def _build_specialist_messages(
        self,
        messages: list[dict],
        route: RouteDecision,
        research_result: WebResearchResult,
    ) -> list[dict]:
        system_prompt = f"""Eres el agente especialista de Packet Tracer.

Ruta asignada: {route.route_label}
Motivo: {route.route_reason}
Foco: {route.specialist_focus}

Reglas:
- Responde siempre en espanol.
- Mantente en temas de Cisco Packet Tracer y redes Cisco.
- No inventes interfaces, IPs ni datos faltantes.
- Si faltan datos criticos, pide solo la informacion minima necesaria.
- Usa Markdown valido.
- Si hay comandos, colocalos en bloques de codigo.
- Si la ruta es troubleshooting, empieza por verificaciones y comandos show antes de cambios de configuracion.
- Si la ruta es laboratorio guiado, entrega un plan breve y desarrolla solo el primer paso.
- Si la ruta es validacion de comandos, di exactamente que es correcto, que es incorrecto y como quedaria.
- Si recibes contexto web, usalo solo como contraste tecnico y no cites nada que contradiga el contexto real del usuario.
- No menciones agentes, pipeline, validador ni proceso interno.
"""

        specialist_messages = [{"role": "system", "content": system_prompt}]
        if research_result.status == "used" and research_result.brief:
            specialist_messages.append({"role": "system", "content": research_result.brief})

        specialist_messages.extend(messages[1:])
        return specialist_messages

    def _build_validator_messages(
        self,
        messages: list[dict],
        route: RouteDecision,
        specialist_reply: str,
        research_result: WebResearchResult,
        *,
        allow_retry: bool,
    ) -> list[dict]:
        history_text = self._serialize_messages(messages)
        research_context = research_result.brief if research_result.status == "used" else "Sin apoyo web adicional."
        retry_instruction = "yes|no" if allow_retry else "no"
        validator_prompt = f"""Eres el agente validador de Packet Tracer.

Tu tarea es revisar la respuesta del especialista y corregirla si hay comandos dudosos, pasos ilogicos, suposiciones no justificadas o recomendaciones incompatibles con Packet Tracer.

Ruta asignada: {route.route_label}

Reglas:
- Responde siempre en espanol.
- No inventes datos nuevos si el usuario no los proporciono.
- Si falta contexto critico, no improvises: pide la informacion minima necesaria.
- Devuelve SIEMPRE este formato exacto:

STATUS: validated | revised | needs_clarification
RATIONALE: una sola frase breve
RETRY_SPECIALIST: {retry_instruction}
SPECIALIST_FEEDBACK:
<una sola lista corta de correcciones para el especialista o N/A>
FINAL_RESPONSE:
<respuesta final en Markdown lista para mostrar al usuario>
"""

        validator_input = (
            "Contexto reciente:\n"
            f"{history_text[:MAX_VALIDATION_CONTEXT_CHARS]}\n\n"
            "Contexto web:\n"
            f"{research_context}\n\n"
            "Borrador del especialista:\n"
            f"{specialist_reply}"
        )

        return [
            {"role": "system", "content": validator_prompt},
            {"role": "user", "content": validator_input},
        ]

    def _build_specialist_retry_messages(
        self,
        messages: list[dict],
        route: RouteDecision,
        research_result: WebResearchResult,
        previous_reply: str,
        specialist_feedback: str,
    ) -> list[dict]:
        retry_instructions = (
            "Corrige tu respuesta anterior usando estas observaciones del validador. "
            "Si falta contexto critico, pide solo lo minimo necesario. "
            "No expliques el proceso interno ni menciones que hubo correccion interna."
        )
        retry_messages = self._build_specialist_messages(messages, route, research_result)
        retry_messages.append(
            {
                "role": "system",
                "content": (
                    f"{retry_instructions}\n\n"
                    f"Respuesta anterior:\n{previous_reply}\n\n"
                    f"Observaciones del validador:\n{specialist_feedback}"
                ),
            }
        )
        return retry_messages

    async def _synthesize_final_reply(
        self,
        messages: list[dict],
        route: RouteDecision,
        technical_reply: str,
        validation_status: str,
    ) -> str:
        latest_user_text = self._extract_latest_user_text(messages)
        synthesizer_prompt = f"""Eres el sintetizador final de un asistente de Cisco Packet Tracer.

Tu trabajo es mejorar la claridad didactica de una respuesta tecnica ya validada sin cambiar su contenido tecnico.

Ruta: {route.route_label}
Estado de validacion: {validation_status}

Reglas:
- Responde siempre en espanol.
- No inventes datos nuevos.
- No cambies comandos, nombres de interfaces, IPs o bloques de codigo salvo para preservarlos intactos.
- Mantén la respuesta concisa y facil de seguir.
- Usa Markdown valido.
- Si la respuesta pide aclaracion o datos faltantes, haz esa solicitud mas clara pero no agregues nuevas suposiciones.
- Si la ruta es laboratorio guiado, conserva plan breve y desarrollo solo del primer paso.
- Si la ruta es troubleshooting, prioriza verificaciones, sintomas y siguiente accion concreta.
- No menciones sintetizador, pipeline ni proceso interno.
"""

        synthesizer_input = (
            f"Consulta del usuario:\n{latest_user_text[:1600]}\n\n"
            f"Respuesta tecnica base:\n{technical_reply}"
        )

        return await self.client.chat_completion(
            [
                {"role": "system", "content": synthesizer_prompt},
                {"role": "user", "content": synthesizer_input},
            ],
            requires_vision=False,
            max_tokens=1200,
            temperature=0.2,
        )

    def _parse_validator_reply(self, validator_reply: str, specialist_reply: str) -> ValidationDecision:
        status_match = re.search(r"STATUS:\s*(validated|revised|needs_clarification)", validator_reply, re.IGNORECASE)
        retry_match = re.search(r"RETRY_SPECIALIST:\s*(yes|no)", validator_reply, re.IGNORECASE)
        feedback_match = re.search(r"SPECIALIST_FEEDBACK:\s*(.*?)\nFINAL_RESPONSE:", validator_reply, re.IGNORECASE | re.DOTALL)
        final_match = re.search(r"FINAL_RESPONSE:\s*(.*)\Z", validator_reply, re.IGNORECASE | re.DOTALL)

        status = status_match.group(1).lower() if status_match else "skipped"
        retry_specialist = retry_match.group(1).lower() == "yes" if retry_match else False
        specialist_feedback = feedback_match.group(1).strip() if feedback_match else ""
        final_response = final_match.group(1).strip() if final_match else ""

        if not final_response:
            final_response = specialist_reply
            status = "skipped"
            retry_specialist = False

        if specialist_feedback.upper() == "N/A":
            specialist_feedback = ""

        return ValidationDecision(
            status=status,
            final_response=final_response,
            retry_specialist=retry_specialist,
            specialist_feedback=specialist_feedback,
        )

    async def _run_web_research(
        self,
        messages: list[dict],
        route: RouteDecision,
        requires_vision: bool,
        web_research_mode: str,
    ) -> WebResearchResult:
        if requires_vision or web_research_mode == "off" or not route.use_web_research:
            return WebResearchResult(status="skipped", sources=[], brief="")

        query = self._build_research_query(messages, route)
        return await self.web_research.research(query)

    def _build_research_query(self, messages: list[dict], route: RouteDecision) -> str:
        latest_user_text = self._extract_latest_user_text(messages)
        route_suffix = {
            "troubleshooting": "Cisco IOS troubleshooting documentation",
            "command_validation": "Cisco IOS command reference",
            "concept_explanation": "Cisco networking documentation RFC",
            "general_packet_tracer": "Cisco Packet Tracer documentation",
        }.get(route.route, "Cisco networking documentation")
        return f"{latest_user_text[:180]} {route_suffix}".strip()

    def _parse_route_reply(
        self,
        router_reply: str,
        fallback_route: RouteDecision,
        requires_vision: bool,
        web_research_mode: str,
    ) -> RouteDecision:
        route_match = re.search(r"ROUTE:\s*([a-z_]+)", router_reply, re.IGNORECASE)
        label_match = re.search(r"LABEL:\s*(.+)", router_reply)
        reason_match = re.search(r"REASON:\s*(.+)", router_reply)
        focus_match = re.search(r"FOCUS:\s*(.+)", router_reply)
        clarify_match = re.search(r"CLARIFY:\s*(yes|no)", router_reply, re.IGNORECASE)
        web_match = re.search(r"USE_WEB:\s*(yes|no)", router_reply, re.IGNORECASE)

        route_value = route_match.group(1).lower() if route_match else ""
        if route_value not in ROUTE_OPTIONS:
            return fallback_route

        route_label = label_match.group(1).strip() if label_match else fallback_route.route_label
        route_reason = reason_match.group(1).strip() if reason_match else fallback_route.route_reason
        specialist_focus = focus_match.group(1).strip() if focus_match else fallback_route.specialist_focus
        needs_clarification = (clarify_match.group(1).lower() == "yes") if clarify_match else fallback_route.needs_clarification
        use_web_research = (web_match.group(1).lower() == "yes") if web_match else fallback_route.use_web_research

        if requires_vision:
            route_value = "evidence_analysis"
            route_label = "Analisis de evidencia"
            use_web_research = False

        route_decision = RouteDecision(
            route=route_value,
            route_label=route_label,
            route_reason=route_reason,
            specialist_focus=specialist_focus,
            needs_clarification=needs_clarification,
            use_web_research=use_web_research,
        )
        return self._apply_web_research_override(route_decision, requires_vision, web_research_mode)

    def _apply_web_research_override(
        self,
        route: RouteDecision,
        requires_vision: bool,
        web_research_mode: str,
    ) -> RouteDecision:
        if requires_vision:
            route.use_web_research = False
            return route

        if web_research_mode == "force":
            route.use_web_research = True
        elif web_research_mode == "off":
            route.use_web_research = False

        return route

    def _serialize_messages(self, messages: list[dict]) -> str:
        rendered_messages: list[str] = []
        for message in messages[1:]:
            role = message.get("role", "unknown")
            content = message.get("content", "")
            rendered_messages.append(f"[{role}] {self._render_content_as_text(content)}")
        return "\n\n".join(rendered_messages)

    def _extract_latest_user_text(self, messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                return self._render_content_as_text(message.get("content", ""))
        return ""

    def _render_content_as_text(self, content: str | list[dict]) -> str:
        if isinstance(content, str):
            return content

        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and item.get("type") == "image_url":
                text_parts.append("[imagen adjunta]")
            else:
                text_parts.append(str(item))

        return "\n".join(part for part in text_parts if part).strip()

    def _contains_any(self, text: str, terms: list[str]) -> bool:
        return any(term in text for term in terms)