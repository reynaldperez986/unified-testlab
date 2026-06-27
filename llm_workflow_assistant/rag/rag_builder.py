import ollama, os
from db.pg_connection import get_connection
def build_rag(): conn=get_connection();cur=conn.cursor();cur.execute('SELECT record_name, step_text FROM steps'); rows=cur.fetchall(); grouped={};
 for name,step in rows: grouped.setdefault(name,[]).append(step);
 for name,steps in grouped.items(): text='\n'.join(steps); emb=ollama.embeddings(model=os.getenv('EMBED_MODEL'), prompt=text)['embedding']; cur.execute('INSERT INTO ai_rag_document(record_name,content,embedding) VALUES (%s,%s,%s)',(name,text,emb)); conn.commit();