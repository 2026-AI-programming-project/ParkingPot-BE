# 주차장 API

FastAPI로 만든 주차장 검색·주변 조회·상세·혼잡도·즐겨찾기 API입니다.

## 시작하기

Python 3.11 이상이 필요합니다.

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`의 `PARKING_CATALOG_SERVICE_KEY`에는 [전국주차장정보표준데이터](https://www.data.go.kr/data/15012896/standard.do) 키를, `PARKING_STATUS_SERVICE_KEY`에는 [한국교통안전공단 주차정보 제공 API](https://www.data.go.kr/data/15099883/openapi.do) 키를 넣습니다. 두 값 모두 발급받은 **디코딩 인증키**를 사용합니다. 두 번째 키는 시설정보와 실시간정보 조회에 함께 쓰입니다. `.env`는 Git에 포함되지 않습니다. 각 API의 활용신청이 필요합니다.

```powershell
.venv\Scripts\python -m app.sync --kind all --sido-code 11 --sigungu-code 11110
.venv\Scripts\python -m uvicorn app.main:app --reload
```

위 예시의 `11`은 서울 시도코드, `11110`은 종로구 시군구코드입니다. 다른 지역이면 두 코드를 바꾸세요. 기본 주차장 정보는 전국에서 가져오고, 시설·실시간정보는 지정한 지역만 가져옵니다. 실제 API 응답 기준으로 전국 시설정보는 1,706,731건, 서울 전체는 238,045건, 종로구는 5,429건입니다. 시군구 단위로 시작하면 훨씬 빠릅니다.

API 문서는 `http://127.0.0.1:8000/docs`에서 볼 수 있습니다. 처음 실행할 때는 동기화가 끝날 때까지 주차장 목록이 비어 있습니다. 공공 API 호출을 시험할 때는 `.venv\Scripts\python -m app.sync --kind all --sido-code 11 --sigungu-code 11110 --max-pages 1`로 소스별 한 페이지만 가져올 수 있습니다. 이후 같은 명령을 다시 실행하면 저장된 다음 페이지부터 이어받습니다. 처음부터 갱신하려면 `--restart`를 사용하세요. 기본 페이지 크기는 1000건이며 `--page-size`로 조절할 수 있습니다.

전국 시설정보는 약 170만 건이며 실제 조회에서 깊은 페이지가 반복 시간 초과됐습니다. 그래서 시설정보를 포함한 명령에는 지역코드가 필요합니다. 기본정보를 이미 가져왔다면 `.venv\Scripts\python -m app.sync --kind facility --sido-code 11 --sigungu-code 11110` 다음에 `.venv\Scripts\python -m app.sync --kind realtime --sido-code 11 --sigungu-code 11110`을 실행하세요. 다른 지역은 코드를 바꾸면 됩니다. 전국 시설정보를 꼭 시도하려면 `--allow-nationwide-facility`를 명시해야 하며 시간 초과 가능성이 있습니다. 기존 전국 조회의 이어받기 위치는 지역별 조회와 별도로 보존됩니다. 운영 시에는 기본정보를 정기적으로, 실시간 정보는 별도 주기로 갱신해야 합니다. 실시간 응답은 가져온 지 10분이 지나면 `unknown`으로 표시됩니다.

## API

| 기능                | 경로                                           | 주요 입력·출력                                                                                |
| ------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------- |
| 검색·주변 조회·필터 | `GET /parking-lots`                            | `name`, `address`, `lat`, `lon`, `radius_m`, `fee`, `accessible`, `sort`, `page`, `page_size` |
| 상세                | `GET /parking-lots/{id}`                       | 요금, 1시간 예상 요금, 운영시간, 키워드, 별점, 혼잡도                                         |
| 혼잡도              | `GET /parking-lots/{id}/congestion`            | 잔여면수 비율, 색상, 조회 시각                                                                |
| 익명 세션 발급      | `POST /sessions/anonymous`                     | 즐겨찾기·별점용 Bearer 토큰                                                                   |
| 즐겨찾기            | `GET /favorites`, `PUT/DELETE /favorites/{id}` | 세션별 주차장 목록·등록·해제                                                                  |
| 별점                | `PUT /parking-lots/{id}/rating`                | `{"score": 1~5}`; 같은 세션은 기존 별점을 변경                                                |

