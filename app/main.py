from fastapi import FastAPI
from app.api.routes import health
from app.api.routes import endpoint
from app.api.routes import event
from app.api.routes import dlq

app = FastAPI()

app.include_router(health.router)
app.include_router(endpoint.router)
app.include_router(event.router)
app.include_router(dlq.router)
