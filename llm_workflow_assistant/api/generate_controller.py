from fastapi import APIRouter
import ollama, os
router=APIRouter(prefix='/generate')
@router.post('/')
async def generate(req:str): return ollama.generate(model=os.getenv('FT_MODEL'),prompt=f'Generate test script: {req}')