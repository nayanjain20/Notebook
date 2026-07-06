# Kafka RAG Evaluation

A fresh evaluation set built around the Apache Kafka demo, used to measure the
quality of the RAG pipeline (retrieval + grounded generation).

## What's here

| Artifact | Path |
|----------|------|
| Source corpus | `eval/docs/kafka_documentation.txt` (official Kafka docs: intro, use cases, quickstart) |
| Question set | `test_data/kafka_dataset.json` — 50 Q&A, each answerable from the corpus |
| Latest results | `eval/output/output_2026-07-06_1/` |

The 50 questions span the core Kafka concepts: event streaming, events,
producers/consumers, topics, partitions, ordering, replication, brokers, the
five APIs, common use cases, and getting started (Docker, CLI, Connect, Streams).

## Metrics (RAGAS)

| Metric | What it measures |
|--------|------------------|
| **Faithfulness** | Are the answer's claims supported by the retrieved context? (no hallucination) |
| **Answer Relevancy** | Does the answer actually address the question? |
| **Context Precision** | Were the retrieved chunks relevant (little noise)? |
| **Context Recall** | Did retrieval surface all the information the answer needed? |

## Latest results (50 samples)

| Metric | Score |
|--------|-------|
| Faithfulness | **0.94** |
| Answer Relevancy | **0.89** |
| Context Precision | **0.92** |
| Context Recall | **1.00** |

Context Recall of 1.0 means the retriever consistently pulled in the information
needed to answer — expected given a focused, single-source corpus. Faithfulness
and precision are high, indicating grounded answers with low retrieval noise.

## Known limitations / next steps

- **Yes/no questions** (e.g. "Do you usually need to implement your own
  connectors?") can score poorly on Answer Relevancy and Faithfulness — a known
  RAGAS artifact with short answers, not necessarily a pipeline fault. Worth
  rephrasing such questions or adding a metric better suited to them.
- **Single-source corpus** makes Context Recall easy; a multi-document set with
  distractor sources would stress retrieval more realistically.
- Add **multi-hop** questions that require combining two sections.
- Evaluate the **full agentic path** (reflect + critique loops), not just the
  single-pass retrieve→generate used here.

## Reproduce

```bash
# From ChatBot-Backend/
python -m eval.run_eval --ingest --dataset test_data/kafka_dataset.json
```

`--ingest` re-embeds the corpus into the eval vector store. Only top-level files
in `eval/docs/` are ingested (the `_archive/` subfolder is ignored), so the run
uses the Kafka corpus alone.
