# drafts/ — 맥미니가 만든 공유 초안

유리가 **읽기만** 하면 되는 완성 초안입니다. 무거운 생성은 맥미니가 끝내고 여기에 올립니다.

```
drafts/
├── candidates/{YYYY-MM-DD}.md     소싱 후보 표(가격·리뷰·영상 유무·탈락 이유)
└── {글감 id}/
    ├── manifest.json              상품·가격·생성 시각·codex 성공 여부
    ├── shorts_script.md           훅 3안 + 20초 타임라인 + 촬영 컷
    ├── captions.srt               자막 초안
    ├── blog_draft.md              블로그 본문(스펙 표 포함)
    ├── hotdeal_card.md            카드뉴스 5장 문구
    └── codex_output.md            Codex가 남긴 요약(있으면)
```

- 영상(`*.mp4`)은 올리지 않습니다. 프레임·`clip_manifest.json`만 필요하면 `{id}/clips/`에 복사합니다.
- 비밀·개인정보 패턴(API 키, 토큰, 휴대전화번호)이 발견되면 `codex_sync.py`가 그 글감의 공유를 보류하고 `output/`에만 남깁니다.
- 게시·구매·결제는 하지 않습니다. 유리/사람이 최종 확인 후 게시합니다.
