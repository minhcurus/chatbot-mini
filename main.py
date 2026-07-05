import json
import os
import sys
from contextlib import ExitStack

from dotenv import load_dotenv

from scraper import fetch_all_articles, convert_article_to_markdown, OUTPUT_DIR
from manifest_sync import pull_latest_manifest, push_manifest
from upload_vector import (
    client,
    get_or_create_vector_store,
    upload_single_file,
    delete_file,
    estimate_chunks_for_file,
    CHUNKING_STRATEGY,
    VECTOR_STORE_NAME,
    ASSISTANT_ID,
)

load_dotenv()

MANIFEST_PATH = "manifest.json"


def load_manifest() -> dict:
    if not os.path.exists(MANIFEST_PATH):
        return {}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def attach_vector_store_to_assistant(vector_store_id: str):

    if not ASSISTANT_ID:
        print("Warning: OPENAI_ASSISTANT_ID not set; skipping assistant attach.")
        return
    client.beta.assistants.update(
        assistant_id=ASSISTANT_ID,
        tool_resources={"file_search": {"vector_store_ids": [vector_store_id]}},
    )
    print(f"Assistant {ASSISTANT_ID} attached to vector store {vector_store_id}")


def cold_start_batch_upload(vector_store_id: str):

    entries = []  # (article_id, slug, filepath, updated_at)
    total_chunks = 0

    for article in fetch_all_articles():
        article_id = str(article.get("id"))
        updated_at = article.get("updated_at")
        slug, content = convert_article_to_markdown(article)
        if content is None:
            continue

        filepath = os.path.join(OUTPUT_DIR, f"{slug}.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        total_chunks += estimate_chunks_for_file(content)
        entries.append((article_id, slug, filepath, updated_at))

    if not entries:
        return {}, 0, 0

    with ExitStack() as stack:
        file_streams = [
            stack.enter_context(open(filepath, "rb"))
            for _, _, filepath, _ in entries
        ]
        file_batch = client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store_id,
            files=file_streams,
            chunking_strategy=CHUNKING_STRATEGY,
        )

    if file_batch.file_counts.failed > 0:
        raise RuntimeError(
            f"{file_batch.file_counts.failed} file(s) failed in the "
            f"cold-start batch upload."
        )

    # The batch preserves each uploaded file's original filename, but not
    # our article_id, so map filename -> vector_store_file.id to rebuild
    # the manifest.
    filename_to_file_id = {}
    after = None
    while True:
        page = client.vector_stores.file_batches.list_files(
            vector_store_id=vector_store_id,
            batch_id=file_batch.id,
            after=after,
            limit=100,
        )
        for vs_file in page.data:
            source_file = client.files.retrieve(vs_file.id)
            filename_to_file_id[source_file.filename] = vs_file.id
        if not getattr(page, "has_more", False):
            break
        after = page.data[-1].id

    new_manifest = {}
    for article_id, slug, filepath, updated_at in entries:
        filename = os.path.basename(filepath)
        file_id = filename_to_file_id.get(filename)
        if file_id is None:
            print(f"Warning: could not resolve vector file id for {filename}")
            continue
        new_manifest[article_id] = {
            "slug": slug,
            "updated_at": updated_at,
            "vector_file_id": file_id,
        }

    return new_manifest, len(new_manifest), total_chunks


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Render's cron container has no persistent disk, so pull the latest
    # manifest.json committed by the previous run before doing anything else.
    pull_latest_manifest()

    manifest = load_manifest()
    vector_store = get_or_create_vector_store(VECTOR_STORE_NAME)
    print(f"Vector Store: {vector_store.id} ({VECTOR_STORE_NAME})")

    total_chunks_processed = 0

    if not manifest:
        # True first run: nothing to diff against, so batch-upload
        # everything in one request instead of looping file-by-file.
        print("No manifest found - first run, using batch upload mode.")
        new_manifest, added, total_chunks_processed = cold_start_batch_upload(
            vector_store.id
        )
        updated = removed = skipped = 0

    else:
        new_manifest = {}
        added = updated = skipped = removed = 0
        seen_ids = set()

        for article in fetch_all_articles():
            article_id = str(article.get("id"))
            updated_at = article.get("updated_at")
            seen_ids.add(article_id)

            slug, content = convert_article_to_markdown(article)
            filepath = os.path.join(OUTPUT_DIR, f"{slug}.md")

            prev = manifest.get(article_id)

            if content is None:
                # Article now has no usable body (e.g. emptied out) - treat
                # like a removal if we previously had it embedded.
                if prev and prev.get("vector_file_id"):
                    delete_file(vector_store.id, prev["vector_file_id"])
                    removed += 1
                    print(f"[REMOVED] {slug} (now empty)")
                continue

            if prev is None:
                # ADDED: never seen this article_id before
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                file_id = upload_single_file(vector_store.id, filepath)
                total_chunks_processed += estimate_chunks_for_file(content)
                new_manifest[article_id] = {
                    "slug": slug,
                    "updated_at": updated_at,
                    "vector_file_id": file_id,
                }
                added += 1
                print(f"[ADDED] {slug}")

            elif prev.get("updated_at") != updated_at:
                # UPDATED: content changed since last run
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                if prev.get("vector_file_id"):
                    delete_file(vector_store.id, prev["vector_file_id"])
                file_id = upload_single_file(vector_store.id, filepath)
                total_chunks_processed += estimate_chunks_for_file(content)
                new_manifest[article_id] = {
                    "slug": slug,
                    "updated_at": updated_at,
                    "vector_file_id": file_id,
                }
                updated += 1
                print(f"[UPDATED] {slug}")

            else:
                # SKIPPED: unchanged since last run - carry manifest entry over
                new_manifest[article_id] = prev
                skipped += 1

        # Articles that existed in the previous manifest but weren't seen in
        # this run's fetch at all (deleted/unpublished on the Help Center side)
        for old_id, entry in manifest.items():
            if old_id not in seen_ids and entry.get("vector_file_id"):
                delete_file(vector_store.id, entry["vector_file_id"])
                removed += 1
                print(f"[REMOVED] {entry.get('slug', old_id)} (no longer published)")

    save_manifest(new_manifest)
    push_manifest()

    # Idempotent safety net: make sure the Assistant is wired up to this
    # vector store even if this environment never ran upload_vector.py's
    # manual setup step.
    attach_vector_store_to_assistant(vector_store.id)

    print("\n=== Daily job summary ===")
    print(f"Added   : {added}")
    print(f"Updated : {updated}")
    print(f"Removed : {removed}")
    print(f"Skipped : {skipped}")
    print(f"Chunks embedded (added/updated) : ~{total_chunks_processed}")
    print(f"Total tracked articles: {len(new_manifest)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Job failed: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)