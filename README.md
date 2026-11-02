# TagOrganizer

**이 도구에 포함된 태그 분류 데이터에는 성인용 어휘와 성적 표현을 설명하는 텍스트가 포함되어 있습니다.**

Danbooru와 e621 태그가 섞인 이미지 프롬프트를 분류하고 원하는 순서로 정리하는 Python 도구입니다. 명령줄이나 GUI에서 태그를 찾고, 카테고리·등급으로 거르고, NovelAI·ComfyUI·Anima 문법으로 출력할 수 있습니다.

## 시작하기

Python 3.12에서 검증했습니다. 저장소를 내려받아 폴더 안에서 다음 명령을 실행하세요. 기본 명령줄 도구는 Python 표준 라이브러리만 사용합니다.

```sh
python tagorg.py fetch
python tagorg.py build
python tagorg.py organize -t "blue sky, smile, 1girl, looking at viewer"
```

`fetch`가 두 원본 태그 목록을 내려받아 SHA-256을 확인합니다. `build`는 분류 데이터를 합쳐 `build/tagpack.json.gz`를 만듭니다. 태그 하나의 분류를 확인하려면 `python tagorg.py lookup "blue sky"`를 실행하세요.

## GUI와 사용자 설정

```sh
python -m pip install -r requirements-gui.txt
python tagorg.py gui
```

GUI의 미리보기에서 기본 레이아웃을 고르거나 배열을 직접 만들 수 있습니다. 새로 저장한 배열은 `local/arrangements/`에 보관합니다. 공개판에는 공유 배열 프리셋이 들어 있지 않습니다. 개별 태그는 GUI에서 추가하거나 `python tagorg.py extra add user "my custom tag"`로 등록할 수 있습니다.

자세한 사용법은 `python tagorg.py --help`와 각 명령의 `--help`에서 확인하세요. [배열 파일 형식](docs/arrangement.md) · [태그 분류 지침](docs/classification-guide.md) · [선택 기능인 API 배치 분류](docs/api-batch.md)

## 데이터 출처와 라이선스

프로그램 코드와 자체 작성 문서에는 MIT 라이선스를 적용합니다. [MIT 라이선스](LICENSE)

원본 태그 목록은 Danbooru와 e621에서 수집한 DraconicDragon의 아카이브를 사용합니다. 저장소에는 분류 결과와 다운로드 정보가 들어 있으며, 원본 CSV는 `fetch`가 별도로 받습니다. 원본 아카이브는 Unlicense를 표시합니다. 외부에서 유래한 태그·별칭·자료에는 해당 출처의 조건이 적용됩니다. [원본 아카이브](https://github.com/DraconicDragon/dbr-e621-lists-archive) · [아카이브 라이선스](https://github.com/DraconicDragon/dbr-e621-lists-archive/blob/main/LICENSE) · [파일별 출처와 해시](data/source/sources.json)

분류에는 자동화된 판단이 포함되어 등급이나 카테고리가 틀릴 수 있습니다. 모델별 추가 태그의 참고 문서는 `data/extra/model.csv`의 `ref` 열에서 확인할 수 있습니다.
