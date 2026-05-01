import os
import requests
from dotenv import load_dotenv

load_dotenv()

ENDPOINT = os.getenv("AZURE_COHERE_RERANK_ENDPOINT")
API_KEY = os.getenv("AZURE_COHERE_API_KEY")

query = "What is machine learning?"

chunks = [
    {"id": "c1", "text": "Machine learning is a subset of AI where models learn from data."},
    {"id": "c2", "text": "The Eiffel Tower is located in Paris, France."},
    {"id": "c3", "text": "Deep learning uses neural networks with many layers to learn representations."},
    {"id": "c4", "text": "Python is a popular programming language used in data science."},
    {"id": "c5", "text": "Supervised learning trains on labeled data to predict outputs."},
]

print(f"Endpoint : {ENDPOINT}")
print(f"API key  : {'SET' if API_KEY else 'MISSING'}")
print(f"Query    : {query}\n")

try:
    response = requests.post(
        ENDPOINT,
        headers={"api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "model": "Cohere-rerank-v4.0-fast",
            "query": query,
            "documents": [c["text"] for c in chunks],
            "top_n": 3,
        },
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()["results"]

    print("Reranked top 3:")
    for rank, r in enumerate(results, 1):
        original = chunks[r["index"]]
        print(f"  {rank}. [score={r['relevance_score']:.4f}] {original['text']}")

except Exception as e:
    print(f"ERROR: {e}")
    if hasattr(e, "response") and e.response is not None:
        print(f"Response body: {e.response.text}")
