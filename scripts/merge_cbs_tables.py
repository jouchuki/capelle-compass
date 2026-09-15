"""Copy cbs_tables collection (1536 dim, OpenAI) from ~/.local/share/cbs-tool/chroma_db into the unified capelle_rag/chroma_db. Embeddings already correct; direct copy."""
import chromadb

SRC = '/home/jouchuki2/.local/share/cbs-tool/chroma_db'
DST = '/home/jouchuki2/hobby/capelle-repo-agent/capelle_rag/chroma_db'

src_client = chromadb.PersistentClient(path=SRC)
dst_client = chromadb.PersistentClient(path=DST)

src_col = src_client.get_collection('cbs_tables')
total = src_col.count()
print(f'Source cbs_tables: {total} items')

# Drop existing cbs_tables in unified DB if present
try:
    dst_client.delete_collection('cbs_tables')
    print('Removed existing cbs_tables in unified DB')
except Exception:
    pass

dst_col = dst_client.create_collection('cbs_tables', metadata={'embedding_model': 'openai-text-embedding-3-small'})

BATCH = 500
for offset in range(0, total, BATCH):
    batch = src_col.get(limit=BATCH, offset=offset, include=['documents', 'metadatas', 'embeddings'])
    if not batch['ids']:
        break
    dst_col.add(
        ids=batch['ids'],
        documents=batch['documents'],
        metadatas=batch['metadatas'],
        embeddings=batch['embeddings'],
    )
    print(f'Copied batch {offset+len(batch["ids"])}/{total}')
print(f'Done. Dst cbs_tables: {dst_col.count()} items')
