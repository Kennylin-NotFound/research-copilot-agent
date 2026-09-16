"""Quick direct chromadb test to diagnose add() crash."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chromadb
print(f"chromadb version: {chromadb.__version__}", flush=True)

c = chromadb.EphemeralClient()
print("EphemeralClient OK", flush=True)

col = c.get_or_create_collection("test")
print("Collection OK", flush=True)

import numpy as np
emb = [[0.1] * 384]

col.add(
    documents=["hello world"],
    ids=["doc1"],
    embeddings=emb,
)
print("add() OK", flush=True)

r = col.query(query_embeddings=emb, n_results=1)
print(f"query() OK: {r['documents']}", flush=True)
