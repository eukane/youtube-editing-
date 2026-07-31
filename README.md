# gameedit — 게임 실황 자동 편집기

게임 유튜버용 편집 프로그램입니다.
**풀영상을 통째로 넣으면 전체를 다 훑어보고**

1. 🔥 **하이라이트만 골라 컷 편집**하고
2. 😂 **상황에 맞는 밈**을 얹고
3. 💬 **주인공 대사에 자막**을 달아

유튜브에 바로 올릴 수 있는 mp4 를 만들어 줍니다.

렌더링 전에 **검수용 HTML 리포트**를 만들어 주고, 편집 계획(`plan.json`)을
직접 고쳐서 다시 렌더링할 수 있어서 "자동으로 뽑은 컷을 사람이 손보는" 방식으로 쓸 수 있습니다.

```
풀영상 3시간  ─┬─▶ 오디오 흥분도 분석
               ├─▶ 장면 전환 검출     ─▶ 하이라이트 점수판 ─▶ 컷 편집
               └─▶ 음성 인식(대사)    ─▶ 자막 + 밈 트리거  ─▶ 합성 ─▶ 완성본 10분
```

---

## 설치

```bash
git clone <이 저장소>
cd youtube-editing-
pip install -e .
```

필수 준비물

| | 설명 |
| --- | --- |
| **Python 3.10+** | |
| **ffmpeg** | 영상 처리 전부를 담당합니다. `apt install ffmpeg` / `brew install ffmpeg` / [공식 빌드](https://ffmpeg.org/download.html) |
| **한글 폰트** | 자막용. `Noto Sans KR`(무료) 권장. 없으면 자막이 □□□ 로 나옵니다 |
| faster-whisper (선택) | 음성 인식. `pip install -e ".[whisper]"` — 없으면 `--subs` 로 자막 파일을 직접 넣으면 됩니다 |

설치 상태 확인:

```bash
gameedit doctor
```

## 30초 만에 써보기

```bash
gameedit auto 풀영상.mp4 -t 10m
```

끝입니다. 분석 → 하이라이트 선정 → 밈 배치 → 자막 → 렌더링까지 한 번에 돌아가고
`out/final.mp4` 가 나옵니다. (`-t` 는 원하는 최종 길이)

## 제대로 쓰는 순서

편집자가 중간에 손을 댈 수 있게 3단계로 나눠서 돌리는 걸 권합니다.

```bash
# 1. 영상 전체 분석 (여기가 제일 오래 걸립니다. 결과는 재사용됩니다)
gameedit analyze 풀영상.mp4

# 2. 편집 계획 세우기 — 몇 초면 끝납니다. 옵션 바꿔가며 여러 번 돌려도 됩니다
gameedit plan -t 12m

# 3. work/풀영상/plan.html 을 브라우저로 열어 확인
#    마음에 안 들면 plan.json 을 직접 수정하거나 2번을 다시 실행

# 4. 렌더링
gameedit render
```

### 만들어지는 파일

```
work/풀영상/
  analysis.json    오디오 흥분도 · 장면 전환 · 전사 결과 (재사용)
  plan.json        편집 계획 ← 손으로 고치는 파일
  plan.html        검수용 리포트 (타임라인 · 클립 목록 · 밈 · 자막)
  subtitles.ass    실제로 화면에 구워지는 자막 (스타일 포함)
  subtitles.srt    유튜브 자막 업로드용
out/final.mp4      완성본
```

### plan.json 직접 고치기

렌더링은 **오로지 `plan.json` 만 보고** 돌아갑니다. 그래서 이런 게 다 됩니다.

- 필요 없는 클립은 `clips` 배열에서 삭제
- 클립 순서 바꾸기 (배열 순서 = 편집본 순서, 시간은 자동 재계산)
- `source_start` / `source_end` 를 0.5초 단위로 조정
- 밈 문구·시각 수정, `memes` 에 직접 추가
- 자막 오타 수정 (`subtitles[].lines`)
- `effects: ["punch"]` 를 넣고 빼서 줌 인 여부 조절

고친 다음 `gameedit render` 만 다시 돌리면 됩니다.
`gameedit preview` 를 실행하면 검수 HTML 도 다시 만들어집니다.

## 자막(음성 인식)

세 가지 방법 중 하나를 씁니다.

```bash
# 1) faster-whisper 로 자동 전사 (설치돼 있으면 자동 선택)
pip install faster-whisper
gameedit auto 풀영상.mp4 --set transcribe.model=medium

# 2) 이미 만들어 둔 자막 파일 사용 (유튜브 자동자막 내려받기 등)
gameedit auto 풀영상.mp4 --subs 대사.srt

# 3) 자막 없이 컷 편집만
gameedit auto 풀영상.mp4 --no-subtitles
```

자막은 대사 기준으로 잘리고(한 줄 18자, 최대 2줄), **소리를 지르는 구간의 대사는
자동으로 노란색 강조 스타일**로 나갑니다. 컷 지점도 단어 중간을 자르지 않도록
말 경계에 맞춰 스냅됩니다.

## 밈

기본 팩에 밈 **28종의 트리거가 이미 정의**돼 있습니다. 대사에 "죽었" 이 나오면
`☠️ 또 죽었습니다`, "빡쳐" 가 나오면 `😤 킹 받 네`, 소리를 지르면 `🔥 여기가 하이라이트`.

각 밈에는 **그림·효과음 파일 자리가 미리 잡혀 있어서**, 파일이 없으면 자막 밈으로
나가다가 파일을 넣는 순간 그림·소리로 자동 승격됩니다. 트리거를 다시 짤 필요가 없습니다.

```bash
gameedit packs            # 지금 어떤 밈이 어떤 말에 반응하는지
gameedit packs --missing  # 어떤 파일을 어디에 넣으면 되는지
```

### 내 밈 소스 넣는 3가지 방법

```bash
# 1) 기본 팩의 빈자리 채우기 — 파일명만 맞춰 복사
cp 무야호.png assets/memepacks/default/images/무야호.png
cp 웃음.mp3  assets/memepacks/default/sfx/웃음.mp3

# 2) 모아둔 폴더 통째로 — 파일 이름이 곧 트리거
#    gameedit.yaml 에  memes: { asset_dirs: ["D:/내밈모음"] }
#      무야호.png                → "무야호" 라고 말하면 뜸
#      무야호.mp3                → 같은 이름이면 위 그림의 효과음으로 자동 결합
#      죽었,사망,뒤졌.png         → 쉼표로 트리거 여러 개
#      개킹받네@right@2.5.png    → @위치 @노출시간
#      hype@_.mp3                → 대사 대신 상황(hype/silence/timeskip)으로 발동

# 3) 하나씩 정확하게 등록
gameedit add-meme ~/짤/관짝.gif -t 죽었 -t 사망 --placement center --duration 2.5
gameedit add-meme ~/효과음/두둥.mp3 -e hype
```

자세한 형식은 [`assets/memepacks/README.md`](assets/memepacks/README.md) 참고.

밈이 도배되지 않도록 세 가지 안전장치가 걸려 있습니다.
**분당 개수 제한**, **밈별 쿨다운**, **최소 간격**. 자리가 겹치면 대사에서 나온 밈과
직접 넣은 밈이 자동 리액션 밈보다 우선합니다.

### 전환 카드

클립 사이에 원본 시간이 크게 벌어지면(기본 90초) 화면 가운데에
`3분 후` 같은 **시간 경과 카드**가 자동으로 들어갑니다. 편집본만 보는 시청자가
갑자기 상황이 바뀐 걸 헷갈리지 않게 해주는 연출입니다.

```yaml
memes:
  timeskip_min: 90     # 0 이면 끄기
```

## 하이라이트는 어떻게 고르나

1초 격자마다 점수를 매기고, 점수가 높은 봉우리부터 클립으로 잘라냅니다.

```
점수 = 1.0  × 오디오 흥분도   (평소 목소리 대비 얼마나 시끄러운가)
     + 1.3  × 대사 키워드     ("대박", "죽었", "이겼" …)
     + 0.6  × 컷 밀도         (장면 전환이 몰려 있는 구간 = 교전/이동)
     + 0.35 × 말 밀도         (말이 계속 이어지는 구간)
     + 웃음 가산점            (ㅋㅋ, ㅎㅎ …)
```

그 다음

- 앞뒤로 여유(padding)를 주고 가까운 구간끼리 합치고
- 클립 **안에 남은 2.5초 이상의 정적은 잘라내고**
- 말이 끊기지 않게 컷 지점을 단어 경계로 스냅하고
- 목표 길이(`-t`)에 맞을 때까지 점수 낮은 클립부터 버립니다

가장 시끄러운 순간이 들어간 클립에는 자동으로 **줌 펀치(1.12배)** 가 들어갑니다.

특정 구간을 강제로 넣거나 빼려면:

```yaml
highlight:
  must_include_ranges: [[1830, 1900]]   # 30:30~31:40 은 무조건 포함
  exclude_ranges: [[0, 120]]            # 처음 2분(인사말)은 제외
  boost_ranges: [[600, 900, 1.5]]       # 10~15분 구간 가점
```

## 설정

```bash
gameedit init        # gameedit.yaml 생성 (모든 기본값이 들어 있습니다)
```

자주 쓰는 값만 추리면:

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `highlight.target_duration` | 480 | 목표 길이(초). `-t 8m` 과 같음 |
| `highlight.min_clip` / `max_clip` | 6 / 45 | 클립 한 개의 최소·최대 길이 |
| `highlight.keywords` | 40여 개 | 하이라이트 판정에 쓰는 대사 키워드 |
| `subtitles.font` / `font_size` | Noto Sans CJK KR / 62 | 자막 폰트 (1080p 기준 크기) |
| `subtitles.max_chars_per_line` | 18 | 한 줄 글자 수 |
| `subtitles.emphasis_threshold` | 0.72 | 이 이상 흥분한 구간의 대사는 강조 |
| `memes.max_per_minute` | 4 | 분당 밈 개수 상한 |
| `memes.packs` | `[default]` | 사용할 밈 팩 |
| `memes.asset_dirs` | `[]` | 파일명이 곧 트리거가 되는 밈 폴더 |
| `memes.timeskip_min` | 90 | 클립 사이가 이만큼 벌어지면 "N분 후" 카드 |
| `render.punch_zoom` | true | 피크 구간 줌 인 |
| `render.crf` / `preset` | 20 / medium | 화질·인코딩 속도 |
| `project.resolution` | (원본 유지) | 예: `1920x1080`, 쇼츠면 `1080x1920` |

명령줄에서 임시로 바꾸려면 `--set` 을 씁니다.

```bash
gameedit plan --set memes.max_per_minute=6 --set highlight.weights.audio=1.5
```

## 명령어

| 명령 | 하는 일 |
| --- | --- |
| `gameedit auto <영상>` | 분석 → 계획 → 렌더링 한 번에 |
| `gameedit analyze <영상>` | 영상 전체 분석 (`analysis.json`) |
| `gameedit plan` | 편집 계획 + 검수 리포트 생성 |
| `gameedit preview` | `plan.json` 으로 검수 HTML 다시 생성 |
| `gameedit render` | 계획대로 렌더링. `--dry-run` 이면 ffmpeg 명령만 출력 |
| `gameedit packs` | 사용 중인 밈 목록 (`--missing` 이면 넣을 파일 경로) |
| `gameedit add-meme <파일>` | 그림·움짤·효과음을 밈으로 등록 |
| `gameedit doctor` | ffmpeg·음성인식·폰트 설치 점검 |
| `gameedit init` | 설정 파일 생성 |

## 자주 겪는 문제

**자막이 □□□ 로 나옵니다**
한글 폰트가 없습니다. Noto Sans KR 을 설치하고 `subtitles.font` 를 설치된 이름으로
맞춰 주세요. `gameedit doctor` 가 설치된 한글 폰트를 알려줍니다.

**분석이 너무 오래 걸립니다**
장면 검출과 음성 인식이 대부분입니다. 음성 인식은 GPU가 있으면 훨씬 빠르고
(`transcribe.device: cuda`), 급하면 `--no-transcribe` 로 컷 편집만 먼저 볼 수 있습니다.
`analysis.json` 은 한 번만 만들면 계속 재사용됩니다 (`auto --reuse-analysis`).

**하이라이트가 엉뚱합니다**
게임 장르마다 신호가 다릅니다. 조용한 게임이면 `highlight.weights.audio` 를 낮추고
`keyword` 를 올리세요. 채널에서 자주 쓰는 말은 `highlight.keywords` 에 추가하면
바로 반영됩니다.

**렌더링 중간에 실패했습니다**
`gameedit render --dry-run` 으로 실제 ffmpeg 명령을 뽑아 직접 실행해 보면 원인이 보입니다.
`render.keep_intermediate: true` 로 두면 컷 편집 결과(`cut.mp4`)가 남아
`gameedit render --skip-cut` 으로 합성 단계만 다시 돌릴 수 있습니다.

## 개발

```bash
pip install -e ".[dev]"
pytest -q
```

테스트는 ffmpeg 없이도 전부 돌아갑니다 (분석·선정·밈·자막·명령 생성 로직 검증).

```
gameedit/
  analyze.py     원본 훑기 (오디오·장면·전사)
  audio.py       RMS → 흥분도 곡선 (표준 라이브러리만 사용)
  scenes.py      ffmpeg scene 점수 파싱
  transcribe.py  faster-whisper / whisper / 외부 자막
  highlights.py  점수판 → 클립 선정
  memes.py       밈 팩 로딩 → 밈 배치
  subtitles.py   자막 큐 생성 → .ass 작성
  plan.py        EditPlan 조립
  render.py      ffmpeg 필터 그래프 (컷 → 합성 2단계)
  timeline.py    검수용 HTML
  cli.py         명령줄
```

화면에 올라가는 글자(자막·텍스트 밈·클립 라벨)는 전부 하나의 `.ass` 파일로 만들어
libass 로 굽습니다. ffmpeg 빌드에 `drawtext`(libfreetype)가 없어도 동작하고,
한글 줄바꿈·외곽선·팝 애니메이션이 안정적입니다.

## 알아두면 좋은 것

- 밈 소스의 사용 범위는 각 원작자·플랫폼 정책을 따릅니다. 저장소에 동봉된 것은
  텍스트 문구뿐이고, 그림·효과음 파일은 직접 넣는 구조입니다.
- 음성 인식은 완벽하지 않습니다. 게임 용어·고유명사는 `plan.json` 에서 고치는 게 빠릅니다.
- 최종 판단은 사람이 합니다. 이 도구는 긴 영상에서 "볼 만한 부분"을 찾아
  1차 편집본까지 만들어 주는 것까지가 역할입니다.
