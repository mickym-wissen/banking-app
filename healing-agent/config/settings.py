import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Gemini (kept for backward-compat but no longer used)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Anthropic Claude (RCA agent)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    MAX_REACT_ITERATIONS: int = int(os.getenv("MAX_REACT_ITERATIONS", "15"))

    # GitHub (RCA agent — repo code retrieval)
    GITHUB_PAT: str = os.getenv("GITHUB_TOKEN", "")  # reuse GITHUB_TOKEN from .env
    GITHUB_ORG: str = os.getenv("GITHUB_ORG", "varunesh-ra")

    # MySQL
    DB_HOST: str     = os.getenv("DB_HOST", "localhost")
    DB_PORT: int     = int(os.getenv("DB_PORT", "3306"))
    DB_NAME: str     = os.getenv("DB_NAME", "healing_agent_db")
    DB_USER: str     = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # Log source — "local" or "datadog"
    LOG_SOURCE: str           = os.getenv("LOG_SOURCE", "local")
    LOG_FILE_PATH: str        = os.getenv("LOG_FILE_PATH", "../bank-application/logs/banking-app.log")
    LOG_CHECK_INTERVAL: float = float(os.getenv("LOG_CHECK_INTERVAL", "2"))

    # Datadog credentials (required when LOG_SOURCE=datadog)
    DD_API_KEY: str = os.getenv("DD_API_KEY", "")
    DD_APP_KEY: str = os.getenv("DD_APP_KEY", "")
    DD_SITE: str    = os.getenv("DD_SITE", "us5.datadoghq.com")
    DD_QUERY: str   = os.getenv("DD_QUERY", "service:banking-app")

    # Application
    APP_ENV: str       = os.getenv("APP_ENV", "development")
    APP_LOG_LEVEL: str = os.getenv("APP_LOG_LEVEL", "INFO")

    def validate(self) -> None:
        if not self.ANTHROPIC_API_KEY or self.ANTHROPIC_API_KEY == "your_anthropic_api_key_here":
            raise EnvironmentError("ANTHROPIC_API_KEY is not set in .env")
        if self.LOG_SOURCE == "datadog" and not (self.DD_API_KEY and self.DD_APP_KEY):
            raise EnvironmentError(
                "LOG_SOURCE=datadog requires DD_API_KEY and DD_APP_KEY to be set in .env"
            )
        if not self.DB_PASSWORD and self.APP_ENV != "development":
            raise EnvironmentError("DB_PASSWORD is not set in .env")


settings = Settings()
