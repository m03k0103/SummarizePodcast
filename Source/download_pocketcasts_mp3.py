import argparse
import os
import re
import sys
import urllib.request
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def extract_download_url(page_html: str) -> str | None:
    patterns = [
        r'https://feeds\.soundcloud\.com/stream/[^"\'\s<>]+\.mp3[^"\'\s<>]*',
        r'https://[^"\'\s<>]+/stream/[^"\'\s<>]+\.mp3[^"\'\s<>]*',
        r'href=["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, page_html, re.IGNORECASE)
        if not matches:
            continue
        if pattern.startswith("href"):
            for href in matches:
                if ".mp3" in href.lower() or "stream" in href.lower():
                    if href.startswith("http"):
                        return href
        else:
            for candidate in matches:
                if candidate.lower().endswith(".mp3") or ".mp3" in candidate.lower():
                    return candidate

    for match in re.finditer(r'Download file', page_html, re.IGNORECASE):
        # Fallback: search the surrounding snippet for an href.
        start = max(0, match.start() - 300)
        end = min(len(page_html), match.end() + 600)
        snippet = page_html[start:end]
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', snippet, re.IGNORECASE)
        for href in hrefs:
            if href.startswith("http") and (href.lower().endswith(".mp3") or ".mp3" in href.lower()):
                return href

    return None


def choose_output_name(url: str, download_url: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if download_url:
        parsed = urlparse(download_url)
        base = os.path.basename(parsed.path)
        if base:
            return base
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        return f"{path_parts[-1]}.mp3"
    return "downloaded_audio.mp3"


def download_file(url: str, output_path: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, open(output_path, "wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Download an MP3 from a Pocket Casts episode URL")
    parser.add_argument("url", help="Pocket Casts episode URL")
    parser.add_argument("-o", "--output", help="Output file name")
    args = parser.parse_args()

    if not args.url.startswith("http"):
        print("Please provide a valid http(s) URL.", file=sys.stderr)
        return 2

    print(f"Fetching episode page: {args.url}")
    page_html = fetch_text(args.url)

    download_url = extract_download_url(page_html)
    if not download_url:
        print("Could not find a downloadable MP3 URL in the page.", file=sys.stderr)
        return 1

    output_path = choose_output_name(args.url, download_url, args.output)
    print(f"Resolved audio URL: {download_url}")
    print(f"Saving to: {output_path}")
    download_file(download_url, output_path)
    print("Download complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
