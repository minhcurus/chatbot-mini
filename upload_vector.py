import glob
import math
import os
from contextlib import ExitStack

import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()

ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
OUTPUT_DIR = "articles"
VECTOR_STORE_NAME = "OptiSigns_Help_Center_Docs"


CHUNKING_STRATEGY = {
    "type": "static",
    "static": {
        "max_chunk_size_tokens": 400,
        "chunk_overlap_tokens": 120,
    },
}

_ENCODER = tiktoken.get_encoding("cl100k_base")


def estimate_chunks_for_file(text: str) -> int:

    max_size = CHUNKING_STRATEGY["static"]["max_chunk_size_tokens"]
    overlap = CHUNKING_STRATEGY["static"]["chunk_overlap_tokens"]
    stride = max_size - overlap

    n_tokens = len(_ENCODER.encode(text))
    if n_tokens <= max_size:
        return 1
    return math.ceil((n_tokens - max_size) / stride) + 1


def get_or_create_vector_store(name: str):

    after = None
    while True:
        page = client.vector_stores.list(after=after, limit=100)
        for store in page.data:
            if store.name == name:
                return store
        if not getattr(page, "has_more", False):
            break
        after = page.data[-1].id

    return client.vector_stores.create(name=name)


def upload_single_file(vector_store_id: str, filepath: str) -> str:

    with open(filepath, "rb") as fh:
        vs_file = client.vector_stores.files.upload_and_poll(
            vector_store_id=vector_store_id,
            file=fh,
            chunking_strategy=CHUNKING_STRATEGY,
        )
    if vs_file.status != "completed":
        raise RuntimeError(f"Upload failed for {filepath}: status={vs_file.status}")
    return vs_file.id


def delete_file(vector_store_id: str, file_id: str):

    client.vector_stores.files.delete(
        vector_store_id=vector_store_id,
        file_id=file_id,
    )
    client.files.delete(file_id)


def main():
    if not ASSISTANT_ID:
        raise ValueError("Missing OPENAI_ASSISTANT_ID in .env")

    md_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "*.md")))
    if not md_files:
        raise FileNotFoundError(f"No Markdown files found in '{OUTPUT_DIR}'.")

    # Estimate chunk count locally, using the same window math as the
    # vector store's static chunking strategy, before uploading.
    total_estimated_chunks = 0
    for path in md_files:
        with open(path, "r", encoding="utf-8") as fh:
            total_estimated_chunks += estimate_chunks_for_file(fh.read())

    vector_store = get_or_create_vector_store(VECTOR_STORE_NAME)

    with ExitStack() as stack:
        file_streams = [
            stack.enter_context(open(path, "rb"))
            for path in md_files
        ]
        file_batch = client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store.id,
            files=file_streams,
            chunking_strategy=CHUNKING_STRATEGY,
        )

    if file_batch.file_counts.failed > 0:
        raise RuntimeError(
            f"{file_batch.file_counts.failed} file(s) failed to upload."
        )

    client.beta.assistants.update(
        assistant_id=ASSISTANT_ID,
        tool_resources={
            "file_search": {
                "vector_store_ids": [vector_store.id]
            }
        },
    )

    # Required log: how many files and chunks were embedded
    print(f"Files embedded  : {file_batch.file_counts.completed}")
    print(f"Chunks embedded : ~{total_estimated_chunks} "
          f"(estimated; static chunking, "
          f"max_chunk_size_tokens={CHUNKING_STRATEGY['static']['max_chunk_size_tokens']}, "
          f"chunk_overlap_tokens={CHUNKING_STRATEGY['static']['chunk_overlap_tokens']})")


if __name__ == "__main__":
    main()

