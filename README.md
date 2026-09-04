# 유튜브 자막 검색 사이트

유튜브 영상들의 자막을 기반으로 질문에 답하는 사이트입니다.
자막에 없는 내용은 "다루지 않았습니다"라고 알려줍니다.

## 1. 처음 설정 (관리자)

```bash
cd ingest
pip install -r requirements.txt --break-system-packages
```

## 2. 영상 추가

`ingest/videos.txt` 파일을 열어서 유튜브 링크를 한 줄에 하나씩 추가합니다.

```
https://www.youtube.com/watch?v=xxxxxxxxxxx
https://youtu.be/yyyyyyyyyyy
```

## 3. 인덱스 만들기

본인의 Gemini API 키로 실행합니다 (Google AI Studio에서 무료 발급).

```bash
export GEMINI_API_KEY="본인의_API_키"
cd ingest
python ingest.py
```

실행하면 `docs/data/index.json`이 생성/갱신됩니다.
이미 인덱싱된 영상은 자동으로 건너뛰므로, 나중에 videos.txt에 링크를 추가하고
다시 실행하면 새 영상만 처리됩니다.

## 4. GitHub에 올리기

```bash
git init
git add .
git commit -m "영상 검색 사이트"
git branch -M main
git remote add origin https://github.com/본인아이디/저장소이름.git
git push -u origin main
```

## 5. GitHub Pages 켜기

1. GitHub 저장소 페이지에서 **Settings → Pages**로 이동
2. **Source**를 `Deploy from a branch`로 설정
3. Branch: `main`, 폴더: `/docs` 선택 후 저장
4. 몇 분 후 `https://본인아이디.github.io/저장소이름/` 주소로 접속 가능

## 6. 사용할 사람에게 안내할 내용

- 위 URL로 접속
- Google AI Studio(https://aistudio.google.com/apikey)에서 본인 Gemini API 키 무료 발급
- 사이트에 키를 한 번 입력하면 그 브라우저에 저장되어 계속 사용 가능
- 질문을 입력하면 영상 자막에 근거해 답변하고, 출처 영상과 타임스탬프를 함께 보여줌

## 나중에 영상을 추가하려면

`videos.txt`에 링크 추가 → `python ingest.py` 재실행 → git add/commit/push
이게 전부입니다. 사이트를 쓰는 사람은 아무것도 할 필요 없습니다.
