"""
RAGAS Testset Generator
=======================
1. Clears eval_chroma_db
2. Ingests all docs from eval/docs/ using the unstructured pipeline
3. Uses RAGAS TestsetGenerator to synthesize 20 QA pairs
4. Writes to test_data/test_dataset.json  (overwrites)

Usage:
  python -m eval.generate_testset
"""

import os
import json
import warnings
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore", category=DeprecationWarning)

from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

# RAGAS 0.4.3 calls agenerate_prompt which was removed in langchain-core >= 0.3.
# Patch it back onto BaseChatModel using the still-present agenerate method.
from langchain_core.language_models.chat_models import BaseChatModel
if not hasattr(BaseChatModel, "agenerate_prompt"):
    async def _agenerate_prompt_compat(self, prompts, stop=None, callbacks=None, **kwargs):
        messages = [p.to_messages() for p in prompts]
        return await self.agenerate(messages, stop=stop, callbacks=callbacks, **kwargs)
    BaseChatModel.agenerate_prompt = _agenerate_prompt_compat
from langchain_core.documents import Document
from ragas.testset import TestsetGenerator
from ragas.testset.synthesizers import (
    SingleHopSpecificQuerySynthesizer,
    MultiHopSpecificQuerySynthesizer,
)
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper

from eval.pipeline import (
    clear_collection,
    ingest_all,
    EVAL_COLLECTION,
    EVAL_DOCS_DIR,
)

_BACKEND_DIR  = os.path.join(os.path.dirname(__file__), "..")
DATASET_OUT   = os.path.join(_BACKEND_DIR, "test_data", "test_dataset.json")
TESTSET_SIZE  = 20


def _raw_llm():
    return AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
        temperature=0,
    )


def _llm():
    return LangchainLLMWrapper(_raw_llm())


def _raw_embeddings():
    return AzureOpenAIEmbeddings(
        azure_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    )


def _embeddings():
    return LangchainEmbeddingsWrapper(_raw_embeddings())


def load_langchain_docs():
    """Load all text files from eval/docs/ as LangChain Documents."""
    docs = []
    for fname in sorted(os.listdir(EVAL_DOCS_DIR)):
        fpath = os.path.join(EVAL_DOCS_DIR, fname)
        if os.path.isfile(fpath):
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            doc = Document(page_content=content, metadata={"doc_file": fname, "source": fpath})
            docs.append(doc)
            print(f"  loaded '{fname}'")
    return docs


def generate_testset(docs) -> list[dict]:
    llm  = _llm()            # LangchainLLMWrapper — for synthesizers
    emb  = _embeddings()     # LangchainEmbeddingsWrapper — for synthesizers

    # from_langchain expects the raw langchain objects, not RAGAS wrappers
    generator = TestsetGenerator.from_langchain(llm=_raw_llm(), embedding_model=_raw_embeddings())

    query_distribution = [
        (SingleHopSpecificQuerySynthesizer(llm=llm), 0.70),
        (MultiHopSpecificQuerySynthesizer(llm=llm),  0.30),
    ]

    testset = generator.generate_with_langchain_docs(
        documents=docs,
        testset_size=TESTSET_SIZE,
        query_distribution=query_distribution,
        raise_exceptions=False,
    )

    df = testset.to_pandas()
    print(f"\n  Generated {len(df)} test cases")
    print(f"  Columns: {df.columns.tolist()}")

    samples = []
    for _, row in df.iterrows():
        question     = str(row.get("user_input", row.get("question", ""))).strip()
        ground_truth = str(row.get("reference", row.get("ground_truth", ""))).strip()
        synthesizer  = str(row.get("synthesizer_name", "unknown"))

        if not question or not ground_truth:
            continue

        samples.append({
            "question":    question,
            "ground_truth": ground_truth,
            "collection":  EVAL_COLLECTION,
            "synthesizer": synthesizer,
        })

    return samples


def main():
    print("\n--- Step 1: Clearing eval vector DB ------------------------------")
    clear_collection(EVAL_COLLECTION)
    print(f"  Cleared collection '{EVAL_COLLECTION}'")

    print("\n--- Step 2: Ingesting docs from eval/docs/ -----------------------")
    counts = ingest_all(EVAL_COLLECTION)
    for fname, n in counts.items():
        print(f"  '{fname}' -> {n} chunks")

    print("\n--- Step 3: Loading docs for RAGAS ------------------------------")
    lc_docs = load_langchain_docs()
    print(f"  {len(lc_docs)} LangChain document(s) loaded")

    print("\n--- Step 4: Generating testset with RAGAS -----------------------")
    samples = generate_testset(lc_docs)

    print(f"\n--- Step 5: Writing {len(samples)} samples to test_dataset.json --")
    os.makedirs(os.path.dirname(DATASET_OUT), exist_ok=True)
    with open(DATASET_OUT, "w", encoding="utf-8") as f:
        json.dump(samples, f, indent=2, ensure_ascii=False)
    print(f"  Written to: {DATASET_OUT}")

    print("\n  Preview (first 3 samples):")
    for s in samples[:3]:
        print(f"  [{s['synthesizer']}]")
        print(f"    Q:  {s['question'][:100]}")
        print(f"    GT: {s['ground_truth'][:100]}")


if __name__ == "__main__":
    main()
