import ollama, os
from db.pg_connection import get_connection
def search_docs(query): conn=get_connection();cur=conn.cursor(); qemb=ollama.embeddings(model=os.getenv('EMBED_MODEL'),prompt=query)['embedding']; cur.execute('SELECT record_name,content,embedding <-> %s FROM ai_rag_document ORDER BY embedding <-> %s LIMIT 5',(qemb,qemb)); return cur.fetchall()