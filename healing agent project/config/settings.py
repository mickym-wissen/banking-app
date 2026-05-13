import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # MySQL
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_NAME: str = os.getenv("DB_NAME", "healing_agent_db")
    DB_USER: str = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # Log monitoring
    LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", "../bank-application/logs/banking-app.log")  # default matches repo layout
    LOG_CHECK_INTERVAL: float = float(os.getenv("LOG_CHECK_INTERVAL", "2"))

    # Application
    APP_ENV: str = os.getenv("APP_ENV", "development")
    APP_LOG_LEVEL: str = os.getenv("APP_LOG_LEVEL", "INFO")

    def validate(self) -> None:
        if not self.GEMINI_API_KEY:
            raise EnvironmentError("GEMINI_API_KEY is not set in .env")
        if not self.DB_PASSWORD and self.APP_ENV != "development":
            raise EnvironmentError("DB_PASSWORD is not set in .env")


settings = Settings()
