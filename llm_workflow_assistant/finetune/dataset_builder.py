import json
from db.pg_connection import get_connection
def build_dataset(): conn=get_connection();cur=conn.cursor();cur.execute('SELECT record_name,content FROM ai_rag_document');rows=cur.fetchall(); f=open('fine_tune.jsonl','w');
 for n,c in rows: f.write(json.dumps({'instruction':f'Generate test similar to {n}','input':'','output':c})+'
');