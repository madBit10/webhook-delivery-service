from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    class Config: 
        env_file = ".env"
        extra = "ignore"

settings = Settings()