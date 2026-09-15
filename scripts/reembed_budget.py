"""Re-embed budget_data with OpenAI text-embedding-3-small (1536 dim) so it matches cbs_tables and policy_documents."""
import os
import chromadb
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv('/home/jouchuki2/hobby/capelle-repo-agent/.env')
PATH = '/home/jouchuki2/hobby/capelle-repo-agent/capelle_rag/chroma_db'

client = chromadb.PersistentClient(path=PATH)
old = client.get_collection('budget_data')
items = old.get(include=['documents', 'metadatas'])
print(f'Retrieved {len(items["ids"])} budget items from old 384-dim collection')

oai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
# Batch embed (small set, single call)
resp = oai.embeddings.create(model='text-embedding-3-small', input=items['documents'])
embeddings = [e.embedding for e in resp.data]
print(f'Embedded {len(embeddings)} items with OpenAI')

client.delete_collection('budget_data')
new = client.create_collection('budget_data', metadata={'embedding_model': 'openai-text-embedding-3-small'})
new.add(ids=items['ids'], documents=items['documents'], metadatas=items['metadatas'], embeddings=embeddings)
print(f'Re-inserted {new.count()} items into 1536-dim budget_data')
