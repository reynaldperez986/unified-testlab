from fastapi import APIRouter
import ollama, os
from rag.rag_retriever import search_docs
router=APIRouter(prefix='/chat')
@router.post('/')
async def chat(q:str): ctx=search_docs(q); context=' '.join([c[1] for c in ctx]); prompt=f'Context:{context}
User:{q}'; return ollama.generate(model=os.getenv('FT_MODEL'),prompt=prompt)