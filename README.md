# ParkingPot-BE · Auth

회원가입, 이메일 인증, 로그인, 로그아웃, 회원탈퇴, 마이페이지 조회·수정을 구현한
FastAPI + SQLite 프로젝트입니다. 서버 코드는 모두 Python입니다.

## 실행 (Windows PowerShell)

Python 3.11 이상이 필요합니다. 이 컴퓨터에서는 Python 3.13으로 검증했습니다.

```powershell
cd C:\Users\User\Desktop\Python\AIprogramming\ParkingPot
uv sync --python 3.13 --extra dev --locked
Copy-Item .env.example .env
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

이미 `.env`가 있다면 복사 단계를 생략하세요.
`uv` 대신 일반 Python 가상환경을 사용해도 됩니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

- API 실행 후 [Swagger UI](http://127.0.0.1:8000/docs)에서 요청을 직접 실행할 수 있습니다.
- [상태 확인](http://127.0.0.1:8000/health), [실행 중 OpenAPI](http://127.0.0.1:8000/openapi.json)
- SQLite 파일 `data/parkingpot.sqlite3`은 첫 실행 때 자동으로 생성됩니다.
- 로컬 기본 설정은 실제 이메일을 보내지 않습니다. `data/outbox/*.eml`에 메일을 저장합니다.
  `uv run python -m scripts.read_mail`로 가장 최근 인증번호를 확인하세요.

## 구현한 API

| 메서드 | 경로 | 기능 | 인증 |
| --- | --- | --- | --- |
| POST | `/api/auth/email-verifications` | 인증번호 요청 | 회원가입은 불필요, 정보 변경은 필요 |
| POST | `/api/auth/signup` | 회원가입 | 불필요 |
| POST | `/api/auth/login` | 로그인·세션 발급 | 불필요 |
| POST | `/api/auth/logout` | 현재 세션 로그아웃 | 필요 |
| GET | `/api/users/me` | 내 이메일·닉네임 조회 | 필요 |
| POST | `/api/users/me/password/verify` | 입력한 현재 비밀번호 확인 | 필요 |
| PATCH | `/api/users/me` | 이메일·닉네임·비밀번호 수정 | 필요 |
| DELETE | `/api/users/me` | 회원탈퇴 | 필요 |

인증은 로그인 응답의 `access_token`을 `Authorization: Bearer <access_token>` 헤더에
넣는 방식입니다. Swagger UI의 **Authorize**에도 토큰 문자열만 입력하면 됩니다.
토큰은 JWT가 아닌 서버 저장형 임의 세션 토큰이며 기본 유효 시간은 7일입니다.

## 회원가입 → 로그인 → 조회

1. 인증번호 요청: `POST /api/auth/email-verifications`

```json
{"email": "driver@example.com", "purpose": "signup"}
```

응답은 202입니다. 인증번호는 이메일로만 전달하며 API 응답에는 포함하지 않습니다.
로컬 개발에서는 위의 메일 확인 명령으로 인증번호를 읽습니다.

2. 회원가입: `POST /api/auth/signup`

```json
{
  "nickname": "주차왕",
  "email": "driver@example.com",
  "verification_code": "123456",
  "password": "ParkingPot!123",
  "password_confirm": "ParkingPot!123"
}
```

예시 `123456` 대신 발급받은 인증번호를 입력하세요. 성공 응답은 201입니다.

```json
{
  "message": "계정이 생성되었습니다.",
  "user": {"id": "사용자 UUID", "email": "driver@example.com", "nickname": "주차왕"}
}
```

3. 로그인: `POST /api/auth/login`

```json
{"email": "driver@example.com", "password": "ParkingPot!123"}
```

```json
{
  "message": "로그인이 되었습니다.",
  "access_token": "발급된 세션 토큰",
  "token_type": "bearer",
  "expires_in": 604800,
  "user": {"id": "사용자 UUID", "email": "driver@example.com", "nickname": "주차왕"}
}
```

4. `GET /api/users/me`에 인증 헤더를 넣으면 `id`, `email`, `nickname`을 반환합니다.

요구사항의 마이페이지 ‘비밀번호 확인’은 현재 비밀번호 입력 후
`POST /api/users/me/password/verify`에 `{"password": "현재 비밀번호"}`를 보내는 동작으로
구현했습니다. 비밀번호 원문·해시·비밀번호 확인 값은 조회·수정 응답에 포함하지 않습니다.
이 확인 API는 확인 메시지만 반환하며, 수정 권한을 추가로 발급하지 않습니다.

## 마이페이지 수정

`PATCH /api/users/me`에 바꿀 필드만 보냅니다. 닉네임은 2~30자, 비밀번호는 8~128자입니다.
이메일은 소문자로 통일하고 닉네임 앞뒤 공백은 제거합니다. 이메일과 닉네임은 중복될 수 없습니다.
빈 수정 요청, 명시적 `null`, 알 수 없는 필드, 비밀번호 확인 불일치는 422입니다.

닉네임만 바꾸려면:

```json
{"nickname": "새닉네임"}
```

이메일은 로그인 상태에서 **새 이메일**로 `purpose: "email_change"` 인증번호를 요청한 뒤:

```json
{"email": "new@example.com", "email_verification_code": "123456"}
```

비밀번호는 로그인 상태에서 **현재 이메일**로 `purpose: "password_change"` 인증번호를 요청한 뒤:

```json
{
  "password": "NewParkingPot!456",
  "password_confirm": "NewParkingPot!456",
  "password_verification_code": "123456"
}
```

이메일과 비밀번호를 함께 수정하려면 **새 이메일 인증번호와 현재 이메일의 비밀번호 변경
인증번호 두 개**를 각각 제출합니다. 모든 인증과 중복 검사가 끝난 뒤 전체 변경을 한 번에 저장합니다.
수정 후 인증하는 대신, 이메일 인증에 성공한 경우에만 비밀번호가 변경됩니다.

성공 응답:

```json
{
  "message": "수정이 완료 되었습니다",
  "user": {"id": "사용자 UUID", "email": "new@example.com", "nickname": "새닉네임"},
  "requires_login": true
}
```

닉네임만 변경하면 `requires_login`은 `false`입니다. 이메일 또는 비밀번호를 변경하면
모든 세션과 해당 계정의 미사용 인증번호를 폐기하며, 변경된 정보로 다시 로그인해야 합니다.

## 로그아웃·탈퇴 및 화면 이동

로그아웃과 탈퇴는 요청 본문 없이 인증 헤더만 보냅니다.

- 로그아웃 성공: `{"message": "로그아웃이 되었습니다."}`. 현재 기기의 토큰만 폐기합니다.
- 탈퇴 성공: `{"message": "회원 탈퇴가 완료되었습니다"}`. 계정과 모든 세션·계정에 연결된
  인증번호를 삭제합니다. 프론트엔드에는 탈퇴 확인 UI를 두는 것을 권장합니다.
- 로그인 성공 후 홈 이동, 로그아웃·탈퇴 후 토큰 제거 및 로그인 화면 이동은 프론트엔드가 처리합니다.
- 만료·폐기된 토큰은 401을 반환합니다. 프론트엔드는 저장한 토큰을 지우고 로그인 화면으로 안내하세요.

## 오류 응답

업무 오류는 `{"detail": "오류 메시지"}`입니다. 입력 형식 오류(422)는 `detail`에
`loc`, `msg`, `type`으로 구성된 배열을 반환합니다. 제출한 비밀번호와 인증번호를 오류 응답에
다시 넣지 않습니다.

| 상태 | 의미 |
| --- | --- |
| 400 | 인증번호 불일치·만료·미요청, 잘못된 현재 비밀번호 |
| 401 | 로그인 필요, 잘못된 로그인 정보, 세션 만료 |
| 409 | 중복 이메일 또는 닉네임 |
| 422 | 입력값 형식 오류·비밀번호 확인 불일치 |
| 429 | 요청 횟수 초과·인증번호 재발송 대기·인증 시도 초과 |
| 503 | 메일 발송 실패. 이전에 발급된 유효한 인증번호는 유지됨 |

인증번호는 6자리 숫자 문자열이며 기본 10분간 유효합니다. 60초 후 재발송할 수 있고,
재발송하면 이전 번호는 무효입니다. 번호당 오입력 5회 후에는 새로 발급받아야 합니다.
인증번호는 목적과 계정에 연결되며 다른 사용자의 번호를 사용할 수 없습니다.

메일 요청은 IP당 1시간 10회, 회원가입은 IP당 10분 20회, 로그인은 이메일당 5분 10회 및
IP당 5분 50회입니다. 현재 비밀번호 확인은 사용자·IP 조합당 5분 10회입니다.
로그인은 성공·실패 모두 횟수에 포함합니다. 시간 제한에 따른 429에는 `Retry-After` 헤더가 있습니다.

## 실제 이메일 발송

`.env`에서 `MAIL_BACKEND=smtp`로 바꾸고 `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `MAIL_FROM`을 메일 서비스에 맞게 설정하세요. 기본 방식은 STARTTLS(587)이며
SSL(465)은 `SMTP_SECURITY=ssl`을 사용합니다. 비밀번호는 `.env`에만 저장하고 커밋하지 않습니다.

운영 시 `APP_ENV=production`, 32자 이상의 무작위 `AUTH_SECRET`, TLS를 사용하는 SMTP가
필요하며 잘못 설정하면 실행을 중단합니다. 인증번호 해시는 이 키를 사용하므로 재시작·여러 워커에서
동일한 키를 써야 합니다. 개발 모드에서 키를 생략하면 재시작 시 미사용 인증번호가 무효화됩니다.

비밀번호는 Argon2로, 세션 토큰은 SHA-256으로, 인증번호는 HMAC-SHA256으로 저장합니다.
운영에서는 HTTPS와 명시적인 `CORS_ORIGINS`를 설정하고 `--reload` 없이 실행하세요.
프록시 뒤에 배포한다면 uvicorn의 신뢰할 프록시 IP를 제한해 실제 클라이언트 IP를 전달하세요.
SQLite는 로컬·소규모 실행 기준이며, SMTP 발송 중 인증번호 저장 트랜잭션을 유지합니다.
대규모 배포의 메일 큐·DB 마이그레이션 도구·만료된 인증번호의 정기 정리는 포함하지 않았습니다.

## 검증과 API 명세

```powershell
uv run pytest -q
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
uv run python -m scripts.export_openapi
```

API 정의의 원본은 FastAPI의 라우트와 Pydantic 모델입니다. `docs/openapi.json`은 위 명령으로
생성한 스냅샷으로, API를 바꾸면 다시 생성하세요. Postman에도 OpenAPI 파일을 가져올 수 있습니다.
테스트는 임시 SQLite DB, 파일 메일함, 로컬 SMTP 테스트 서버를 사용하며 실제 메일을 발송하지 않습니다.
SMTP 수신 성공·실패를 로컬에서 검증하지만 실제 메일 서비스의 자격증명·TLS 연결·수신함 도착은
사용할 SMTP 계정 설정 후 별도로 확인해야 합니다.

FastAPI 테스트 방식은 [공식 테스트 문서](https://fastapi.tiangolo.com/tutorial/testing/)를 참고했습니다.

## 백엔드 협업 구조

```text
app/
├── main.py             # 앱 시작, 공통 미들웨어, 도메인 테이블 초기화
├── api/
│   ├── router.py       # 도메인 라우터를 /api 아래 등록
│   └── dependencies.py # 공유 의존성 CurrentUser, DatabaseDep
├── core/config.py      # 환경 설정
├── db/database.py      # SQLite 연결과 트랜잭션
├── integrations/      # 공용 메일 발송
├── common/            # 공통 HTTP 오류
└── modules/auth/      # 회원가입, 로그인, 마이페이지, 탈퇴
    ├── router.py      # HTTP 경로
    ├── schemas.py     # 요청과 응답 모델
    ├── service.py     # 인증 규칙과 데이터 처리
    ├── tables.py      # 회원, 세션, 인증번호 테이블
    ├── dependencies.py # 인증 내부 의존성과 공개 사용자 정보
    └── security.py    # 비밀번호, 토큰, 인증번호 해시
tests/auth/            # 인증 API와 메일 테스트
```

팀원은 `app/modules/<담당기능>/`에 도메인 코드를 두고 `tests/<담당기능>/`에 테스트를 작성합니다.
새 라우터는 `app/api/router.py`에 등록합니다. 새 테이블이 필요하면 도메인의 `tables.py`에
정의한 SQL을 `app/main.py`의 `database.initialize(...)`에 추가합니다. 기존 테이블의 구조를
바꿀 때는 기존 데이터에 적용할 마이그레이션을 별도로 준비해야 합니다.

로그인이 필요한 라우트는 `app.api.dependencies.CurrentUser`로 `id`, `email`, `nickname`을
받습니다. 다른 도메인에서 계정 소유자를 연결할 때는 `user["id"]`를 사용합니다.
같은 모듈의 `DatabaseDep`로 앱의 DB 연결을 가져올 수 있습니다. 도메인 내부의 인증 SQL과
비밀번호 해시는 `auth`에서 관리합니다. 마이페이지 경로는 `/api/users/me`를 사용합니다.
