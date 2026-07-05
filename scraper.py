import os
import re
import time
import requests
import tiktoken
from markdownify import markdownify as md


API_URL = "https://support.optisigns.com/api/v2/help_center/en-us/articles.json"
OUTPUT_DIR = "articles"
PER_PAGE = 30
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 5


MAX_CHUNK_SIZE_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 120
CHUNK_STRIDE_TOKENS = MAX_CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS  # 280

_ENCODER = tiktoken.get_encoding("cl100k_base")


def inject_url_into_sections(markdown_content: str, html_url: str) -> str:

    if not html_url:
        return markdown_content

    url_line = f"Article URL: {html_url}"
    paragraphs = markdown_content.split("\n\n")

    out_paragraphs = []
    token_count = 0

    for para in paragraphs:
        out_paragraphs.append(para)
        token_count += len(_ENCODER.encode(para))

        if token_count >= CHUNK_STRIDE_TOKENS:
            out_paragraphs.append(url_line)
            token_count = 0

    return "\n\n".join(out_paragraphs)


def fetch_with_retry(session: requests.Session, url: str):
    """GET a URL with retry/backoff, handling Zendesk rate limiting (429)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"Request error ({e}); retrying "
                  f"({attempt}/{MAX_RETRIES})...")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", RETRY_BACKOFF_SECONDS))
            print(f"Rate limited (429); waiting {wait}s "
                  f"({attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts.")


def fetch_all_articles(target_article_count=None):
    
    session = requests.Session()
    next_url = f"{API_URL}?per_page={PER_PAGE}"
    fetched = 0

    while next_url:
        if target_article_count and fetched >= target_article_count:
            return

        data = fetch_with_retry(session, next_url)
        for article in data.get("articles", []):
            if target_article_count and fetched >= target_article_count:
                return
            if article.get("draft", False):
                continue
            fetched += 1
            yield article

        next_url = data.get("next_page")


def convert_article_to_markdown(article: dict) -> tuple[str, str]:
    
    title = article.get("title", "Untitled")
    html_body = article.get("body", "") or ""
    html_url = article.get("html_url", "")
    article_id = article.get("id")

    if html_url:
        slug = html_url.rstrip("/").split("/")[-1]
    else:
        slug = f"article_{article_id}"

    if not html_body.strip():
        return slug, None

    markdown_content = md(html_body, heading_style="ATX")
    markdown_content = re.sub(
        r"(?im)^\s*null\s*$", "", markdown_content
    ).strip()
    markdown_content = inject_url_into_sections(markdown_content, html_url)

    final_content = f"""# {title}

Article URL: {html_url}

{markdown_content}

---
Article URL: {html_url}
"""
    return slug, final_content


def scrape_to_markdown(target_article_count=None, output_dir=OUTPUT_DIR):
    
    os.makedirs(output_dir, exist_ok=True)

    articles_fetched = 0
    articles_skipped = 0

    print("Starting download...")

    for article in fetch_all_articles(target_article_count):
        slug, content = convert_article_to_markdown(article)

        if content is None:
            print(f"Skipping empty article: {article.get('title', slug)}")
            articles_skipped += 1
            continue

        filepath = os.path.join(output_dir, f"{slug}.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        articles_fetched += 1
        print(f"[{articles_fetched}] Saved: {slug}.md")

    print(f"\nDone! Downloaded {articles_fetched} articles "
          f"(skipped {articles_skipped} draft/empty).")


if __name__ == "__main__":
    scrape_to_markdown()