예: `GET /parking-lots?name=시청&lat=37.5665&lon=126.9780&radius_m=3000&fee=free&accessible=true&sort=distance`

`fee`는 `free`, `paid`, `mixed` 중 하나입니다. `free`와 `paid` 필터에는 각각 `mixed` 주차장도 포함됩니다. `sort`는 `name`, `price`, `rating`, `distance`를 지원합니다. `distance`에는 `lat`와 `lon`이 필요합니다. 좌표가 있으면 기본 정렬은 거리순입니다. 목록 응답의 `nearest_id`, 각 항목의 `distance_m`·`is_nearest`를 지도 강조에 활용할 수 있습니다. 목적지 주소는 `address`로 검색하거나, 지도 서비스에서 주소를 좌표로 변환한 뒤 `lat`·`lon`으로 주변 조회할 수 있습니다. 지도 렌더링과 사용자 위치 취득은 프론트엔드 역할입니다.

`hourly_price`는 기본시간·기본요금·추가단위시간·추가단위요금으로 계산한 **첫 60분 예상 요금**입니다. 데이터가 불충분하거나 혼합 요금이면 `null`입니다. 실제 할인·일일요금 등은 `fee_description`과 다를 수 있습니다. 별점은 이 서비스 사용자가 등록한 평가 평균이며, 평가 전에는 `average: null`입니다.

혼잡도는 `available_spaces / total_spaces` 기준입니다. `50%` 이상은 `green`/`available`, `20%` 이상 `50%` 미만은 `yellow`/`moderate`, `20%` 미만은 `red`/`busy`입니다. 실시간 정보가 없거나 10분 넘게 갱신되지 않으면 `gray`/`unknown`입니다. `fetched_at`은 원천 데이터의 측정 시각이 아니라 **우리 서버가 가져온 시각**입니다.

즐겨찾기와 별점 요청에는 `Authorization: Bearer <access_token>`이 필요합니다. 토큰은 `POST /sessions/anonymous`에서 발급하며, 잃어버리면 해당 익명 사용자의 즐겨찾기를 복구할 수 없습니다. 계정 로그인과 기기 간 동기화는 아직 지원하지 않습니다.

## 데이터 연결

- 전국주차장정보표준데이터의 `prkplceNo`를 주차장 ID로 사용합니다. 이름·주소·좌표·요금·운영시간·장애인 전용 주차구역 여부를 저장합니다.
- 한국교통안전공단 API의 시설정보와 실시간정보는 `prk_center_id`로 연결합니다. 전국 표준데이터와는 ID가 달라, 동일 ID이거나 **같은 이름 + 250m 이내의 유일한 후보**인 경우에만 연결합니다. 연결되지 않은 주차장은 혼잡도를 `unknown`으로 표시합니다. 동기화 결과의 `new_links`는 이전에 저장된 시설정보까지 포함한 새 연결 수이며, `linked_realtime_statuses`는 현재 DB에서 기본정보와 연결된 실시간정보 수입니다.
- 실시간 잔여면수는 `pkfc_Available_ParkingLots_total`, 총면수는 `pkfc_ParkingLots_total`을 사용합니다. 제공 기관 설명에 따르면 실시간 데이터가 시설정보보다 적습니다.

## 테스트

```powershell
.venv\Scripts\python -m pytest -q
```

테스트는 공공 API 키 없이 샘플 응답으로 검색·필터·거리·혼잡도·즐겨찾기·별점을 검증합니다.
