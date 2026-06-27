from fastapi import APIRouter
from db.pg_connection import get_connection
router=APIRouter(prefix='/download')
@router.get('/')
async def download(name:str): conn=get_connection();cur=conn.cursor();cur.execute('SELECT content FROM ai_rag_document WHERE record_name=%s',(name,)); return cur.fetchone()