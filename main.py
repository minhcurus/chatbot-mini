import json
import os
import sys

from dotenv import load_dotenv

from scraper import fetch_all_articles, convert_article_to_markdown, OUTPUT_DIR
from upload_vector import (
    get_or_create_vector_store,
    upload_single_file,
    delete_file,
    VECTOR_STORE_NAME,
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


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    manifest = load_manifest()
    new_manifest = {}

    vector_store = get_or_create_vector_store(VECTOR_STORE_NAME)
    print(f"Vector Store: {vector_store.id} ({VECTOR_STORE_NAME})")

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

    print("\n=== Daily job summary ===")
    print(f"Added   : {added}")
    print(f"Updated : {updated}")
    print(f"Removed : {removed}")
    print(f"Skipped : {skipped}")
    print(f"Total tracked articles: {len(new_manifest)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Job failed: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
