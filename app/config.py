import os
from dotenv import load_dotenv

load_dotenv()


def parse_csv_env(value: str, default: list[str]) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


def parse_bool_env(value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "si", "sí"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


class Settings:
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "stepfun/step-3.5-flash:free")
    FALLBACK_MODEL_NAMES: list[str] = parse_csv_env(
        os.getenv("FALLBACK_MODEL_NAMES", ""),
        [
            "stepfun/step-3.5-flash:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "openai/gpt-oss-20b:free",
            "openrouter/free",
        ],
    )
    VISION_MODEL_NAME: str = os.getenv("VISION_MODEL_NAME", "nvidia/nemotron-nano-12b-v2-vl:free")
    VISION_FALLBACK_MODEL_NAMES: list[str] = parse_csv_env(
        os.getenv("VISION_FALLBACK_MODEL_NAMES", ""),
        [
            "nvidia/nemotron-nano-12b-v2-vl:free",
            "openrouter/free",
        ],
    )
    WEB_RESEARCH_ENABLED: bool = parse_bool_env(os.getenv("WEB_RESEARCH_ENABLED", "true"), True)
    WEB_RESEARCH_TIMEOUT_SECONDS: float = float(os.getenv("WEB_RESEARCH_TIMEOUT_SECONDS", "10"))
    WEB_RESEARCH_MAX_RESULTS: int = int(os.getenv("WEB_RESEARCH_MAX_RESULTS", "2"))
    FRONTEND_ORIGINS: list[str] = parse_csv_env(
        os.getenv(
            "FRONTEND_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173",
        ),
        [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
    )
    WEB_RESEARCH_ALLOWED_DOMAINS: list[str] = parse_csv_env(
        os.getenv("WEB_RESEARCH_ALLOWED_DOMAINS", ""),
        [
            "cisco.com",
            "learningnetwork.cisco.com",
            "netacad.com",
            "ietf.org",
            "rfc-editor.org",
        ],
    )
    HTTP_REFERER: str = "https://cisco-pt-assistant.local"
    X_TITLE: str = "Cisco PT Assistant"

settings = Settings()