# 밈 팩 (meme pack)

밈을 어디에 터뜨릴지는 프로그램이 알아서 정합니다. 여러분이 할 일은
**소스 파일을 넣는 것**뿐입니다. 넣는 방법이 세 가지 있습니다.

---

## 방법 1. 기본 팩의 빈자리를 채우기 (가장 쉬움)

기본 팩(`default/pack.yaml`)에는 밈 28종의 트리거가 이미 다 정의돼 있고,
각각 **어떤 파일을 넣으면 되는지 경로까지 잡혀 있습니다.**
파일이 없으면 자막 밈으로 나가고, 파일을 넣는 순간 그림·소리로 바뀝니다.

```bash
gameedit packs --missing      # 어떤 파일을 어디에 넣으면 되는지 전부 출력
```

```
default/
  images/사망.png      ← "죽었" 이라고 말하면 뜸
  images/무야호.png    ← "신난다", "가보자"
  images/킹받네.png    ← "빡쳐", "짜증"
  sfx/웃음.mp3         ← "ㅋㅋ"
  sfx/두둥.mp3         ← "헐", "소름"
  ...
```

파일명만 맞춰서 복사해 넣으면 끝입니다. 설정 수정 필요 없습니다.

## 방법 2. 폴더에 쏟아붓기 (파일명 = 트리거)

내가 모아 둔 짤·효과음을 폴더째로 쓰고 싶을 때. 설정에 폴더만 등록하면
**파일 이름이 그대로 트리거**가 됩니다.

```yaml
memes:
  asset_dirs: ["D:/내밈모음"]
```

```
내밈모음/
  무야호.png                 → "무야호" 라고 말하면 뜸
  무야호.mp3                 → 같은 이름이면 위 그림의 효과음으로 자동 결합
  죽었,사망,뒤졌.png          → 쉼표로 트리거 여러 개
  개킹받네@right@2.5.png     → @위치 @노출시간(초)
  hype@_.mp3                 → 대사 대신 상황으로 발동 (hype/silence/timeskip)
  터짐.gif                   → 움짤도 됩니다
```

하위 폴더까지 훑고, 직접 넣은 밈은 기본 자막 밈보다 우선합니다.

## 방법 3. 명령어로 하나씩 등록

위치·길이·쿨다운을 정확히 주고 싶을 때.

```bash
gameedit add-meme ~/다운로드/무야호.png -t 무야호 -t 신난다 --placement center --duration 2.5
gameedit add-meme ~/다운로드/두둥.mp3 -e hype          # 소리 지를 때마다 효과음
gameedit add-meme ./짤/관짝.gif -t 죽었 --pack ./내팩   # 다른 팩에 등록
```

파일은 팩 폴더로 복사되고 `pack.yaml` 에 정의가 추가됩니다.

---

## 새 팩 만들기

```
assets/memepacks/
  default/
    pack.yaml        # 밈 정의 (필수)
    images/          # 그림·움짤
    sfx/             # 효과음
  내팩/
    pack.yaml
```

```yaml
memes:
  packs: [default, 내팩]          # assets/memepacks 아래 이름
  pack_dirs: ["/다른/경로/외부팩"]  # 완전히 다른 위치도 가능
```

## pack.yaml 필드

| 필드 | 설명 |
| --- | --- |
| `id` | 밈 식별자. 쿨다운이 이 단위로 걸립니다 |
| `kind` | `text` / `image` / `video` / `audio` |
| `text` | 자막 문구. 그림 파일이 없을 때 대신 나갑니다 |
| `asset` | 그림·영상 경로 (팩 폴더 기준 상대경로) |
| `sfx` | 같이 재생할 효과음 |
| `triggers` | 대사에 이 문자열이 들어가면 발동 |
| `events` | `hype`(소리 지름) · `silence`(정적) · `timeskip`(시간 점프) |
| `placement` | `top` / `center` / `bottom` / `left` / `right` / `fullscreen` |
| `style` | 텍스트 스타일: `MemeTop` / `MemeCenter` / `Card` / `Label` |
| `duration` | 노출 시간(초) |
| `scale` | 그림 크기 (화면 너비 대비 0~1) |
| `weight` | 여러 밈이 겹칠 때 우선순위 |
| `cooldown` | 같은 밈 재등장 최소 간격(초) |
| `min_excitement` | 이만큼 시끄러운 순간에만 발동 (0~1) |

### 전환 카드의 `{gap}`

`events: [timeskip]` 밈의 `text` 에 `{gap}` 을 쓰면 건너뛴 시간이 들어갑니다.

```yaml
  - id: timeskip_card
    text: "{gap}"          # → "3분 후", "1시간 후"
    events: [timeskip]
    style: Card
```

## 파일 형식

| 종류 | 확장자 |
| --- | --- |
| 그림 | `.png` `.jpg` `.jpeg` `.webp` `.bmp` (투명 배경 png 권장) |
| 영상·움짤 | `.gif` `.mp4` `.webm` `.mov` `.mkv` |
| 소리 | `.mp3` `.wav` `.m4a` `.ogg` `.flac` |

`asset` 파일이 없어도 렌더링은 실패하지 않습니다. 자막으로 대체되거나 그냥 넘어갑니다.

## 저작권

밈 소스의 사용 범위는 각 원작자·플랫폼 정책을 따릅니다. 공개적으로 사용이 허용된
소스인지 확인하고 넣어 주세요. 기본 팩에 동봉된 것은 텍스트 문구뿐입니다.
