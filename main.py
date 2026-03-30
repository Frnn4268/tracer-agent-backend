from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import chat

app = FastAPI(title="Cisco Packet Tracer Assistant", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)

@app.get("/")
async def root():
    return {
        "name": app.title,
        "version": app.version,
        "status": "ok",
        "docs": "/docs",
        "chat_endpoint": "/api/chat/",
        "allowed_origins": settings.FRONTEND_ORIGINS,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}