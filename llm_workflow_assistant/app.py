from fastapi import FastAPI
from api.chat_controller import router as chat_router
from api.search_controller import router as search_router
from api.generate_controller import router as gen_router
from api.download_controller import router as dl_router
app = FastAPI()
app.include_router(chat_router)
app.include_router(search_router)
app.include_router(gen_router)
app.include_router(dl_router)