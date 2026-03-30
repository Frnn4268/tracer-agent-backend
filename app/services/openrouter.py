import httpx
from app.config import settings


class OpenRouterError(Exception):
    pass


class OpenRouterEmptyResponseError(OpenRouterError):
    pass


class OpenRouterConfigurationError(OpenRouterError):
    pass

class OpenRouterClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.base_url = settings.OPENROUTER_BASE_URL
        self.model = settings.MODEL_NAME
        self.fallback_models = settings.FALLBACK_MODEL_NAMES
        self.vision_model = settings.VISION_MODEL_NAME
        self.vision_fallback_models = settings.VISION_FALLBACK_MODEL_NAMES
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": settings.HTTP_REFERER,
            "X-Title": settings.X_TITLE,
            "Content-Type": "application/json"
        }

    async def chat_completion(
        self,
        messages: list[dict],
        requires_vision: bool = False,
        *,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> str:
        """
        Envía una conversación a OpenRouter y retorna la respuesta del asistente.
        """
        if not self.api_key:
            raise OpenRouterConfigurationError("Falta OPENROUTER_API_KEY en la configuración del proyecto.")

        models_to_try = self._build_model_candidates(requires_vision)
        errors: list[str] = []

        async with httpx.AsyncClient() as client:
            for model_name in models_to_try:
                payload = {
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }

                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self.headers,
                        json=payload,
                        timeout=30.0
                    )
                    response.raise_for_status()
                    data = response.json()
                    reply = self._extract_reply(data)

                    if reply:
                        return reply

                    errors.append(f"{model_name}: respuesta vacia")
                except httpx.HTTPStatusError as exc:
                    detail = exc.response.text.strip() if exc.response.text else str(exc)
                    errors.append(f"{model_name}: {detail}")
                except httpx.HTTPError as exc:
                    errors.append(f"{model_name}: {exc}")

        raise OpenRouterEmptyResponseError(
            "Ninguno de los modelos configurados devolvió contenido útil. Detalle: " + " | ".join(errors[:3])
        )

    def _build_model_candidates(self, requires_vision: bool) -> list[str]:
        candidates: list[str] = []

        if requires_vision:
            source_models = [self.vision_model, *self.vision_fallback_models]
        else:
            source_models = [self.model, *self.fallback_models]

        for model_name in source_models:
            if model_name and model_name not in candidates:
                candidates.append(model_name)
        return candidates

    def _extract_reply(self, data: dict) -> str | None:
        choices = data.get("choices") or []
        if not choices:
            return None

        first_choice = choices[0]
        direct_text = first_choice.get("text")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        message = first_choice.get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            stripped = content.strip()
            return stripped or None

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                    continue

                if not isinstance(item, dict):
                    continue

                item_type = item.get("type")
                if item_type == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])

            combined = "\n".join(part.strip() for part in text_parts if part and part.strip()).strip()
            return combined or None

        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            return refusal.strip()

        return None