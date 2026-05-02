"""
RAG Evaluation with RAGAS
=========================
Each run produces an output directory:
  eval/output/output_<date>_<runno>/
      agent_responses.json   -- what the RAG pipeline returned for every test case
      eval_results.json      -- RAGAS scores + remarks per rule, per test case

Test dataset lives outside the eval pipeline:
  test_data/test_dataset.json   (default, override with --dataset)

Usage:
  python -m eval.run_eval
  python -m eval.run_eval --ingest                        # force re-embed docs
  python -m eval.run_eval --dataset path/to/dataset.json  # alternate dataset
"""

import os
import json
import math
import warnings
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore", category=DeprecationWarning)

from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from ragas import evaluate, EvaluationDataset
from ragas.dataset_schema import SingleTurnSample
from ragas.metrics import (
    Faithfulness,
    AnswerRelevancy,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
)
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper

from eval.pipeline import ingest_all, retrieve, generate, collection_exists, EVAL_COLLECTION

# ── Paths ─────────────────────────────────────────────────────────────────────
_EVAL_DIR    = os.path.dirname(__file__)
_BACKEND_DIR = os.path.join(_EVAL_DIR, "..")
DATASET_PATH = os.path.join(_BACKEND_DIR, "test_data", "test_dataset.json")
OUTPUT_DIR   = os.path.join(_EVAL_DIR, "output")

METRIC_KEYS = [
    "faithfulness",
    "answer_relevancy",
    "llm_context_precision_with_reference",
    "context_recall",
]

METRIC_DISPLAY = {
    "faithfulness":                         "Faithfulness",
    "answer_relevancy":                     "Answer Relevancy",
    "llm_context_precision_with_reference": "Context Precision",
    "context_recall":                       "Context Recall",
}


# ── Dataset ───────────────────────────────────────────────────────────────────
def load_dataset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Azure LLM / embeddings ────────────────────────────────────────────────────
def _ragas_llm():
    return LangchainLLMWrapper(AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
        temperature=0,
    ))


def _ragas_embeddings():
    return LangchainEmbeddingsWrapper(AzureOpenAIEmbeddings(
        azure_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    ))


# ── Ingestion ─────────────────────────────────────────────────────────────────
def ensure_ingested(force: bool = False):
    if not force and collection_exists(EVAL_COLLECTION):
        print(f"  [skip] '{EVAL_COLLECTION}' already ingested")
        return
    counts = ingest_all(EVAL_COLLECTION)
    for fname, n in counts.items():
        print(f"  [ok] '{fname}' -> {n} chunks into '{EVAL_COLLECTION}'")


# ── RAG pipeline ──────────────────────────────────────────────────────────────
def run_pipeline(samples: list[dict]) -> list[dict]:
    rows = []
    for i, s in enumerate(samples, 1):
        q = s["question"]
        print(f"  [{i}/{len(samples)}] {q}")
        contexts = retrieve(q, s["collection"], top_k=5)
        answer   = generate(q, contexts)
        rows.append({
            "id":                 i,
            "question":           q,
            "answer":             answer,
            "ground_truth":       s["ground_truth"],
            "contexts":           contexts,
        })
    return rows


# ── RAGAS scoring ─────────────────────────────────────────────────────────────
def run_ragas(rows: list[dict]):
    ragas_samples = [
        SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["ground_truth"],
        )
        for r in rows
    ]
    llm  = _ragas_llm()
    emb  = _ragas_embeddings()
    metrics = [
        Faithfulness(llm=llm),
        AnswerRelevancy(llm=llm, embeddings=emb),
        LLMContextPrecisionWithReference(llm=llm),
        LLMContextRecall(llm=llm),
    ]
    return evaluate(dataset=EvaluationDataset(samples=ragas_samples), metrics=metrics)


# ── Remarks ───────────────────────────────────────────────────────────────────
def _remarks(rule: str, score: float | None) -> str:
    if score is None:
        return "Score could not be computed for this sample."

    if rule == "faithfulness":
        if score == 1.0:
            return "All claims in the answer are fully supported by the retrieved context."
        if score >= 0.8:
            return f"Most claims are grounded in context. A small number of statements could not be verified against the retrieved chunks."
        if score >= 0.5:
            return f"About half the claims are grounded in context. Several statements go beyond what the retrieved chunks support."
        if score > 0:
            return f"Few claims are grounded in context. Most of the answer contains statements not supported by retrieved chunks."
        return "None of the answer claims could be verified against the retrieved context."

    if rule == "answer_relevancy":
        if score >= 0.95:
            return "Answer directly and completely addresses the question."
        if score >= 0.8:
            return "Answer is mostly relevant but may include slightly tangential detail."
        if score >= 0.6:
            return "Answer partially addresses the question. Some key aspects are missing or unrelated content is present."
        return "Answer does not adequately address the question asked."

    if rule == "llm_context_precision_with_reference":
        if score >= 0.9:
            return "Retrieved chunks are highly relevant; almost all context was useful for answering."
        if score >= 0.7:
            return "Most retrieved chunks were relevant, with some noise in the top results."
        if score >= 0.4:
            return "Mixed retrieval — roughly half the chunks were relevant. Some irrelevant chunks ranked above useful ones."
        if score > 0:
            return "Low precision. Most retrieved chunks were not directly relevant to the question."
        return "None of the retrieved chunks were ranked as relevant to the question."

    if rule == "context_recall":
        if score == 1.0:
            return "All information needed to answer the question was present in the retrieved context."
        if score >= 0.7:
            return "Most of the necessary information was retrieved. Some supporting details may have been missed."
        if score >= 0.4:
            return "Only part of the necessary information was retrieved. Key facts from the ground truth are missing."
        if score > 0:
            return "Very little of the needed information was retrieved."
        return "The retrieved context did not contain the information needed to answer this question."

    return f"Score: {score:.3f}"


