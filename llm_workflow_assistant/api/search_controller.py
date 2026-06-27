from fastapi import APIRouter
from rag.rag_retriever import search_docs
router=APIRouter(prefix='/search')
@router.get('/')
async def search(q:str): return search_docs(q)