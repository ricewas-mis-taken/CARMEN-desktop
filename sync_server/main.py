"""FastAPI app entrypoint. Run with:
    uvicorn sync_server.main:app --reload --port 8420
See README.md for env var setup and test_sync_manual.md for a manual
end-to-end test sequence.
"""
from fastapi import FastAPI

from .sync import router as sync_router

app = FastAPI(title="CARMEN sync_server")


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(sync_router)