# ── Output helpers ────────────────────────────────────────────────────────────
def _safe(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


def _next_run_number(date_str: str) -> int:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    prefix = f"output_{date_str}_"
    existing = [d for d in os.listdir(OUTPUT_DIR) if d.startswith(prefix)]
    if not existing:
        return 1
    nums = []
    for d in existing:
        try:
            nums.append(int(d[len(prefix):]))
        except ValueError:
            pass
    return max(nums) + 1 if nums else 1


# ── Save outputs ──────────────────────────────────────────────────────────────
def save_outputs(rows: list[dict], ragas_result, dataset_path: str) -> str:
    df          = ragas_result.to_pandas()
    date_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_no      = _next_run_number(date_str)
    run_dir     = os.path.join(OUTPUT_DIR, f"output_{date_str}_{run_no}")
    os.makedirs(run_dir, exist_ok=True)

    meta = {
        "run_date":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_number":  run_no,
        "dataset":     os.path.relpath(dataset_path, _BACKEND_DIR).replace("\\", "/"),
        "total_samples": len(rows),
    }

    # ── 1. agent_responses.json ───────────────────────────────────────────────
    agent_out = {
        **meta,
        "responses": [
            {
                "id":             r["id"],
                "question":       r["question"],
                "answer":         r["answer"],
                "ground_truth":   r["ground_truth"],
                "chunks_retrieved": len(r["contexts"]),
                "contexts":       r["contexts"],
            }
            for r in rows
        ],
    }
    with open(os.path.join(run_dir, "agent_responses.json"), "w", encoding="utf-8") as f:
        json.dump(agent_out, f, indent=2, ensure_ascii=False)

    # ── 2. eval_results.json ──────────────────────────────────────────────────
    aggregate = {
        key: _safe(df[key].dropna().mean()) if key in df.columns else None
        for key in METRIC_KEYS
    }

    results = []
    for i, row in enumerate(rows):
        rule_results = []
        for key in METRIC_KEYS:
            score = _safe(df.at[i, key]) if key in df.columns else None
            rule_results.append({
                "rule":       METRIC_DISPLAY[key],
                "metric_key": key,
                "score":      score,
                "remarks":    _remarks(key, score),
            })
        results.append({
            "id":          row["id"],
            "question":    row["question"],
            "rule_results": rule_results,
        })

    eval_out = {
        **meta,
        "aggregate_scores": {METRIC_DISPLAY[k]: aggregate[k] for k in METRIC_KEYS},
        "results": results,
    }
    with open(os.path.join(run_dir, "eval_results.json"), "w", encoding="utf-8") as f:
        json.dump(eval_out, f, indent=2, ensure_ascii=False)

    return run_dir


# ── Console summary ───────────────────────────────────────────────────────────
def print_summary(rows: list[dict], ragas_result):
    df = ragas_result.to_pandas()

    header  = "  ".join(f"{METRIC_DISPLAY[k][:8]:>8}" for k in METRIC_KEYS)
    divider = "-" * (52 + len(header))

    print("\n" + "=" * len(divider))
    print("  PER-SAMPLE SCORES")
    print("=" * len(divider))
    print(f"  {'#':>2}  {'Question':<45}  {header}")
    print(divider)

    for i, row in enumerate(rows):
        q_short = (row["question"][:43] + "..") if len(row["question"]) > 45 else row["question"]
        scores  = "  ".join(
            f"{_safe(df.at[i, k]) or 0:>8.3f}" if k in df.columns else f"{'N/A':>8}"
            for k in METRIC_KEYS
        )
        print(f"  {i+1:>2}  {q_short:<45}  {scores}")

    print(divider)
    print("\n  AGGREGATE")
    print(divider)
    agg_scores = "  ".join(
        f"{_safe(df[k].dropna().mean()) or 0:>8.3f}" if k in df.columns else f"{'N/A':>8}"
        for k in METRIC_KEYS
    )
    print(f"  {'':>2}  {'':45}  {agg_scores}")
    print("=" * len(divider))


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Run RAG evaluation with RAGAS")
    parser.add_argument("--ingest",   action="store_true", help="Force re-ingest documents")
    parser.add_argument("--dataset",  default=DATASET_PATH, help="Path to test dataset JSON")
    args = parser.parse_args()

    print(f"\n--- Loading dataset -----------------------------------------------")
    print(f"  {args.dataset}")
    samples = load_dataset(args.dataset)
    print(f"  {len(samples)} samples loaded")

    print("\n--- Step 1: Ingesting documents -----------------------------------")
    ensure_ingested(force=args.ingest)

    print("\n--- Step 2: Running RAG pipeline ----------------------------------")
    rows = run_pipeline(samples)

    print("\n--- Step 3: Scoring with RAGAS ------------------------------------")
    ragas_result = run_ragas(rows)

    print_summary(rows, ragas_result)

    print("\n--- Saving outputs ------------------------------------------------")
    run_dir = save_outputs(rows, ragas_result, args.dataset)
    print(f"  output dir  : {run_dir}")
    print(f"  agent output: {os.path.join(run_dir, 'agent_responses.json')}")
    print(f"  eval output : {os.path.join(run_dir, 'eval_results.json')}")


if __name__ == "__main__":
    main()
