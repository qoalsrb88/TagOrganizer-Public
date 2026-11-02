# 확장 태그

`model.csv`는 모델별 태그, `deprecated.csv`는 Danbooru의 폐기 태그, `user.csv`는 직접 추가한 태그를 보관합니다. 배포본의 `user.csv`에는 열 이름만 들어 있습니다.

| 열 | 용도 |
|---|---|
| `name`, `aliases`, `models` | 출력 이름, 별칭, 적용 모델 ID |
| `primary`, `secondary`, `rating`, `regions`, `identity` | 분류, 등급, 신체 부위, 고유 외형 표시 |
| `status`, `by`, `confidence`, `note`, `ref` | 검수 상태, 분류 기록, 신뢰도, 메모, 근거 링크 |
| `same_as`, `use_instead` | 같은 뜻인 태그, 대체 권장 태그 |

`python tagorg.py extra add user "my custom tag"`로 태그를 추가할 수 있습니다. 편집 후 `python tagorg.py check`로 검사하고 `python tagorg.py build`로 팩을 다시 만드세요. [분류 지침](../../docs/classification-guide.md)
