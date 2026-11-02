# API 배치 분류

Claude Message Batches로 태그 목록을 분류하는 선택 기능입니다. 앱 실행과 일반 프롬프트 정리에는 API 키가 필요하지 않습니다.

1. `python -m pip install -r requirements-api.txt`로 SDK를 설치합니다.
2. `local/anthropic.env`에 `ANTHROPIC_API_KEY=<발급받은 키>`를 한 줄로 적습니다.
3. 분류할 배치 CSV를 만든 뒤 다음 순서로 계획과 결과를 확인합니다.

```sh
python tagorg.py batch export --top 100 --size 50 --name demo
python tagorg.py api plan "work/batches/demo-*.csv" --run demo
python tagorg.py api submit demo --max-usd 1
python tagorg.py api status demo
python tagorg.py api collect demo
```

`plan`은 로컬에서 요청과 예상 비용을 준비합니다. `submit`은 태그 이름·분류 지침·배치에 포함된 근거를 Anthropic에 전송하며 API 요금이 발생합니다. `--max-usd`는 제출 전 예상 비용을 검사하는 값입니다. `collect`는 결과를 `work/results/`에 기록합니다. [분류 지침](classification-guide.md)
