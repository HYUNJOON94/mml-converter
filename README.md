# MIDI to MML 변환기

마비노기에서 사용할 수 있는 MIDI 파일을 MML 코드로 변환하는 웹 서비스입니다.

## 기능

- MIDI 파일 업로드
- MML 코드로 자동 변환
- 간단하고 직관적인 웹 인터페이스

## 설치 방법

1. Python 3.7 이상이 설치되어 있어야 합니다.
2. 필요한 패키지 설치:
```bash
pip install -r requirements.txt
```

## 실행 방법

1. 터미널에서 다음 명령어를 실행합니다:
```bash
python app.py
```

2. 웹 브라우저에서 `http://localhost:5000`으로 접속합니다.

## 사용 방법

1. 웹 페이지에서 "파일 선택" 버튼을 클릭하여 MIDI 파일을 선택합니다.
2. "변환하기" 버튼을 클릭합니다.
3. 변환된 MML 코드가 화면에 표시됩니다.
4. MML 코드를 복사하여 마비노기에서 사용할 수 있습니다.

## 주의사항

- MIDI 파일만 업로드 가능합니다 (.mid 확장자)
- 파일 크기는 최대 16MB로 제한됩니다.
- 복잡한 MIDI 파일의 경우 변환 결과가 완벽하지 않을 수 있습니다. 