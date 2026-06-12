from fastapi import FastAPI
from app.api.routes import health

app = FastAPI()

app.include_router(health.router)
