"""Configuration management for the VoiceAPI backend."""

import os
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class DatabaseConfig:
    """Database configuration settings."""

    def __init__(self):
        self.user = os.getenv("POSTGRES_USER", "user")
        self.password = os.getenv("POSTGRES_PASSWORD", "password")
        self.host = os.getenv(
            "POSTGRES_HOST", "db"
        )  # "db" for Docker, "localhost" for local
        self.port = os.getenv("POSTGRES_PORT", "5432")
        self.database = os.getenv("POSTGRES_DB", "voiceapi")

        # Override with DATABASE_URL if provided
        self.url = os.getenv(
            "DATABASE_URL",
            f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}",
        )

    def get_url(self) -> str:
        """Get the complete database URL."""
        return self.url

    def __repr__(self) -> str:
        # Don't expose password in repr
        return f"DatabaseConfig(host={self.host}, port={self.port}, database={self.database}, user={self.user})"


class AppConfig:
    """Application configuration settings."""

    def __init__(self):
        self.host = os.getenv("API_HOST", "0.0.0.0")
        self.port = int(os.getenv("API_PORT", "8000"))
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # Database configuration
        self.database = DatabaseConfig()

        # ZMQ port configurations
        self.audio_port = os.getenv("AUDIO_PORT", "8001")
        self.asr_port = os.getenv("ASR_PORT", "8003")
        self.sid_port = os.getenv("SID_PORT", "8004")
        self.kws_port = os.getenv("KWS_PORT", "8005")
        self.trans_port = os.getenv("TRANS_PORT", "8007")
        self.agent_port = os.getenv("AGENT_PORT", "8008")

        # Address configurations (for Docker vs local)
        self.is_docker = os.getenv("DOCKER_MODE", "false").lower() == "true"
        if self.is_docker:
            self.audio_address = "*"
            self.asr_address = "asr"
            self.sid_address = "sid"
            self.kws_address = "kws"
            self.trans_address = "*"
            self.agent_address = "agent"
        else:
            self.audio_address = "127.0.0.1"
            self.asr_address = "127.0.0.1"
            self.sid_address = "127.0.0.1"
            self.kws_address = "127.0.0.1"
            self.trans_address = "127.0.0.1"
            self.agent_address = "127.0.0.1"


def get_config() -> AppConfig:
    """Get the global configuration instance."""
    return AppConfig()


# Load config immediately when module is imported
config = get_config()


# This function can be called from main() to override with CLI args
def update_config(**kwargs):
    """Update configuration with provided key-value pairs."""
    global config
    for key, value in kwargs.items():
        if hasattr(config, key) and value is not None:
            setattr(config, key, value)
        elif hasattr(config.database, key) and value is not None:
            setattr(config.database, key, value)


def is_production() -> bool:
    """Check if running in production mode."""
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def is_testing() -> bool:
    """Check if running in test mode."""
    return os.getenv("TESTING", "false").lower() == "true"
