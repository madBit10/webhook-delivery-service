from fastapi import FastAPI
from app.api.routes import health
from app.api.routes import endpoint

app = FastAPI()

app.include_router(health.router)
app.include_router(endpoint.router)
