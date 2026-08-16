"""FastAPI app entrypoint. Run with:
    uvicorn sync_server.main:app --reload --port 8420
See README.md for env var setup and test_sync_manual.md for a manual
end-to-end test sequence.
"""
import logging

from fastapi import FastAPI

from .sync import router as sync_router

# uvicorn only attaches handlers to its own "uvicorn.*" loggers, so
# sync_server's own logger (e.g. the PostgREST failure details in
# sync.py) needs its own handler to reliably show up in this terminal.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="CARMEN sync_server")


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(sync_router)
