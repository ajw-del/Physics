"""
유튜브 자막 인덱싱 스크립트

역할: videos.txt에 있는 유튜브 링크들의 자막을 가져와서
      문단 단위로 쪼갠 뒤 Gemini 임베딩을 배치로 생성하고
      ../docs/data/index.json 으로 저장합니다.

실행 방법:
  1. pip install -r requirements.txt
  2. 환경변수로 본인 Gemini API 키를 설정
     (Mac/Linux) export GEMINI_API_KEY="본인키"
     (Windows)   $env:GEMINI_API_KEY="본인키"
  3. python ingest.py

영상을 나중에 추가하고 싶으면 videos.txt에 링크만 추가하고
이 스크립트를 다시 실행하면 됩니다. (이미 완전히 인덱싱된 영상은 건너뜁니다)

무료 API 한도(하루 요청 횟수 등)에 걸리면 스크립트가 자동으로 잠시 기다렸다가
재시도하고, 그래도 안 되면 지금까지 처리된 것만 저장하고 안전하게 멈춥니다.
이 경우 다음날 다시 실행하면 끝난 영상은 건너뛰고 이어서 처리됩니다.
"""

import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import defaultdict

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

# ---- 설정 ----
CHUNK_CHAR_LIMIT = 800  # 청크 하나당 최대 글자 수
TRANSCRIPT_DELAY_RANGE = (2, 5)  # 영상 간 자막 요청 간격(초), IP 차단 예방용
EMBED_MODEL = "gemini-embedding-001"
BATCH_SIZE = 50  # 한 번의 API 호출로 임베딩할 청크 개수
MAX_RETRIES = 5  # 요청 한도(429)에 걸렸을 때 재시도 횟수
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
VIDEOS_FILE = os.path.join(os.path.dirname(__file__), "videos.txt")
OUTPUT_FILE = os.path.join(
    os.path.dirname(__file__), "..", "docs", "data", "index.json"
)

BATCH_EMBED_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{EMBED_MODEL}:batchEmbedContents"
)


class QuotaExceeded(Exception):
    """여러 번 재시도해도 요청 한도가 풀리지 않을 때 (보통 하루 한도 초과)."""


def extract_video_id(url: str) -> str | None:
    """유튜브 URL 여러 형태에서 영상 ID를 뽑아낸다."""
    patterns = [
        r"(?:v=|/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be/([0-9A-Za-z_-]{11})",
        r"shorts/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_video_title(video_id: str) -> str:
    """oEmbed API로 API 키 없이 영상 제목을 가져온다."""
    try:
        oembed_url = (
            f"https://www.youtube.com/oembed?url="
            f"https://www.youtube.com/watch?v={video_id}&format=json"
        )
        with urllib.request.urlopen(oembed_url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("title", video_id)
    except Exception:
        return video_id


def fetch_transcript(video_id: str):
    """영상 자막을 가져온다. 한국어 우선, 없으면 영어, 그것도 없으면 자동생성."""
    api = YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
        for lang in ["ko", "en"]:
            try:
                fetched = transcript_list.find_transcript([lang]).fetch()
                return fetched.to_raw_data()
            except NoTranscriptFound:
                continue
        # 위 언어가 없으면 아무거나 첫 번째 것 사용
        for t in transcript_list:
            return t.fetch().to_raw_data()
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as e:
        print(f"  [건너뜀] 자막 없음: {e}")
        return None
    return None


def chunk_transcript(segments, video_id, title, url):
    """자막 조각들을 CHUNK_CHAR_LIMIT 글자 단위로 묶는다."""
    chunks = []
    buffer_text = []
    buffer_start = None
    buffer_len = 0

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if buffer_start is None:
            buffer_start = seg["start"]

        buffer_text.append(text)
        buffer_len += len(text)

        if buffer_len >= CHUNK_CHAR_LIMIT:
            chunks.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "url": url,
                    "start": buffer_start,
                    "text": " ".join(buffer_text),
                }
            )
            buffer_text = []
            buffer_start = None
            buffer_len = 0

    if buffer_text:
        chunks.append(
            {
                "video_id": video_id,
                "title": title,
                "url": url,
                "start": buffer_start,
                "text": " ".join(buffer_text),
            }
        )

    return chunks


