"""Explicit embedding identity and dimensional validation; mock vectors are labeled."""
import hashlib
import math
import re
import config
from openai import OpenAI

DIMENSION = 2048


def embedding_model(mode):
    return 'mock-embedding-v1' if mode == 'mock' else config.EMBEDDING_MODEL


def validate_vector(vector):
    if len(vector) != DIMENSION or not all(math.isfinite(v) for v in vector) or sum(v*v for v in vector) <= 0:
        raise ValueError('invalid_embedding_vector')
    return vector


def embed(texts, mode):
    if not texts or len(texts) > 16 or any(not text.strip() for text in texts):
        raise ValueError('invalid_embedding_batch')
    if mode == 'mock':
        vectors=[]
        for text in texts:
            vector=[0.0]*DIMENSION
            for token in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]',text.lower()):
                digest=hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:2],'big')%DIMENSION]+=1.0
            if not any(vector):
                vector[0]=1.0
            vectors.append(validate_vector(vector))
        return vectors, {'total_tokens':0,'mode':'mock'}
    client=OpenAI(api_key=config.EMBEDDING_API_KEY,base_url=config.EMBEDDING_API_BASE,timeout=30,max_retries=0)
    result=client.embeddings.create(model=config.EMBEDDING_MODEL,input=texts,dimensions=DIMENSION)
    rows=sorted(result.data,key=lambda item:item.index)
    if len(rows)!=len(texts) or [r.index for r in rows]!=list(range(len(texts))):
        raise ValueError('incomplete_embedding_batch')
    return [validate_vector(r.embedding) for r in rows], result.usage.model_dump()
