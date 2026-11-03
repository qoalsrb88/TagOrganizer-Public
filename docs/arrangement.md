# 배열 파일 형식

배열은 정리된 프롬프트를 어느 줄에 어떤 순서로 놓을지 정의하는 UTF-8 JSON 파일입니다. GUI의 미리보기에서 기본 레이아웃으로 시작해 청크를 옮기고 내보낼 수 있습니다.

| 필드 | 용도 |
|---|---|
| `format`, `version` | `tagorg-arrangement`, `1` |
| `label`, `description` | 표시할 이름과 선택적 설명 |
| `rows` | 출력 줄별로 나열한 청크 ID 목록 |
| `chunks` | ID별 정의. `members`에 카테고리 선택자를 적고, 사용자 청크에는 `custom`, `tags`, `patterns`를 사용합니다. |
| `sub_order`, `guess` | 서브 카테고리 정렬과 추가 분류 추정 규칙 |

저장한 배열을 명령줄에서 적용하는 예입니다.

```sh
python tagorg.py organize --arrangement my-arrangement.json -t "blue sky, smile, 1girl"
```

코드에서 기본 배열을 만들 때는 `arrangement_from_layout(pack)`을 사용합니다. `organize()`의 `arrangement` 옵션으로 넘기면 결과의 `report["arranged"]`에서 줄·청크별 토큰을 읽을 수 있습니다.

크롬 확장의 배열 편집기도 같은 JSON을 가져오고 내보냅니다. [크롬 확장](extension.md)