def embed_batch(texts):
    """여러 텍스트를 한 번의 API 호출로 임베딩한다 (batchEmbedContents).
    429(요청 한도)에 걸리면 잠시 기다렸다가 재시도하고,
    MAX_RETRIES를 넘기면 QuotaExceeded를 발생시킨다."""
    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "requests": [
            {
                "model": f"models/{EMBED_MODEL}",
                "content": {"parts": [{"text": t}]},
            }
            for t in texts
        ]
    }

    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.post(BATCH_EMBED_URL, json=body, headers=headers, timeout=60)
        if resp.status_code == 429:
            wait = 15 * attempt
            print(f"    요청 한도에 걸림. {wait}초 대기 후 재시도 ({attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        return [e["values"] for e in data["embeddings"]]

    raise QuotaExceeded(
        "여러 번 재시도했지만 요청 한도를 초과했습니다. "
        "무료 티어의 하루 한도일 가능성이 높습니다. 잠시 후 또는 내일 다시 실행해주세요."
    )


def main():
    if not GEMINI_API_KEY:
        print("오류: GEMINI_API_KEY 환경변수를 설정하세요.")
        sys.exit(1)

    if not os.path.exists(VIDEOS_FILE):
        print(f"오류: {VIDEOS_FILE} 파일이 없습니다.")
        sys.exit(1)

    with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
        urls = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    # 기존 인덱스가 있으면 불러와서 이미 완전히 처리된 영상은 건너뛴다
    existing_chunks = []
    processed_video_ids = set()
    if os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0:
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing_chunks = json.load(f)
                processed_video_ids = {c["video_id"] for c in existing_chunks}
        except json.JSONDecodeError:
            print(f"경고: {OUTPUT_FILE} 파일이 손상되어 있어 비어있는 것으로 간주하고 새로 시작합니다.")
            existing_chunks = []
            processed_video_ids = set()

    # 1단계: 새 영상들의 자막을 모두 가져와서 청크로 쪼갠다 (아직 임베딩 안 함)
    video_chunks = defaultdict(list)  # video_id -> [chunk, ...]
    ip_blocked = False
    for url in urls:
        video_id = extract_video_id(url)
        if not video_id:
            print(f"[건너뜀] URL에서 영상 ID를 찾을 수 없음: {url}")
            continue
        if video_id in processed_video_ids:
            print(f"[스킵] 이미 인덱싱됨: {video_id}")
            continue

        print(f"자막 확인 중: {url}")
        title = get_video_title(video_id)
        try:
            segments = fetch_transcript(video_id)
        except (IpBlocked, RequestBlocked):
            print(
                "\n유튜브가 이 IP에서의 요청을 일시적으로 막았습니다 (IpBlocked). "
                "지금까지 확보한 영상만 저장하고 여기서 중단합니다."
            )
            print("잠시 후(보통 몇십 분~몇 시간 뒤) 또는 내일 다시 실행하면, 안 끝난 영상부터 이어서 처리됩니다.")
            ip_blocked = True
            break
        if not segments:
            continue

        chunks = chunk_transcript(segments, video_id, title, url)
        print(f"  '{title}' -> {len(chunks)}개 청크")
        video_chunks[video_id] = chunks

        # 영상 사이에 살짝 간격을 둬서 차단 가능성을 줄인다
        time.sleep(random.uniform(*TRANSCRIPT_DELAY_RANGE))

    all_new_chunks = [c for chunks in video_chunks.values() for c in chunks]

    if not all_new_chunks:
        if ip_blocked:
            print("\n이번 실행에서는 새로 처리된 영상이 없습니다. 위 안내대로 나중에 다시 시도해주세요.")
        else:
            print("\n새로 추가할 영상이 없습니다.")
        return

    print(f"\n총 {len(all_new_chunks)}개 청크를 배치({BATCH_SIZE}개씩)로 임베딩합니다...")

    # 2단계: 배치로 임베딩 (하나의 요청으로 여러 청크 처리 -> 요청 횟수 크게 절감)
    quota_hit = False
    for i in range(0, len(all_new_chunks), BATCH_SIZE):
        batch = all_new_chunks[i : i + BATCH_SIZE]
        try:
            embeddings = embed_batch([c["text"] for c in batch])
        except QuotaExceeded as e:
            print(f"\n중단: {e}")
            quota_hit = True
            break

        for chunk, embedding in zip(batch, embeddings):
            chunk["embedding"] = embedding

        done = min(i + BATCH_SIZE, len(all_new_chunks))
        print(f"  진행: {done}/{len(all_new_chunks)}")
        time.sleep(1)  # 요청 속도 여유

    # 3단계: 영상 단위로 "전부 임베딩된 영상"만 저장한다.
    # (일부만 처리된 영상을 저장하면 다음 실행 때 "이미 처리됨"으로 착각해
    #  나머지 청크를 영원히 재시도하지 않게 되므로, 통째로 재시도하도록 남겨둔다.)
    completed_chunks = []
    incomplete_videos = []
    for video_id, chunks in video_chunks.items():
        if chunks and all("embedding" in c for c in chunks):
            completed_chunks.extend(chunks)
        elif chunks:
            incomplete_videos.append(chunks[0]["title"])

    all_chunks = existing_chunks + completed_chunks

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False)

    print(f"\n완료. 이번에 {len(completed_chunks)}개 청크 추가, 총 {len(all_chunks)}개가 저장되었습니다.")

    if incomplete_videos:
        print(f"\n다음 영상은 이번에 끝까지 처리되지 못해 다시 시도가 필요합니다 (다음 실행 시 자동으로 재처리됨):")
        for title in incomplete_videos:
            print(f"  - {title}")

    if quota_hit:
        print("\n요청 한도(아마 하루 한도) 때문에 중단되었습니다. 나중에 python ingest.py를 다시 실행하면 이어서 처리됩니다.")


if __name__ == "__main__":
    main()
