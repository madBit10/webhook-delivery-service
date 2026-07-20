from fastapi import FastAPI
from app.api.routes import health
from app.api.routes import endpoint
from app.api.routes import event
from app.api.routes import dlq
from fastapi.middleware.cors import CORSMiddleware # import the cors from the fastapi


app = FastAPI()

# adding the cors so the browser can call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(health.router)
app.include_router(endpoint.router)
app.include_router(event.router)
app.include_router(dlq.router)
