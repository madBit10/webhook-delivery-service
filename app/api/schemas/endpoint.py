from pydantic import BaseModel, ConfigDict, HttpUrl

class EndpointCreate(BaseModel):
    url: HttpUrl
    event_types: str

class EndpointRead(BaseModel):
    id: int
    url: str
    event_types: str
    is_active: bool

    model_config = ConfigDict(
        from_attributes=True
    )