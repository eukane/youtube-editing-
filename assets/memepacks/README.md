# 밈 팩 (meme pack)

밈 하나하나를 YAML 로 정의해 두면, 프로그램이 대사·오디오·편집 구조를 보고
알아서 터뜨릴 자리를 잡습니다.

## 폴더 구조

```
assets/memepacks/
  default/
    pack.yaml        # 밈 정의 (필수)
    images/          # kind: image 용 png/gif
    videos/          # kind: video 용 mp4/webm
    sfx/             # 효과음 mp3/wav
  my-pack/
    pack.yaml
    ...
```

내 팩을 만들려면 폴더를 하나 더 만들고 `gameedit.yaml` 에 등록합니다.

```yaml
memes:
  packs: [default, my-pack]        # assets/memepacks 아래 이름
  pack_dirs: ["/경로/외부팩"]       # 완전히 다른 위치도 가능
```

## 필드

| 필드 | 설명 |
| --- | --- |
| `id` | 밈 식별자. 쿨다운이 이 단위로 걸립니다. |
| `kind` | `text` / `image` / `video` / `audio` |
| `text` | `kind: text` 일 때 화면에 뜨는 문구 (이모지 사용 가능) |
| `asset` | 이미지·영상 파일 경로 (팩 폴더 기준 상대경로) |
| `sfx` | 같이 재생할 효과음 파일 |
| `triggers` | 대사에 이 문자열이 들어가면 발동 |
| `events` | `hype`(오디오 피크) · `silence`(정적 구간) |
| `placement` | `top` / `center` / `bottom` / `left` / `right` |
| `style` | 텍스트 밈 스타일: `MemeTop` / `MemeCenter` / `Label` |
| `duration` | 노출 시간(초) |
| `scale` | 이미지·영상 밈의 화면 너비 대비 크기 (0~1) |
| `weight` | 여러 밈이 동시에 걸릴 때 우선순위 |
| `cooldown` | 같은 밈 재등장 최소 간격(초) |
| `min_excitement` | 이 값 이상 시끄러운 순간에만 발동 (0~1) |

## 주의

- 저작권이 있는 밈 이미지·효과음은 직접 확보해서 넣어 주세요.
  기본 팩은 텍스트만 사용합니다.
- `asset` 파일이 없으면 그 밈은 자동으로 텍스트로 대체되거나 무시됩니다
  (렌더가 실패하지 않습니다).
