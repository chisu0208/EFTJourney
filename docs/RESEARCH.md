# MYEFTJOURNEY 리서치 노트

> - 정리일 2026-10-30. 멀티 에이전트 리서치 워크플로를 두 번 돌린 결과(스크린샷 파일명, 지도, 게임 로그, Raid Review, 킬 목록·OCR, 기존 도구의 6개 주제)를 한곳에 모았다. 검증 판정은 2차 실행 기준이다. 1차 실행에서는 스크린샷 검증이 세션 한도로 실패했고, 지도는 두 번 검증됐다.
> - 전제: 게임 메모리를 읽지 않는다. 입력을 주입하거나 자동화하지 않는다. 레이드가 끝난 뒤에만 보여 준다(레이드 중 실시간 지도 없음).
> - 표기: **미확인**은 근거가 부족하다는 뜻이며 확인 방법을 함께 적는다. "추론"은 직접 증거 없이 정황으로 판단한 내용이다.

## 1. 요약

- 위치 정보는 **인게임 스크린샷 파일명**에만 있다. 형식은 `yyyy-MM-dd[HH-mm]_x, y, z_qx, qy, qz, qw_tod (n).png`이고, 실제 파일명 291개로 검증했다. 좌표는 1인칭 카메라(눈) 위치, 회전은 카메라 쿼터니언, `tod`는 인게임 시각(시간 단위)이다.
- 방향은 쿼터니언으로 forward 벡터를 구해 `atan2(fx, fz)`로 계산한다(0° = +Z, 90° = +X). TarkovMonitor 공식은 위아래를 볼수록 틀어진다(피치 42.5°에서 8.4° 오차).
- 시각은 파일 생성 시각(`st_birthtime`)이 가장 정확하다. 파일명은 분 단위이고 초는 버린다. `tod`로 그 1분 안에서 ±2.6 s까지 좁힐 수 있다(Factory는 시계가 멈춰 있어 불가, PvE는 미확인).
- 이 PC의 스크린샷 폴더는 OneDrive로 옮겨진 `문서` 아래에 생긴다(2026-10-30 첫 스크린샷으로 확인, 2.9). 스크린샷 키는 기본값 `SysReq` 하나뿐인데, 이 기본값은 많은 사용자에게서 동작하지 않는다고 보고된다. 게임 안에서 다른 키로 다시 지정해야 한다.
- 지도 변환은 tarkov.dev `maps.json` + `getCRS`를 그대로 따른다. 게임 (x, z) → LatLng(z, x) → 맵별 회전 → transform. Customs 탈출구 28개 중 27개가 bounds 안에 들어오고 그림과도 맞는다. 데이터는 `json.tarkov.dev` JSON API에서 받는다(GraphQL API는 조사 중 계속 HTTP 422).
- 로그: 세션 371개, 레이드 시도 628건, 실제로 시작된 레이드 605건(2025-12-31부터 2026-10-27까지). 맵, 모드(Pve/PvpSeason), 종류(local/online), RaidId, 트랜싯 연결, 시작·종료 시각은 로그로 가를 수 있다. 하지만 **레이드 결과(생존/사망/탈출구/킬)는 어떤 로그에도 없다.**
- 킬·사망은 레이드 후 화면을 OCR해서 얻는다. 킬 목록의 TIME은 레이드 경과 시간(`HH:MM:SS`)이므로 `T0 = GameStarted`에 더해 벽시계 시각으로 바꾸고, 종료 화면의 RAID TIME으로 T0를 보정한다.
- OCR 1순위는 이 PC에 이미 있는 Windows OCR(`Windows.Media.Ocr`, ko 엔진)이다. 다만 TIME 열(가늘고 흐린 이탤릭 숫자)은 0/9로 하나도 못 읽었으므로 숫자는 템플릿 매칭으로 따로 읽는다. 예비안은 RapidOCR.
- 라이선스: `maps.json`과 CRS 코드는 MIT, 지도 SVG는 CC BY-NC-SA 4.0에 "치트 용도로 쓰면 자동 철회" 조항이 붙어 있고, 래스터 타일은 라이선스를 찾지 못했다.
- 라이브 EFT용 도구는 모두 "최신 위치 점" 하나나 메모리 속 짧은 궤적만 보여 준다. 레이드별 저장 기록 + 타임라인 + 경로 위 킬/사망 표시는 SPT 전용 Raid Review에만 있다. 약관 쪽은 BSG의 공식 판단이 없고, 키 자동 입력이 가장 위험하며, 레이드 후 로컬 분석이 가장 안전하다.

> **버전 주의**: 위 내용은 모두 1.2.5 이하 클라이언트 기준으로 검증했다(로그 1.0.1부터 1.2.5.0.48146, 파일명 0.16부터 1.2.x). 게임은 2026-10-30 03:57 KST에 **1.3.0.48484**로 업데이트됐다(Unity는 2022.3.43f2 그대로). 첫 1.3.0 스크린샷과 첫 1.3.0 레이드 로그로 파일명 형식과 로그 마커를 다시 확인해야 한다.

## 2. 스크린샷 파일명

### 2.1 형식
실제 예(Customs, 1.1.5 PvP, kartograf 형식 문서. heading 268.52°):

```
2026-09-18[01-14]_616.67, 1.05, -103.50_0.04218, -0.71487, 0.04329, 0.69664_14.65 (0).png
```

```
NAME = DATE '[' HH '-' MM ']' [ '_' X ', ' Y ', ' Z '_' QX ', ' QY ', ' QZ ', ' QW ] [ '_' TOD ] ' (' N ').png'
```

- 그룹 사이는 `_`, 그룹 안 숫자 사이는 `, `(쉼표+공백). Unity Vector3/Quaternion의 `ToString` 결과에서 괄호만 지운 모양이다.
- 확장자는 항상 `.png`다. 2560×1440 8-bit RGB, tIME/tEXt 같은 메타데이터 청크 없음, 장당 4.4–6.4 MB.
- 소수 자릿수: 위치 2자리(F2), 쿼터니언 5자리(F5), tod 2자리. 2024년(0.15.x) 빌드는 위치·쿼터니언이 1자리(F1)였다(실제 파일 4개 확인). 0.16(2025-03)부터 1.2.x(2026-10)까지는 형식 변화가 없다.
- 소수점은 OS 로캘과 상관없이 `.`이다(쉼표 로캘 PC에서도 `.`로 기록됨).

### 2.2 필드
| 필드 | 의미 | 메모 |
|---|---|---|
| `DATE`, `HH`, `MM` | PC 로컬 벽시계(Windows 시간대), 24시간제 | 초는 **버림**(반올림 아님). 이 PC는 KST(UTC+9, DST 없음) |
| `x, y, z` | 카메라의 월드 위치(눈 높이, 발이 아님) | Unity 단위(m). 왼손 좌표계, +Y가 위. 지도는 (x, z), y는 층 선택용. 연구소 레이드 3판의 첫 스크린샷이 PMC 스폰 지점보다 1.4–1.5 m 높게 찍혔다(2026-11-01) |
| `qx, qy, qz, qw` | 카메라 회전(쿼터니언), Unity (x, y, z, w) 순서 | q와 −q는 같은 회전. 반올림 탓에 \|q\|가 1에서 최대 약 7e-6 벗어나므로 정규화한다 |
| `tod` | 인게임 시각(0–24시간) | 0.01 h = 인게임 36초 ≈ 실제 5.1초. 프레임 시간이 아니다(fov·speed·frame time으로 잘못 부르는 도구가 있음) |
| `n` | 이름이 겹치지 않게 붙는 숫자로 보임, **순번이 아님** | 실제 이름에서는 늘 0이었다(n > 0은 본 적 없음). 순서나 개수로 쓰지 않는다 |

tod와 실제 시각의 관계(타르코프 시계, 7배속): `tod = (3 + 7·t_unix/3600 [+12]) mod 24`. 두 슬롯 중 하나(+12)가 맞는다.
- PvP 파일명 258개 전부가 UTC+3 가정에서 맞았다(중앙 오차 0.032 h). 다른 작성자들의 이름도 각자 시간대(+1, +2, +9, −4)에서 맞는다 → 파일명 시각은 서버 시각이 아니라 로컬 시각이다.
- Factory(PvP)는 15.47로 고정된다(인게임 15:28).
- PvE Factory 샘플 하나(13.12)는 어느 시간대에도 맞지 않는다. **PvE의 tod는 미확인**(확인 방법: 한 PvE 레이드에서 연속 샷의 tod가 실제 시간의 7배로 증가하는지 본다).

### 2.3 정규식(검증에서 다듬은 판)
`os.path.basename(path)`에 `re.IGNORECASE`로 적용한다.

```
^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\[(?P<hh>[0-9]{2})-(?P<mm>[0-9]{2})\](?:_(?P<x>-?[0-9]+\.[0-9]+), (?P<y>-?[0-9]+\.[0-9]+), (?P<z>-?[0-9]+\.[0-9]+)_(?P<qx>-?[0-9]+\.[0-9]+), (?P<qy>-?[0-9]+\.[0-9]+), (?P<qz>-?[0-9]+\.[0-9]+), (?P<qw>-?[0-9]+\.[0-9]+))?(?:_(?P<tod>[0-9]+[.,][0-9]+))? \((?P<n>[0-9]+)\)\.png\Z
```

- 실제 이름 291/291 통과. 1차안과 다른 점: `$` 대신 `\Z`(끝 개행 거부), `\d` 대신 `[0-9]`(비ASCII 숫자 거부), `]` 뒤에 `_` 하나만 허용.
- 카운터는 `[0-9]+`(TarkovMonitor의 `\(\d\)`는 여러 자리 카운터를 놓친다). 소수 자릿수는 제한하지 않는다(F1 옛 이름도 통과). 위치 블록과 tod 블록은 각각 선택.
- 숫자는 `float()`로, tod는 `,`를 `.`로 바꾼 뒤 변환한다(쉼표는 방어용이고 실제로 본 적 없음). 날짜는 `strptime`으로 한 번 더 검사.
- 맞지 않는 `*.png`는 로그로 남겨 형식 변화(1.3.x)를 바로 알아차린다. 지수 표기(`3.5E-05`)는 거부하는 게 맞다(F5 고정 소수점은 지수를 내지 않고 실례도 없음).

### 2.4 방향(heading)
1. q를 정규화한다.
2. forward = q × (0, 0, 1): `fx = 2(qx·qz + qw·qy)`, `fy = 2(qy·qz − qw·qx)`, `fz = 1 − 2(qx² + qy²)`
3. `heading = degrees(atan2(fx, fz)) mod 360`. 0 = +Z, 90 = +X, 위에서 보아 시계 방향.
4. `pitch_down = degrees(asin(clamp(−fy, −1, 1)))`

- 롤(기울이기)과 q의 부호에 영향받지 않는다. `hypot(fx, fz) < 1e-6`(바로 위나 아래를 볼 때)이면 방향이 없다.
- 점검값: (0, 0, 0, 1) → 0°, (0, 0.70711, 0, 0.70711) → 90°, Unity Euler(40, 45, 0) → 45°, devkey 예시 15.32°, kartograf 예시 268.52°. 연속 샷 45쌍에서 heading과 이동 방향의 중앙 오차는 13.7°(좌우 반전 대조군 134°)로, 부호와 축이 맞다.
- **TarkovMonitor 공식이 틀어지는 이유**: `atan2(2(qw·qy + qx·qz), 1 − 2(qy² + qz²))`는 회전행렬로 보면 `atan2(R02, R00)`다. 분자는 fx와 같지만 분모가 fz와 같은 건 피치와 롤이 0일 때뿐이라, 위아래를 볼수록 오차가 커진다.
  - 피치 42.5° 실제 샷: 130.88°(정답 122.53°). Euler(40, 45, 0): 37.45°(정답 45°). PvP 258장: 중앙 0.22°, 최대 3.85°.
  - Tarkovy와 MAYAK가 이 공식을 그대로 쓰고, TarkovHelper와 TarkovMapTrackerCC는 여기에 +180을 더한다. kartograf, QTtrash/tarkov-map, eft-where-am-i, tarkoveyes, devkey는 forward 벡터 방식이다.
- 지도에 그리기: P와 P + (fx, 0, fz)를 같은 지도 변환으로 투영해 두 점을 잇는다. 맵별 회전과 반전이 자동으로 처리된다. tarkov.dev CSS 마커와 똑같이 하려면 화면 각도 = `(heading − r) mod 360`(r = coordinateRotation).
- +Z가 나침반 북쪽이라는 보장은 없다. **미확인**(나침반 방위를 표시할 때만 필요. 맵별로 게임 나침반과 비교해 확인). F1(2024년) 이름은 쿼터니언이 1자리라 방향 정밀도가 ±10° 안팎이다.

### 2.5 시각
우선순위: 파일 생성 시각 → tod 보정 → 파일명 분 → `n`.

1. **파일 생성 시각**: `os.stat(p).st_birthtime`(UTC, 100 ns). Windows에서는 Python 3.12 이상에서 쓸 수 있고, 그 이전 버전은 Windows의 `st_ctime`이 생성 시각이다. 감시기가 파일을 처음 볼 때 `st_mtime_ns`와 함께 저장한다. 복사나 동기화(OneDrive 복원 등)를 거치면 생성 시각이 바뀐다.
   - 기대 범위: 생성 시각(KST 변환) − 파일명 분 ∈ [0 s, 60 s + 쓰기 지연]. PNG 인코딩·쓰기 지연은 **미확인**(1 s 미만 예상. 확인 방법: 키를 누른 시각과 첫 스크린샷의 생성 시각 비교).
2. **파일명 분**: 로컬 시각, 버림. 60초 창으로 다룬다. 근거: 역산한 "그 분 안의 초"가 −1.7–61.7 s에 고르게 퍼져 있다(중앙 29.1 s).
3. **tod 보정**: 파일명 분(UTC로 바꿔서) 안에서 tod 공식을 풀면 ±2.6 s로 좁혀지고 낮/밤 슬롯도 정해진다. 한 레이드 안에서는 tod 차이 ÷ 7 = 실제 경과 시간. Factory에서는 쓸 수 없고, PvE에서는 쓰기 전에 검증한다.
4. 경로 점은 로그의 레이드 구간(`GameStarted`부터 종료 마커까지) 안에 들어오는 것만 쓴다.

### 2.6 저장 폴더
- 규칙: `<문서>\Escape from Tarkov\Screenshots\`. 게임이 첫 스크린샷 때 폴더를 만든다.
- `<문서>`는 `SHGetKnownFolderPath(FOLDERID_Documents)`로 구하고, 안 되면 `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders\Personal` 값(환경 변수 확장)을 쓴다. `%USERPROFILE%\Documents`나 `Path.home()/'Documents'`를 하드코딩하면 안 된다.
- 근거: 이 PC에서 게임이 OneDrive로 옮겨진 `문서` 폴더에 저장했다(2026-10-30, 2.9). Windows가 알려 주는 문서 폴더 위치(OneDrive 폴더 이동 포함)를 따른다는 뜻이다.
- 이 PC: `%USERPROFILE%\OneDrive\문서\Escape from Tarkov\Screenshots\` — 2026-10-30 저녁 첫 스크린샷과 함께 생겼다. 없으면 `<문서>`를 감시하다가 폴더가 생기면 붙는다. 폴더 이름은 대소문자를 무시하고 비교한다(`Escape From Tarkov` 표기도 있음). 영어 OneDrive는 `OneDrive\Documents\…`(앞 경로는 계정 종류에 따라 다름). Steam판도 같은 `문서` 폴더를 쓴다(SteamLibrary 아래에는 게임과 로그만 있음).
- 한글 경로(`문서`)에 게임이 실제로 저장한다(2026-10-30 확인, 2.9). 한국 개발자의 도구 두 개도 `OneDrive\문서\Escape from Tarkov\Screenshots`를 기본 후보로 둔다.
- 관련 로컬 파일(읽기 전용): 키 바인딩 `%APPDATA%\Battlestate Games\Escape from Tarkov\Settings\Control.ini`, Unity 로그 `%USERPROFILE%\AppData\LocalLow\Battlestate Games\EscapeFromTarkov\Player.log`.

### 2.7 함정
- **키 바인딩**: `Control.ini`의 MakeScreenshot이 `SysReq` 하나뿐이다(기본값). TarkovMonitor는 이 값을 보면 경고를 띄우고, 관리자는 기본 바인딩이 많은 사람에게 동작하지 않는다고 했다(#147, #166). 게임 설정의 조작 > 스크린샷에서 다른 키로 바꾸고, 오른쪽 아래에 저장 경로 토스트가 뜨는지 확인한다. README가 안내하는 PrtSc도 이 확인을 거친 뒤에 쓴다. F12는 Steam 스크린샷 키라 피한다. PrtSc를 Windows 11 캡처 도구가 가로채는지는 **미확인**(재지정 후 토스트와 파일 생성으로 확인).
- Steam(F12)이나 Windows 캡처 도구로 찍은 스크린샷에는 좌표가 없다. 다른 폴더에 저장되고 정규식에도 맞지 않는다.
- **사망 센티널 좌표**: 사망 순간 `(-0.1, -9997.5, -0.1)`이 찍혔다는 보고가 있다(제3자 1건). `y < -1000`은 버린다.
- **결과 화면 샷**: 사망 뒤 결과 화면에서는 카메라 블록이 빠지고 tod만 남는다(예: `2026-08-29[12-52]_19.12 (0).png`). 위치가 없으면 고정점이 아니다. 메뉴 샷은 `yyyy-MM-dd[HH-mm] (0).png`(합성 예시만 있음), 은신처 샷은 좌표만 있고 tod가 없을 수 있다. 둘 다 **미확인**(실제 샘플 한 장씩으로 확인).
- **낡은 좌표**: 메뉴 스크린샷에 이전 좌표가 붙을 수 있다는 경고가 있다(MAYAK). 그래서 파일 시각이 레이드 구간 안이고 (x, z)가 그 맵 bounds 안인 샷만 쓴다. Icebreaker는 bounds가 Factory와 같으므로 맵 판정은 반드시 로그로 한다.
- **사망 X**를 사망 후 스크린샷에서 가져오면 안 된다. 사망 전 마지막 유효 고정점을 쓴다.
- 다른 트래커가 스크린샷을 지울 수 있다(TarkovMonitor는 옵션, Tarkov Pilot은 맵이 바뀔 때, EFT-Map·Tarkovy·Tarkov Overlay·Wand는 기본으로 삭제). 파일이 나타나는 즉시 이름과 시각을 기록하고, 같이 돌아가는 트래커가 있으면 경고한다.
- OneDrive가 PNG를 전부 업로드한다(장당 수 MB). Windows "제어된 폴더 액세스"가 다른 앱의 삭제를 조용히 막을 수 있다(읽기에는 영향 없음).

### 2.8 검증 결과(2차 실행)
43개 주장 중 **확인 30, 반박 7, 확인 불가 6**. 실제 파일명 291개(kartograf 대량 픽스처 258개 포함)와 독립 파서 6종으로 다시 검증했다.

주요 정정:
- 소수 1자리(F1) 이름은 추론이 아니라 실제로 있었다(2024-10, 0.15.x). F2/F5 전환은 2024-10과 2025-03 사이다(정확한 빌드는 미확인).
- 문서 폴더 리디렉션을 따른다는 결론은 유지(이후 2.9에서 실제 저장 위치로 확인). "악센트가 들어간 경로에서 동작했다"(#116)는 제보자가 철회했으므로 비ASCII 경로의 근거가 되지 못한다.
- 클라이언트가 1.3.0.48484로 업데이트됐다. 1.3.0의 형식은 미확인(이후 2.9에서 같은 형식으로 확인).
- `|q|` 편차는 3e-6이 아니라 최대 약 7e-6.
- "위치 없는 이름 = 메뉴"라는 가정은 틀렸다. 사망 직후와 결과 화면에서 tod만 있는 이름이 나오고, 사망 센티널 좌표도 있다.
- "자동 입력하는 도구는 tarkovzero뿐"도 틀렸다. tarkovxyz-agent는 기본 모드(`spam`)에서 `SendInput`으로 키를 누른다. 우리 규칙(사용자가 직접 누름)은 그대로다.

확인 불가로 남은 것: QTtrash 테스트의 ` _ ` 표기, 지수 표기 가능성, PNG 쓰기 지연, 1.x가 저장 경로를 로그에 남기는지, 카메라 y가 눈높이(바닥 + 1.5–1.7 m)인지, 은신처·메뉴·트랜싯 샷의 이름.

### 2.9 1.3.0 실측(2026-10-30, 세관 PvE 로컬 레이드 한 판)
- 파일 99장. 위치가 있는 93장은 1.2.5와 같은 형식이다(`2026-10-30[22-07]_567.95, 2.89, -111.78_-0.00289, 0.99375, -0.02645, -0.10840_10.91 (0).png`). 93장 모두 생성 시각이 이름의 분 안에 있다.
- 위치가 없는 6장은 `날짜[시-분]_tod (n).png`이다. 배치(로딩) 화면 1장(`[22-07]_10.86 (0)`)과 레이드 종료 화면 5장(`[22-21]_12.46 (0)`~`(4)`)이다. 종료 화면에서는 tod가 레이드 마지막 값에서 멈추고 카운터만 오른다.
- 저장 위치는 OneDrive로 옮겨진 한글 문서 폴더(`OneDrive\문서\Escape from Tarkov\Screenshots`)다. 게임 토스트에 저장 경로가 나오고, `paths.find_screenshots_dir()`가 그 폴더를 찾는다.
- **좌표계 확인**: 첫 샷은 API의 PMC 스폰 지점에서 0.2 m, 마지막 샷은 Trailer Park 탈출구(PMC)에서 7.9 m 떨어져 있다. 스크린샷 좌표는 API·`maps.json`과 같은 좌표계다(3.9의 미해결 항목).
- 인게임 시계: 13.21분 동안 tod가 10.91에서 12.45로 갔다(게임 시간 92.4분). 6.99배이므로 PvE 세관도 7배속이다.
- 시간 정렬: 로그의 `GameStarted`→`local/end`는 800.0 s이고, 결과 화면의 레이드 시간은 `00:13:19`다.
- 스크린샷 사이를 직선으로 이은 거리는 1.61 km, 게임 통계의 이동 거리는 2.411 km다.

## 3. 지도와 좌표 변환

### 3.1 데이터와 맵별 값
- 출처: `the-hideout/tarkov-dev` @ `ec44177`(2026-10-20)의 `src/data/maps.json`(109,867 B, sha256 `c53a327d…`). 이 파일을 마지막으로 바꾼 커밋은 `9fd29f7`(Icebreaker 추가, 2026-07-20). Leaflet 1.9.4.
- interactive 정의 13개 + `altMaps` 별칭 3개(`ground-zero-21`→`ground-zero`, `night-factory`→`factory`, `the-lab-dark`→`the-lab`). 별칭은 부모의 기하 정보를 그대로 쓴다.
- 맵마다 `transform = [scaleX, marginX, scaleY, marginY]`, `coordinateRotation`, `bounds = [[x, z], [x, z]]`, `heightRange`, `svgPath`/`svgLayer`, `tilePath`/`tileSize`, `minZoom`/`maxZoom`, `layers`가 있다.

| 키 | transform | r | bounds | heightRange | 그림 |
|---|---|---|---|---|---|
| customs | [0.239, 168.65, 0.239, 136.35] | 180 | [[698,-307],[-372,237]] | [-1000, 1000] | SVG + 타일 |
| factory | [1.629, 119.9, 1.629, 139.3] | 90 | [[77,-64.5],[-65.5,67.4]] | [-1, 3] | SVG + 타일 |
| ground-zero | [0.524, 167.3, 0.524, 65.1] | 180 | [[249,-124],[-99,364]] | [-1000, 28] | SVG + 타일 |
| interchange | [0.265, 150.6, 0.265, 134.6] | 180 | [[598,-442],[-433,426]] | 없음 | SVG + 타일 |
| lighthouse | [0.2, 0, 0.2, 0] | 180 | [[515,-998],[-545,725]] | 없음 | SVG |
| reserve | [0.395, 122, 0.395, 137.65] | 180 | [[289,-293],[-303,244]] | [-7, 10000] | SVG(svgBounds) + 타일 |
| shoreline | [0.16, 83.2, 0.16, 111.1] | 180 | [[504,-415],[-1056,618]] | [-1000, -1] | SVG + 타일 |
| streets-of-tarkov | [0.38, 0, 0.38, 0] | 180 | [[323,-295],[-280,532]] | [-6, 10] | SVG |
| terminal | [0.20, 0, 0.20, 0] | 180 | [[463,-580],[-433,475]] | 없음 | SVG |
| woods | [0.1855, 112.95, 0.1855, 167.85] | 180 | [[646,-914],[-761,442]] | 없음 | SVG + 타일 |
| the-lab | [0.575, 281.2, 0.575, 193.7] | 270 | [[-80,-477],[-287,-193]] | [-0.9, 3] | 타일(tileSize 175) |
| the-labyrinth | [2.115, 85.5, 2.115, 128.0] | 270 | [[-52,-37],[53,76]] | 없음 | 타일 |
| icebreaker | [2.0, 125.0, 3.5, 91.0] | 180 | [[77,-64.5],[-65.5,67.4]] | [18.92, 19.56] | 타일 |

### 3.2 CRS 사슬
- `pos(p) = [p.z, p.x]` → lat = z, lng = x. y는 배치에 쓰지 않고 층 선택에만 쓴다. tarkov.dev의 탈출구, 스폰, 라벨, TarkovMonitor 플레이어 마커가 모두 이 함수를 거친다.
- 게임 (x, y, z), t = transform, r = coordinateRotation(도), Leaflet 줌 Z일 때:
  - `θ = r·π/180`
  - `x' = x·cosθ − z·sinθ`, `z' = x·sinθ + z·cosθ`
  - `X = 2^Z·(t0·x' + t1)`, `Y = 2^Z·(−t2·z' + t3)` (CSS px, Y는 아래로 증가)
  - 예외: x = z = 0이면 (0, 0), r이 0이거나 없으면 회전 생략(`applyRotation` 동작 그대로).
- 역변환: `px = (X/2^Z − t1)/t0`, `py = (Y/2^Z − t3)/(−t2)`를 구한 뒤 −r만큼 회전.
- Leaflet은 정수 px로 반올림해 그린다(최대 0.5 px).

| r | 맵 | X | Y |
|---|---|---|---|
| 180 | 아래 둘을 뺀 전부 | `2^Z·(t1 − t0·x)` | `2^Z·(t3 + t2·z)` |
| 90 | factory, night-factory | `2^Z·(t1 − t0·z)` | `2^Z·(t3 − t2·x)` |
| 270 | the-lab, the-lab-dark, the-labyrinth | `2^Z·(t1 + t0·z)` | `2^Z·(t3 + t2·x)` |

- 닫힌 식은 sin/cos 잔차(1e-16)를 없앤 형태로, 사슬 계산과 6e-14 px 안에서 같다. r = 180이면 +x가 화면 왼쪽, +z가 화면 아래쪽이다.
- 예: Customs Old Gas Station (310.71, −1.63, −174.95) → 줌 0에서 (94.39031, 94.53695), 줌 2에서 (377.56, 378.15) → 타일 `customs_0.16/main/2/1/1.png`(HTTP 200).
- 회전 방향과 Y 부호는 실제 타일 위에 API 탈출구를 찍어 확인했다(Customs r = 180, Factory r = 90, Lab r = 270에서 모두 게이트·통로 끝·도로 위에 놓임).

### 3.3 타일과 SVG 배치
- **타일**: `tileSize` 기본 256, Lab만 175. 줌 Z의 타일 (tx, ty)는 CRS px `[tx·ts, (tx+1)·ts)`를 덮고, 줌 0은 타일 하나다. 그런데 PNG 파일은 모두 256 px이라 Lab을 직접 합성하려면 `image_px = CRS_px × 256/175`. 투영한 bounds가 타일 정사각형을 넘는 맵이 있다(Customs X 최대 257.56, Interchange, Woods, Icebreaker) → 캔버스에 여백을 둔다.
- **SVG**: `L.svgOverlay(svg, svgBounds ?? bounds)`. 투영한 두 모서리 사이 사각형에 viewBox를 `xMidYMid meet`으로 맞춘다. SVG가 있는 맵은 모두 `t0 == t2`라서 transform이 상쇄되고, 회전과 bounds만으로 SVG 좌표를 구할 수 있다(`t0 ≠ t2`이면 모서리 투영 방식으로 돌아가야 한다).

| r | fx | fy | A (사각형 가로/세로) |
|---|---|---|---|
| 180 | `(xmax−x)/(xmax−xmin)` | `(z−zmin)/(zmax−zmin)` | `(xmax−xmin)/(zmax−zmin)` |
| 90 | `(zmax−z)/(zmax−zmin)` | `(xmax−x)/(xmax−xmin)` | `(zmax−zmin)/(xmax−xmin)` |
| 270 | `(z−zmin)/(zmax−zmin)` | `(x−xmin)/(xmax−xmin)` | `(zmax−zmin)/(xmax−xmin)` |

  - `vbW/vbH ≥ A`이면 `u = vbX + fx·vbW`, `v = vbY + fy·vbW/A − (vbW/A − vbH)/2`
  - 아니면 `v = vbY + fy·vbH`, `u = vbX + fx·vbH·A − (vbH·A − vbW)/2`
  - SVG 10개 모두 viewBox가 `0 0`에서 시작한다(원점이 0이 아니면 중첩 svg 때문에 어긋남). Customs는 사각형 비율 1.96691과 viewBox 비율 1.98530이 달라 위아래로 0.60 px(약 2.5 m) 레터박스가 생긴다. 나머지 맵은 0.3% 이내.
- **svgBounds**: Reserve에만 있다. SVG는 `[[289,-274],[-303,272]]`로 놓고, 타일과 층 사각형은 `bounds`를 쓴다. svgBounds로 놓으면 루트 위치 1,080개 중 83.3%가 건물 안에 떨어지고, bounds로 놓으면 26.9%뿐이다(1차 검증).
- 기본 그림은 최상위 `<g id == svgLayer>`와 `data-keep-with-group == svgLayer`인 그룹이다. 나머지 최상위 `<g>`는 층이다. 층을 켜면 그 층 그룹만 보이고 다른 층 그룹은 숨는다. 기본 그림은 남은 채 투명도 0.2로 흐려진다(`show: true`인 층이면 흐리지 않음).

### 3.4 층 판정
- 형식: `layers[].extents = [{height: [min, max], bounds?: [[[x1,z1],[x2,z2],"라벨"], …]}]`. bounds 항목의 세 번째 원소는 라벨일 뿐이다.
- 점 하나에 대해: `min ≤ y < max`(반열림)이고, bounds가 있으면 (x, z)가 사각형 중 하나 안(경계 포함)이어야 한다. `maps.json` 순서대로 처음 맞는 층이 이긴다. 아무 층도 맞지 않으면 기본 층(heightRange, 없으면 ±∞, 그리고 맵 bounds). tarkov.dev는 플레이어 위치가 새로 들어올 때마다 이 판정으로 층을 자동 전환한다.
- Customs 예: ZB-1011 (621.5, −2.07, −126.48)은 `[-1000, 0.5)`이고 zb-1011 사각형 안이라 Underground. ZB-1012, ZB-1013, Old Gas Station, Boiler Room 지하도 Underground. Smugglers' Boat(y −12.63)는 어느 지하 사각형에도 없어 기본 층. 기숙사 2층은 y ∈ [2.7, 6.5)이고 `[[243,190],[165,125]]` 안.
- Lab: 기본 층 `heightRange [-0.9, 3]`, "Second Level"은 y ≥ 3이고 사각형 안, "Technical"은 y < −0.9.
- tarkov.dev의 탈출구 아이콘은 점 대신 top/bottom으로 판정해서 결과가 조금 다르다(점으로는 Underground인 Old Gas·ZB-1013이 아이콘으로는 기본 층). 스크린샷 위치는 점이므로 위 규칙을 쓴다.
- 스크린샷 y는 눈높이라 바닥보다 1.5–1.7 m쯤 높을 것으로 본다. **미확인**. 층 경계가 낮은 곳(기숙사 2층 시작 2.7 m 등)에서 층을 잘못 고를 수 있다(확인 방법: 층을 아는 곳에서 서서·앉아서 찍은 샷 몇 장).

### 3.5 로그 맵 ID → tarkov.dev 키
규칙: JSON API에서 `nameId == 로그 id`인 맵의 `normalizedName`을 찾고, 그 값이 `key`이거나 `altMaps`에 들어 있는 interactive 정의를 쓴다. 로그 id는 API `nameId`와 대소문자까지 일치한다.

| 로그 id | 정의(키) | 한국어 이름 |
|---|---|---|
| `bigmap` | customs | 세관 |
| `factory4_day` / `factory4_night` | factory (야간은 별칭) | 공장 / 야간 공장 |
| `Sandbox` / `Sandbox_high` | ground-zero (21+는 별칭) | 그라운드 제로 / 그라운드 제로 21+ |
| `laboratory` / `laboratory_dark` | the-lab (Dark는 별칭) | 연구소 / 연구소 (Dark) |
| `Labyrinth` | the-labyrinth | 미궁 |
| `Woods` | woods | 삼림 |
| `Shoreline` | shoreline | 해안선 |
| `Interchange` | interchange | 인터체인지 |
| `RezervBase` | reserve | 리저브 |
| `Lighthouse` | lighthouse | 등대 |
| `TarkovStreets` | streets-of-tarkov | 타르코프 시내 |
| `Terminal` | terminal | 터미널 |
| `Icebreaker` | icebreaker (**신뢰 불가**, 3.9) | 쇄빙선 |
| `factory_event` | factory (대체 정의, 이벤트) | 오염된 공장 |
| `laboratory_event` | the-lab (대체 정의, 이벤트) | 오염된 연구소 |
| `Sandbox_start` | ground-zero (대체 정의) | Ground Zero 튜토리얼 (tarkov.dev 번역 그대로) |

tarkov.dev 지도 정의가 없는 아래 세 id는 `scripts/update_maps.py`의 `ALIASES`로 같은 지형의 맵에 붙인다(2026-10-30).
- `factory_event`, `laboratory_event`: 1.3.0 이벤트 맵. 실제 로그의 레이드 9개(공장 3, 연구소 6)에서 `UserConfirmed`, TRACE, 씬(`factory_event.scenespreset.asset`, `Laboratory_Event.ScenesPreset.asset`)이 모두 같은 id다. json.tarkov.dev는 2026-10-29 21:20 UTC 스냅숏부터 이 둘을 `infected-factory`, `infected-lab`으로 싣는다(탈출구 0개). infected-factory 스폰 79개는 모두 공장 오브젝트에서 10 m 안(높이 차 중앙값 0.2 m)에 있고, infected-lab 스폰 145개는 연구소(Dark) 스폰과 같은 점이다. 그래서 기본 맵의 지도와 탈출구에 API 스폰을 더해 그리고, 뷰어에 이벤트 맵 안내를 띄운다.
- `Sandbox_start`: 그라운드 제로 튜토리얼(실제 로그의 레이드 2개, 트랜짓 체인 끝, 씬 `Sandbox_SL`). API 스폰 17개와 루즈 루트 2개에서 가장 가까운 GZ 오브젝트까지 높이 차 중앙값이 0.3 m다(세관은 19.2 m). 탈출구가 0개라 GZ 탈출구를 쓴다.

### 3.6 API
- 사용할 것: `GET https://json.tarkov.dev/{regular|pve|pvp-season}/maps`, 번역 `…/maps_ko`와 `…/maps_en`, 목록 `https://json.tarkov.dev/endpoints`. tarkov.dev 지도 페이지도 이것을 쓴다. 무료, 요청 한도 없음, 새 데이터가 올 때까지 캐시된다.
- 응답: `{data: {maps: {<mapId>: {...}}, goonReports, mobs, …}, translations}`. 맵마다 `extracts`(id, name, faction, switches, position, outline, top, bottom), `transits`, `spawns`(zoneName, position, sides, categories), `bosses` 등이 있다. `name`, `extracts[].name` 같은 텍스트는 번역 키(예: `ex_customs_scav_gasstation`)이므로 `maps_ko`로 풀어서 쓴다.
- regular와 pve의 위치는 19개 맵 모두 같다. pve에는 보스 항목이 더 많다(Customs regular 4개, pve 12개).
- `coordinateToCardinalRotation`(JSON API에만 있음)은 Reserve 195.209처럼 `coordinateRotation`과 다른 값이 있다. 나침반 북쪽일 수 있다(Reserve SVG가 15°쯤 기울어 있음). 의미는 **미확인**이고 tarkov.dev 지도 코드는 쓰지 않는다.
- GraphQL `https://api.tarkov.dev/graphql`: 2026-10-29 12:33–12:56 UTC와 21:20 UTC에 모든 요청이 HTTP 422 `GraphQL server unavailable`이었다. 사이트도 "Legacy API, 유지보수 모드"라고 적어 둔다. 소스(@`92f40c6`)에서는 같은 메시지를 503으로 내므로 배포본이 다르고 원인은 알 수 없다. GraphQL에는 기대지 않는다.

### 3.7 Customs 검산
- bounds: x ∈ [−372, 698], z ∈ [−307, 237]. 줌 0 투영 사각형은 X 1.828–257.558, Y 62.977–192.993.
- API 탈출구 28개(pmc 10, scav 17, 공용 1, 고유 이름 22개) 중 **27개가 bounds 안**에 있다. 바깥의 1개는 Railroad Passage (Flare)로 z_min보다 15.83 m 밖이다(그려진 플레어 구역이 테두리에 닿는 자리).
- 지하 탈출구 5개(ZB-1011, ZB-1012, Old Gas, ZB-1013, Boiler Room 지하)가 `maps.json`의 Underground 사각형 안에 정확히 들어간다. API와 `maps.json`이 같은 (x, z) 좌표계라는 독립 근거다.
- 렌더 확인: Customs.svg 위에 탈출구 28개와 라벨 24개를 찍으면 기숙사, 주유소, 다리, 강 같은 그림과 맞는다. svgBounds를 쓰는 Reserve도 맞는다.
- 다른 맵(탈출구 / 트랜싯 / 스폰이 bounds 안에 든 비율): factory 9/10 · 3/3 · 113/119, Woods 19/20, Interchange 8/9 · 트랜싯 0/2(9–12 m 밖), Sandbox 6/9, Icebreaker 0/1 · 47/56. 대부분 1.5–16 m 밖이므로 캔버스를 16 m쯤 넉넉히 잡는다.
- Ground Zero의 `UN Roadblock`(−527.87, 288.23)과 `Scav Bunker`(220.43, −705.80)는 Woods 데이터가 잘못 들어간 것이다. 버린다.

### 3.8 라이선스
| 대상 | 라이선스 | 우리에게 의미 |
|---|---|---|
| tarkov.dev 코드(`maps.json`, `getCRS` 포함) | MIT, "Copyright (c) 2019 Oskar Risberg" | 고지를 남기면 이식할 수 있다. `src/data`에 별도 데이터 라이선스는 없다 |
| 지도 SVG(`tarkov-dev-svg-maps` @`5a8b611`) | CC BY-NC-SA 4.0 + README의 사용 금지·철회 조항 | 출처("Shebuka / tarkov-dev-svg-maps contributors")와 라이선스 링크 표기, 비상업, 수정본은 같은 라이선스. 자체 LICENSE와 함께 별도 폴더에 둔다 |
| 래스터 타일(`assets.tarkov.dev/maps/*/{z}/{x}/{y}.png`) | 찾지 못함 | 재배포하지 않는다 |
| API 데이터 | 라이선스·이용약관·출처 요건을 찾지 못함 | tarkov-api와 tarkov-data-manager는 코드만 GPL-3.0 |
| Leaflet | BSD-2-Clause | 고지 |
| TarkovMonitor | GPL-3.0 | 코드를 복사하지 않는다(참고만) |
| 게임 콘텐츠 | Battlestate Games 상표·저작권 | 고지 |

- 철회 조항: 치트나 부당한 이득을 돕는 소프트웨어(인게임 레이더·ESP 오버레이, 치트 클라이언트용 지도, 자동화 스크립트·픽셀 봇)에 쓰면 라이선스가 자동으로 철회되고 DMCA 대상이 된다고 적혀 있다.
- 해석(법률 자문 아님): 게임이 남긴 파일만 쓰는 레이드 후 리뷰는 열거된 용도에 들지 않고, tarkov.dev도 이 SVG 위에 스크린샷 위치를 찍는다. 그래도 레이드 중 실시간 오버레이는 넣지 않는다. 광고나 후원이 NC 조건에 걸리는지는 **미확인**(tarkov-dev-svg-maps GitHub Discussions에 문의 가능).
- assets.tarkov.dev는 CORS `*`, 7일 캐시 헤더를 보내지만 핫링크 약관은 없다. 오프라인 동작과 라이선스 안전을 위해 GitHub의 SVG를 받아 캐시하는 편이 낫다.

### 3.9 미해결
- **Icebreaker**: bounds가 Factory의 복사본이고, transform `[2.0, 125.0, 3.5, 91.0]`은 축마다 배율이 다르다. 유일한 탈출구 Helicopter가 줌 0에서 Y = −140.1로 선체 그림(Y 26–255 안팎)보다 훨씬 위에 찍히고, 스폰 9/56이 bounds 밖이다. 이 맵의 위치는 믿지 않고, 경로를 그릴 때 경고를 띄운다.
- ~~스크린샷 좌표가 API와 같은 좌표계라는 것은 간접 근거뿐이다.~~ 2026-10-30 세관 실측으로 확인했다: 첫 샷이 API PMC 스폰에서 0.2 m, 마지막 샷이 Trailer Park 탈출구에서 7.9 m(2.9).
- `Sandbox_start`와 이벤트 맵 2개는 tarkov.dev 지도 정의가 없어 3.5의 대체 정의로 그린다(근거는 3.5). Terminal은 API 탈출구가 0개. `Labs.svg`는 저장소에 있지만 `maps.json`이 쓰지 않는다.
- 배포된 SVG 7개는 GitHub 파일과 바이트가 다르다(viewBox, 최상위 그룹 id, path 수는 같음).

### 3.10 검증 결과
- 2차 실행: 28개 주장 중 **확인 24, 반박 1, 확인 불가 3**. 반박은 GraphQL 422의 원인 설명 하나(코드상 503). 확인 불가는 스크린샷 좌표계 동일성, 래스터 타일·핫링크 약관, 라이선스 해석. 보강된 사항은 Lab 타일 PNG 크기(256 vs 175), SVG 공식의 `t0 == t2` 조건, 층을 켰을 때 기본 그룹의 처리, 새 이벤트 맵 2개, GZ 이상치가 Woods 데이터라는 점.
- 1차 실행: 34개 중 확인 29, 반박 2, 확인 불가 3. 반박은 요약 표에서 svgBounds, tileSize, 줌 범위가 빠졌다는 것과, 층 자동 전환이 "add 이벤트"가 아니라 마커 추가 직후의 직접 호출이라는 것(결론은 같음).

## 4. 게임 로그

### 4.1 폴더와 파일
- 위치: `<SteamLibrary>\steamapps\common\Escape from Tarkov\build\Logs\log_<YYYY.MM.DD>_<H-MM-SS>_<버전>\`. 시(hour)는 0을 채우지 않는다(`log_2026.10.24_9-53-05_…`). 폴더 시각은 프로세스 시작 시각(로컬)이고 첫 줄은 약 1 s 뒤에 찍힌다.
- 폴더 하나가 클라이언트 프로세스 하나다. 레이드가 두 폴더에 걸치는 일은 없으므로 폴더 단위로 나누고 합치지 않는다.
- 파일 이름은 `<stamp> <종류>_<NNN>.log`. 2026-10-30 기준 세션 373개, 파일 3690개가 모두 `_000`이었다(output 최대 52 MB, backend 22.6 MB). glob으로 모두 읽는다. UTF-8 `errors=replace`로 읽고 CR을 지운다(대부분 CRLF, 일부 LF).
- 모든 세션에 `application_000.log`가 정확히 1개 있다. push-notifications가 없는 세션은 9개, backend가 없는 세션은 1개다. `backend_queue_000.log`는 13세션에서 세션 중간에 생겨 끝까지 남는다(backend glob에 함께 잡힌다).
- 파일은 이어 쓰기만 되고, 이름이 바뀌거나 지워지지 않는다. 스캐너는 이 성질에 기댄다: 캐시에 있던 파일이 작아지거나 사라졌으면 폴더가 지워지는 중으로 보고 캐시를 지킨다.

| 파일 | 쓸모 |
|---|---|
| `application_000.log` | 생명주기의 중심: 모드, 매칭, 씬 로드, `[Transit]`, `GameStarted`, 취소, 온라인 위치 보정. 로컬 레이드 중에는 조용하다 |
| `backend_000.log` | HTTP 요청·응답 URL만 남는다(본문 없음): `match/local/start`·`end`, `match/join`·`exit`, `putMetrics`, keepalive(140 s), logout |
| `push-notifications_000.log` | 웹소켓 알림과 JSON 본문: `UserConfirmed`, `UserMatchOver`, `ChatMessageReceived` 등. 그룹 알림에 다른 플레이어 정보(PII)가 있다 |
| `output_000.log` | 모든 로거의 상위 집합 + Unity 로그. 합계 4 GB, 세션당 최대 52 MB. `GameTimer`(UTC), 로컬 GameEnd 스택, 정상 종료 마커 |
| `errors_000.log`, `files-checker_000.log`, `network-connection_000.log` | 오류 스택 / 시도 직전 무결성 검사 / 온라인 서버 연결(IP 포함) |
| 그 밖(aiData, inventory, insurance, spatial-audio …) | 봇 진단, 인벤토리 오류 등. 결과와는 무관 |

### 4.2 줄 형식
```
^(?P<ts>\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}\.\d{3})\|(?P<ver>[^|]*)\|(?P<level>[^|]*)\|(?P<logger>[^|]*)\|(?P<msg>.*)$
```

- 맞지 않는 줄은 앞 항목에 이어지는 줄(JSON 본문, 스택, HTTP 오류 헤더)이다. push 본문은 이어지는 줄을 합쳐 `json.loads`로 읽는다.
- 밀리초 해상도이고 같은 밀리초에 여러 이벤트가 겹친다(예: `GameRunned`부터 `GameStarted`까지). 정렬 키는 (ts, 출처 순서 app < backend < push < output, 파일 내 순서).
- 몇몇 핵심 줄은 Debug 레벨이다(`TRACE-…`, `Matching with group id`).

### 4.3 레이드 생명주기 마커
| 단계 | local(클라이언트 호스팅) | online(서버 호스팅) |
|---|---|---|
| 대기 시작 | `MatchingCompleted:0 …` | `Matching with group id: …` |
| 맵 로드 시작 | `scene preset path:maps/<bundle> rcid:<rcid>`(공통, 취소된 시도 포함) | 같음 |
| 로드 완료 | `LocationLoaded:…` | 같음 |
| 등록 | backend `/client/match/local/start` | backend `/client/match/join`, push `UserConfirmed`, `TRACE-NetworkGameCreate profileStatus: … Location: <id>, …` |
| RaidId | `[Transit] Flag:<f>, RaidId:<24hex>, Count:<n>, Locations:<a> -> <b> -> `(유일한 출처) | 없음 |
| 시작 | `GameStarted:…`(공통, 조작할 수 있게 되는 순간) | 추가로 약 0.2 s 뒤 ``[Transit] `<pid>` Count:<n>, EventPlayer:…`` |
| 종료 | backend `--->` `/client/match/local/end` 요청. output의 GameEnd 스택(`Quest with id … wasn't found!` + `BaseLocalGame…GameEnd`)이 0.4–1 s 더 이르다 | 가장 이른 것: push `UserMatchOver`, output 연결 종료(`Disconnect`, `Stop game`, `INetworkGame.Stop()`), `/client/putMetrics` 요청, 시작 후 첫 `PrepareSelectedProfileLocally`, 시작 후 `GC mode switched to Enabled`. 서로 0–20 s 차이 |
| 시작 전 취소 | `Local game matching cancelled.` | `/client/match/exit`, 시작 전 `UserMatchOver`, `Network game matching cancelled.` 중 하나(마지막 줄이 빠질 수 있음) |

- `MatchingCompleted` 값이 0보다 크면 온라인 대기 시간이지 로드 시간이 아니다. `PrepareSelectedProfileLocally`는 레이드 뒤에 2–4번 반복된다. `BEClient exit`는 메뉴에 들어올 때마다 도는 BattlEye 주기일 뿐 앱 종료가 아니다. `GC mode switched to Enabled`는 로딩 중에도 나오므로 `GameStarted` 이후 것만 본다.
- 1.2.5에서 생긴 줄: `PVE transit hop local:… location:<id> …`(로컬 트랜싯의 다음 구간), `Transit reconnect status:… location:<id> …`(온라인 트랜싯).
- 빌드별 차이: 1.0.4.1까지는 `SelectProfile`, 1.0.4.5부터 `PrepareSelectedProfileLocally`. `INetworkGame.Stop()`은 1.1.0까지. push 접두어 변경과 WebSocketSharp 줄은 1.2.0부터.

### 4.4 맵 ID
- 우선순위: `[Transit]` 체인의 마지막 원소 > `UserConfirmed.location` / TRACE `Location` > hop·reconnect의 `location` > rcid 표. 인벤토리에서는 각각 462 / 149 / 3 / 14건에 쓰였다.
- rcid 표(이름이 같은 것은 생략): `bigmap.scenespreset.asset`→`bigmap`, `Rezerv_Base`→`RezervBase`, `Shopping_Mall`→`Interchange`, `city`→`TarkovStreets`, `Sandbox_SL`→`Sandbox_start`, `factory_day`/`factory_night`→`factory4_day`/`factory4_night`, `woods`→`Woods`, `shoreline`→`Shoreline`, `lighthouse`→`Lighthouse`, `sandbox`/`sandbox_high`→`Sandbox`/`Sandbox_high`.
- 온라인 트랜싯에서는 다음 구간의 `UserConfirmed`가 SCENE 줄보다 먼저 올 수 있다. 늦게 온 SCENE으로 새 시도를 열지 말고 열려 있는 시도에 붙인다.

### 4.5 종료 판정과 클라이언트 종료
- 레이드 중(IN_RAID) 상태에서 파일이 끝나면 `client_closed_in_raid`다. 리서치에서는 끝 시각을 모든 파일의 최대 타임스탬프로 잡았다. 실제로는 그 뒤에 껐을 수도 있으니 지속 시간은 하한값이다.
- 1.0.6 이상에서 이런 116건 모두 output에 정상 종료(`TerminateBrowserProcess`)가 있고 `/client/game/logout`은 없다. 충돌이 아니라 사용자가 끈 것(Alt+F4 등)이고, 결과는 알 수 없다.
- 로컬 레이드 중에는 application 로그가 조용하다. 살아 있는지는 backend keepalive(140 s 간격)로 대강 알 수 있다. IN_RAID 중에 다음 SCENE이 오면 `ended_unknown`으로 닫는다(실제로는 한 번도 없었다).

### 4.6 트랜싯 연결
- 다음 시도가 트랜싯 구간인 조건: 앞에 hop/reconnect 마커가 있다, 또는 같은 RaidId에 Count가 1 늘었다, 또는 온라인 ``[Transit] `pid` Count:n``에서 n > 0. 그리고 간격이 15분 미만.
- 앞 레이드를 `exit_via_transit`으로 표시하고 체인으로 묶는다. RaidId는 체인 전체에 하나이므로 구간 키는 (RaidId, Count)다.
- `Flag`는 대부분 Common(457줄)이고 None이 5줄(Sandbox_start 3, PvpSeason Sandbox 1, factory4_day 1). None의 의미는 **미확인**.

### 4.7 모드
- `Session mode: Pve`(370개 세션, 374줄)와 `Session mode: PvpSeason`(3개 세션: 2026-08-12에 두 번, 2026-08-20). `Regular`는 없다.
- 세션 중간에 모드를 바꿀 수 있고, 바꿀 때마다 `Session mode:` 줄과 `/client/game/mode`, `/client/game/start`가 다시 찍힌다. 각 시도의 모드는 직전 `Session mode:` 값으로 정하고 게이트웨이 호스트(`gw-pve…`와 `gw-pvp-season…`)로 확인한다.
- PvE 안에도 두 종류가 있다: local(클라이언트 호스팅, RaidId 있음)과 online(서버 호스팅 협동, RaidId 없음, 위치 보정 줄 있음). PvpSeason의 온라인 레이드도 PvE 온라인과 마커가 같다. `UserMatchOver`는 PvP 전용이 아니다. PvpSeason에서 로컬 레이드가 2건 있었다(시즌 시작 레이드로 추정, **미확인**).

### 4.8 인벤토리 수치(2026-10-29 생성)
- 세션 371개(레이드가 있는 세션 320개, 한 세션 최대 12회), 빌드 25종(1.0.1.0.42625부터 1.2.5.0.48146까지).
- 시도 628건 중 시작된 레이드 605건(2025-12-31 16:49부터 2026-10-27 22:48 KST까지).
- 종류: Pve local 454, Pve online 148, PvpSeason local 2, PvpSeason online 1(local 합계 456, online 합계 149).
- 상태: `ended_local` 269, `ended_online` 143, `client_closed_in_raid` 193(32%). 시작 전에 끝난 시도: `cancelled_local` 6, `cancelled_network` 8, `client_closed_before_start` 9.
- 결과 분류: 트랜싯 41, 보험 신호 13, 클라이언트 종료 193, 나머지 358은 알 수 없음.
- RaidId가 있는 레이드 456건(로컬 전부). 위치 보정 줄이 있는 레이드 98건(줄 645개).

| 맵 id | 레이드 (local / online) | 맵 id | 레이드 (local / online) |
|---|---|---|---|
| Woods | 95 (89 / 6) | Icebreaker | 43 (0 / 43) |
| RezervBase | 68 (52 / 16) | Interchange | 30 (25 / 5) |
| laboratory_dark | 65 (62 / 3) | factory4_night | 27 (27 / 0) |
| factory4_day | 61 (58 / 3) | Lighthouse | 23 (19 / 4) |
| bigmap | 53 (34 / 19) | TarkovStreets | 19 (0 / 19) |
| laboratory | 52 (48 / 4) | Sandbox_high | 7 (6 / 1) |
| Shoreline | 51 (30 / 21) | Sandbox | 5 (4 / 1) |
| Labyrinth | 4 (0 / 4) | Sandbox_start | 2 (2 / 0) |

PvpSeason 레이드 3건은 Sandbox local 1, Sandbox online 1(이 데이터에서 유일한 실제 PvP 레이드), Sandbox_start local 1이다.

### 4.9 결과는 로그에 없다
- 없는 것: 종료 상태(생존/사망/MIA/Runner/트랜싯), 탈출구 이름, 킬러, 킬 목록, 피해 부위, 사망 시각. 모든 로그 종류를 뒤졌다(output 620만 항목 포함).
- `Player.OnGameSessionEnd(ExitStatus, pastTime, locationId, exitName)` 같은 메서드 시그니처는 NullReference 스택에만 나오고 값은 찍히지 않는다. 로컬 레이드 결과는 `/client/match/local/end` 요청 본문에 실리는데 backend 로그는 URL만 남긴다.
- 얻을 수 있는 신호:
  - 트랜싯 탈출(41건): 구조적으로 확실하다.
  - 레이드 중 클라이언트 종료(193건): 결과 불명.
  - **보험 신호**(온라인 13건, 로컬 0/456건): 종료 0–3 s 뒤 push `ChatMessageReceived`, `message.type == 2`, `systemData {date, time(MSK), location}`. SPT 재구현에서는 보험 처리를 시작하는 메시지라서 사망·MIA, 또는 보험 든 장비를 두고 나왔다는 뜻이다. 라이브에서의 의미는 **미확인**이므로 약한 힌트로만 쓴다.
  - 시작 후 `UserMatchOver`(온라인 8건, 그중 6건은 보험 메시지와 같은 밀리초): 약한 신호. 레이드 끝의 퀘스트 알림(type 11/12)은 결과가 아니다.
- 사망 시각은 종료 마커로 어림할 수밖에 없는데, 탈출해도 같은 마커가 나온다. 결과·킬러·탈출구는 OCR(5장)로 얻는다.

### 4.10 온라인 위치 보정 줄
```
Reason:<Stuck|PacketsQueue|Speed|Lift>, Position:(x, y, z), SpeedLimit:<f>, CurrentState:<state>, StrengthSummaryLevel:<n>, WalkSpeedLimit:<f>
```

- 로그에 있는 유일한 플레이어 좌표다. 서버 호스팅 레이드에만 있다(온라인 98/149, 로컬 0/456). 모두 645줄이고 레이드당 중앙값 1개, 최대 114개.
- Reason 분포: Stuck 262, PacketsQueue 211, Speed 118, Lift 54. 이동 검증에서 위치를 바로잡은 기록으로 보인다(**미확인**). 스크린샷과 같은 월드 좌표계라는 가정도 미확인(확인 방법: 같은 레이드의 스크린샷 위치와 비교).
- 경로로 쓰기에는 너무 드물어 기준점이나 검증용으로만 쓴다. output의 `physics mbp settings applied: Center: (…), Extents: (…)`는 맵별 대략적인 월드 경계 상자라 좌표 검산에 쓸 수 있다.

### 4.11 시계
- 로그 줄과 폴더 이름: 오프셋 표시가 없는 로컬 벽시계(이 PC는 KST). HTTP 오류 뒤에만 나오는 ` responseHeaders:{Date:… GMT}`와 비교하면 78/78 표본에서 정확히 +9.0 h였다.
- 본문 안의 다른 시계:
  - output `GameTimer utcNow/start/escape`, `RealDateTime`: UTC(`escape − start`가 레이드 제한 시간)
  - `GameDateTime`: 인게임 시각(7배속)
  - push `sid` 접미사 `_dd.mm.yy_HH-MM-SS`와 보험 메시지 `systemData.time`: 모스크바 시각(UTC+3)
  - push `dt`: Unix epoch 초(UTC)
- 권장: 원래 로컬 문자열과 PC 시간대 오프셋을 함께 저장하고, UTC 변환은 한 번만 한다. 보험 메시지는 레이드 종료 시각을 MSK(KST − 6 h)로 바꿔 ±3분 안에서 맞춘다.

### 4.12 개인정보
로그에서 읽더라도 **원문으로 저장하지 않는다**:
- ProfileId(24hex), AccountId, `Matching with group id:` 값(그룹원의 AccountId)
- push의 `profileid`, `profileToken`, `ip`, `sid`, `shortId`, 그룹 알림의 `aid`·`Nickname`·`KillerName`, 플리마켓 판매 알림의 `buyerNickname`
- backend 웹소켓 URL의 `lt=` JWT와 getwebsocket 토큰
- inventory·errors 줄의 `[<pid>|<Nickname>|Profile]` 접두어(사용자 닉네임 포함)
- network-connection과 TRACE 줄의 게임 서버 IP:포트

RaidId는 레이드 키로 보관해도 된다(리서치 픽스처도 RaidId는 그대로 둠). 픽스처의 대체값은 원래 모양을 지킨다: 프로필 id `aaaa…NNNN`, 그 밖의 24hex `bbbb…NNNN`, 계정 id `9999NNN`, IP `203.0.113.N`, 토큰 글자는 `x`, 레이드 short id `XXXXNN`, `sid` 앞의 게임 서버 이름 `XX-XX00X00N`(대소문자 모양 유지, 뒤의 시각은 그대로). 리서치 산출물은 로그 전체에서 모은 실제 ProfileId·AccountId·IP·토큰·닉네임과 대조해 일치 0건을 확인했다.

## 5. 레이드 후 화면과 OCR

### 5.1 화면 순서
종료 상태 → 킬 목록 → (스캐브 레이드만) 스캐브 인벤토리 → 통계 → 경험치 → (치료할 수 있을 때) 치료. 첫 화면의 **메인 메뉴** 버튼을 누르면 뒤 화면을 모두 건너뛰므로 캡처는 그 전에 해야 한다. 화면 구조는 SPT가 공개한 EFT 0.16.9 어셈블리 기준이고, 1.x 화면 배치는 **미확인**이다.

### 5.2 종료 상태 화면
| 항목 | 한국어 표기(kr 로캘) | 메모 |
|---|---|---|
| 결과 문구 | 생존 / 작전 중 사망 / 작전 중 실종 / 작전 중 이탈 / 도주. | Transit 패널은 없다(0.16.9). '도주.'의 마침표는 로캘 원문 그대로 |
| 레이드 시간 | `레이드 시간:` + `hh:mm:ss` | T0 보정에 쓴다 |
| 킬러 | `사살한 사람 : ` + 닉네임 | 사망 시. 플레이어 스캐브 킬러도 닉네임이 나온다 |
| 치명 부위 | 예: `흉부, 위쪽 등`(Thorax, Upper back) | 일부 값은 kr에서도 영어(Thorax, Neck 등) |
| 그 밖 | 닉네임·레벨, 경험치, 경고(도주 판정 설명), 프로필 보기, 다음 / 메인 메뉴 | 왼쪽 아래에 클라이언트 버전 워터마크 |

- 0.16.9 기준으로 탈출구 이름, 킬러의 무기·탄·거리는 화면에 없다. `사망 시간`(DEATH TIME) 로캘 키는 있지만 그 필드가 없다. 1.x에서 생겼는지는 **미확인**.
- **1.3.0 한국어 실측(생존, 2026-10-30)**: 화면 순서는 요약 → `사살 명단` → `레이드 통계`(스크롤해 2장) → `획득한 경험치`였다. 요약 화면은 제목 `레이드 종료`, 부제 `이른 탈출`, 결과 `생존`(초록 띠), `레이드 시간:` `00:13:19`, 경험치, 레벨·닉네임으로 되어 있고, 왼쪽 아래에 `1.3.0.0.48484 | PvE`가 있다. 탈출구 이름은 없다. 경험치 화면의 결과 띠는 `생존함`이다.
- `레이드 통계`(1.3.0)는 `건강 및 신체 상태`, `파밍`, `전투` 칸으로 나뉜다. 전투 칸에는 레벨 구간별 `사살한 플레이어 수 (레벨 31 ~ 50)`, `BEAR`·`USEC`·`스캐브`·`PMC 사살 횟수`, `헤드샷 횟수`, `장거리 헤드샷 횟수`, `적을 맞춘 가장 먼 거리`(`130.94`), `최다 연속 사살 횟수`, 무기 종류별 `사살한 횟수 : 돌격소총`이 있다. 킬 목록의 `???` 행을 좁히는 데 쓴다(5.3). 파밍 칸에는 `이동한 거리 (Km)`, `약탈한 시체` 등이 있다.

### 5.3 킬 목록
- 제목은 `레이드 종료` / `사살 명단`. 열: `#`, `시간`, `플레이어`, `레벨`, `진영`, `상태`. `LOCATION` 열은 번역되지 않았고 예전 스크린샷에서는 보이지 않았다. 킬이 없으면 `사살 없음`. 9줄이 넘으면 스크롤이 생기므로 여러 장을 찍어 `#`로 중복을 지운다.
- 상태 칸 하나에 무기·부위·거리가 다 들어간다(툴팁 불필요):
  - `Killed (<무기>, <부위>, <거리>m)` — 예: `Killed (SA-58, right arm, 7.6m)`
  - `Headshot (<무기>, <거리>m)` — 헤드샷은 부위를 생략
  - 한국어 예상형: `사살 (SA-58, 흉부, 31m)`, `헤드샷 (…)`. 한글과 라틴 문자가 한 칸에 섞인다.
- 거리는 치명탄의 거리이고 소수 1자리까지, `.0`은 생략한다(형식 `0.#`). 부위는 흉부/복부/머리/왼팔/오른팔/왼다리/오른다리(영문 UI는 소문자 영어).
- 레벨: PMC는 숫자, AI는 `--`. 진영: USEC/BEAR는 라틴 문자, 스캐브는 `SCAV`일 가능성이 높다(`스캐브`일 수도 있음, **미확인**). 역할은 보스/경호원/레이더/로그/저격수, 컬티스트는 `???`, 감염자 `Infected`, 밀수업자, RUAF는 `AF`.
- 도그태그를 챙기지 않은 PMC: 이름과 상태가 `???`, 레벨이 `?`. 시간과 진영은 보인다. 1.3.0 PvE AI PMC도 같고, 진영은 BEAR/USEC 대신 `PMC`로만 나온다(아래 1.3.0 실측).
- 무기는 짧은 이름이고 kr에서도 대부분 라틴 문자다(SVDS, Mk 16, AKM …). `VSS "Vintorez"`처럼 러시아어 음역이 보인다. kr의 AK-74N 짧은 이름은 키릴 문자 `А`(U+0410)로 시작하므로 비교 전에 동형 문자를 정규화한다.
- 배치(0.12.12의 2560×1440과 0.14.8의 1080p 실측. 화면 높이에 비례, 가로 중앙 정렬). 2560×1440 기준:
  - 표 패널 x 614–1946, y 253–1053. 행 글자띠 y = 359 + 80i부터 383 + 80i까지(i = 0..8).
  - 열 x: `#` 660–682, 시간 729–864, 플레이어 887부터, 레벨 1271–1302, 진영 1339–1418, 상태 1491부터 약 1900.
- 글꼴: 라틴 문자는 Bender이고 0에 빗금이 있다(`Ø`처럼 보임). 시간 열은 어두운 회색(약 RGB 100) 가는 이탤릭이 배경(20–30) 위에 있어 대비가 아주 낮다.
- **1.3.0 한국어 실측(2026-10-30, 킬 6개)**:
  - 열은 `#`, `지역`, `시간`, `플레이어`, `레벨`, `진영`, `상태`다. LOCATION 열이 `지역`으로 번역되어 보이고, 값은 `세관`이다.
  - 상태는 `헤드샷 (ADAR 2-15, 130.9m)` 꼴이다. 한글 단어, 라틴 무기 짧은 이름, 소수 1자리 거리 + `m`이 한 칸에 들어간다.
  - 진영: `USEC`, `PMC`는 라틴 문자이고, 역할은 한국어다(`저격수`, `감염자`).
  - 레벨: AI는 `--`, 도그태그 없는 PMC는 `?`. 그 PMC 행은 이름과 상태가 `???`이고 진영은 `PMC`다.
  - 도그태그 없는 PMC의 진영, 레벨 구간, 부위·무기는 `레이드 통계`(5.2)로만 좁힐 수 있다. 이번 판은 `BEAR 사살 횟수`와 `USEC 사살 횟수`가 1씩이고 이름이 보이는 PMC가 USEC(레벨 58)이라 남은 하나가 BEAR였다. 그 USEC이 `사살한 플레이어 수 (레벨 51 ~ 70)`에 들어가므로 BEAR는 `(레벨 31 ~ 50)`이다. `헤드샷 횟수`와 `사살한 횟수 : 돌격소총`이 킬 수 6과 같아 부위(머리)와 무기 종류도 알 수 있었다. 무기 이름은 다른 행이 모두 `ADAR 2-15`라 그것으로 보았고, 거리는 알 수 없다. 도그태그 없는 `???` PMC 행이 둘 이상이고, 이름이 보이는 PMC를 뺀 `BEAR`·`USEC` 사살 횟수가 둘 다 남으면 어느 행이 어느 진영인지 가를 수 없다(사용자 지적). 그때는 편집기 진영을 `PMC`로 둔다. 한쪽 진영만 남으면 `???` 행은 모두 그 진영이다(README 사용법 3단계와 같은 규칙).
  - `감염자`는 이벤트 기간에만 나오는 특수 적이다(사용자 설명). 이름은 `???`, 레벨은 `--`다. 통계는 감염자를 스캐브로 센다(`스캐브 사살 횟수` 4 = 저격수 2 + 감염자 2). 이벤트가 끝나면 쓰지 않을 진영이라 편집기 진영 목록에 넣지 않고 `기타` + 메모 `감염자`로 적는다.
  - 봇 이름은 라틴 음역이다(예: `Alik Stameska`).
  - 시간 열은 예상대로 회색 가는 이탤릭이다.

### 5.4 킬 TIME과 정렬
- TIME은 **레이드 경과 시간**이다(`VictimStats.Time`은 TimeSpan). `00:00:45`처럼 0을 채운 `HH:MM:SS`이고 초 단위다(±1 s, 버림인지 반올림인지는 미확인). 벽시계도 인게임 시각도 아니다.
- 0점은 `GameTimer` 시작으로 보이고, PvE PMC 레이드에서는 application 로그의 `GameStarted:` 줄에 해당한다(추론). 이 PC에서는 `PlayerSpawnEvent`보다 20–60 s 늦게 나온다.
- 정렬 절차:
  1. 킬 목록 스크린샷을 레이드에 배정한다. 촬영 시각이 그 레이드의 종료(`local/end`) 뒤이고 다음 시도 시작 전이어야 한다.
  2. `T0 = GameStarted`(기본). 보정값 `T0' = t(local/end) − RAID TIME`. PMC 레이드에서는 둘이 몇 초 안에서 맞아야 한다. 크게 다르면(스캐브 레이드의 앞당겨진 타이머 등) `T0'`를 쓴다. 예(2026-10-20 Factory): `GameStarted` 00:55:25.943, `local/end` 01:05:13.463이면 RAID TIME은 `00:09:4x` 정도로 나와야 한다.
  3. `kill_wallclock = T0 + TIME + [0, 1) s`. `???` 행도 시간은 쓸 수 있다.
  4. 그 시각의 경로 위치를 보간해 킬 점을 찍는다. 피해자 위치는 화면에 없으므로 거리만큼 원을 그려 줄 수 있다(우리 아이디어). 킬 직후 1 s 안에 찍은 샷이 있으면 쿼터니언이 대략적인 피해자 방향을 알려 준다.
  5. 사망: 시각 = `T0 + RAID TIME`(≈ `local/end`), 위치 = 그 전 마지막 유효 고정점.
- 온라인 레이드에는 `local/end`가 없으니 온라인 종료 마커로 대신해야 하고 0–20 s 오차가 생긴다(추론).
- 트랜싯 체인에서 시간이 맵마다 0부터 다시 시작하는지, LOCATION 열이 어떻게 붙는지는 **미확인**(두 맵 트랜싯 한 번으로 확인).
- 한 번은 킬 직후에 스크린샷을 찍어 `T0 + TIME`과 비교한다. 남은 몇 초 오차와 버림/반올림을 함께 확인할 수 있다.

### 5.5 봇 이름
- 서버 데이터에서 스캐브 이름, 레이더·경호원 코드명, 보스, 컬티스트는 키릴 문자다. 클라이언트의 `Transliteration` 클래스가 라틴 문자로 바꿔 보여 준다. 예: Kocherga, Zimniy, Svinec, Yastreb, Irbis, Sektant, Zhrets, "Maksim Minskiy". 로그(Rogue), Goons, Black Division은 원래 라틴 이름이고 감염자는 `???`.
- 추정 규칙: х→h, щ→sh, ж→zh, ч→ch, ц→c, й→y, ю→yu, я→ya, ё→yo(가끔 e), ь는 생략. ы, э, ъ는 미확인.
- 한국어 클라이언트도 라틴 음역이 나올 가능성이 높다(kr 로캘에는 이름 데이터가 없고 음역 표는 키릴→라틴). 키릴 원문이 그대로 나올 위험은 남는다(**미확인**, 한국어 킬 목록 한 장이면 확인된다).
- 매칭: SPT 봇 이름 목록을 위 규칙으로 음역해 사전을 만들고, 진영 열로 동명이인(경호원 Kocherga와 성이 Kocherga인 스캐브)을 가른다.

### 5.6 OCR 권장안
**1순위: Windows OCR(`Windows.Media.Ocr`) + PyWinRT 4**
- 이 PC에 설치된 인식 언어는 `ko` 하나다(en-US, ru-RU 없음). 오프라인, CPU만 사용, 잘라낸 조각당 약 100–250 ms, 모델 다운로드 없음.
- 패키지: `winrt-runtime` 4.0.3(cp311–cp315, Python 3.14 가능), `winrt-Windows.Media.Ocr` 등 순수 파이썬 휠 약 1 MB + pillow. `winsdk`는 지원이 끊겼고 cp312까지라 쓰지 않는다.
- 실측(ko 엔진, 실제 2560×1440 영어 킬 목록):

| 항목 | 그대로 | 열별 자르기 + 자동 대비 + 반전 + 3배 확대 |
|---|---|---|
| 헤더 / 진영 / 레벨 | 5/5 · 9/9 · 3/3 | 같음 |
| 이름 | 5/9 | 7/9 |
| 상태 | 6/9 (소수점 누락: `21.4m`→`214m`) | 단어 혼동은 어휘 보정으로 해결, `4.1m`→`41m`은 남음 |
| 시간 | 0/9 | 여전히 못 읽음(기울기 보정도 효과 없음) |

- 합성 한국어 UI 문자(맑은 고딕 25 px): 레이드 종료, 사살 명단, 플레이어, 헤드샷, 흉부, 작전 중 사망 등 모두 정확. 19 px에서는 한 글자씩 틀림(흉부→홀부, 이탈→이달). 키릴 문자는 못 읽는다.

파이프라인:
1. 제목(사살 명단 / 레이드 종료 / 결과 문구)으로 화면 종류를 판정한다.
2. 고정 행 간격(1440p에서 80 px)으로 행을 나눈다.
3. 진영·상태: ko OCR → 정규식 → 로캘에서 만든 어휘(상태 단어, 부위, 역할)와 무기 짧은 이름 목록에 맞춘다.
4. 플레이어: en-US 인식기가 있으면 그것을 쓴다. 설치는 관리자 PowerShell의 `Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"`(시스템 변경이므로 사용자가 결정). 없으면 ko로 읽어 한글을 걸러 내고 봇 이름 사전과 퍼지 매칭한다.
5. 시간·레벨·거리 숫자: 사용자 스크린샷에서 한 번 잘라 둔 글리프 템플릿(0–9, `:`, `.`, `-`, 빗금 0)으로 연결 요소를 분류한다. 글꼴과 크기가 고정이라 결과가 결정적이고, 빗금 0과 소수점 문제를 없앤다.

**예비안: RapidOCR 3.10 + onnxruntime 1.31.1**(cp314 휠, 관리자 권한 불필요). PP-OCRv5 모델(korean, eslav/cyrillic, en 인식기)을 쓰고 신뢰도 점수를 준다. 첫 실행 때 modelscope.cn에서 모델을 받으므로 오프라인용으로 미리 묶어 둔다. Tarkov Stammtisch 킬 카운터도 같은 화면에 PaddleOCR을 쓴다.

**비권장**: PaddleOCR 본체(Windows 휠 cp313까지, 무거움), EasyOCR(torch 필요, ko와 ru 모델을 한 Reader에 못 씀), Tesseract(이 UI에서 가장 약함: `Headshat`, `SVOS`, `7@.5m`, 시간 열 통째 누락).

OCR로 얻은 문자열은 툴팁에 넣기 전에 HTML 이스케이프한다.

### 5.7 실제 한국어 스크린샷으로만 풀리는 질문
2026-10-30 세관 한 판(5.2, 5.3의 1.3.0 실측)으로 일부가 풀렸다. 봇 이름은 라틴 음역이고, 역할은 한국어(`저격수`, `감염자`)다. 거리는 `130.9m`, 무기는 라틴 짧은 이름(`ADAR 2-15`)이고, `지역` 열이 보인다. 아래는 원래 질문 목록이다.
- 봇 이름이 라틴 음역인가 키릴인가 / `SCAV`인가 `스캐브`인가 / 거리 단위 표기 / 한글 글꼴 때문에 열 위치가 밀리는가
- 1.2.5·1.3.0 킬 목록: 1.1.5.0에 생긴 프로필 보기·신고 컨트롤의 모양, LOCATION 열이 언제 보이는가, 부위에 좌우나 세부 구역이 붙는가(Stammtisch 파서가 2026-04에 `bodyPartSide`를 추가)
- 1.x 종료 화면에 `사망 시간`, 킬러 무기·거리, 탈출구 이름이 있는가
- 무기 문자열이 러시아어 음역인가 현지화된 짧은 이름인가(보정 어휘가 달라진다)
- kr UI의 한글 글꼴과 크기(지금까지는 맑은 고딕 합성 문자로만 시험)
- RapidOCR이 이탤릭 빗금 숫자와 소수 거리를 얼마나 잘 읽는가(설치가 허용되지 않아 미측정)

## 6. 선행 사례와 기존 도구

### 6.1 Raid Review(SPT 전용, MIT)
- 저장소: `Chazut/SPT-RaidReview`(활성 포크, 1.9.0 준비 중, SPT 4.1.x, HEAD `2567b37`, 2026-10-26). 원본 `ekky-llc/spt-raid-review`는 2026-04-01에 보관 처리됐다(HEAD `6e5b9a6`). 둘 다 MIT("Copyright (c) 2023 ekky-llc").
- 구조:
  1. **클라이언트**: BepInEx 플러그인(C#/.NET 4.7.2). Harmony 패치와 Unity 코루틴으로 기록하고 WebSocket(`ws://<서버>:7828`)으로 보낸다. 큐(2048)가 넘치면 `RECORDING_INCOMPLETE`로 표시.
  2. **서버 모드**: SPT 4.1 서버 모드(C#/.NET 10, 자체 Kestrel). HTTP 7829에서 REST API와 웹 UI를 낸다. 이벤트는 SQLite에, 위치는 레이드별 CSV에 이어 붙였다가 레이드 후 플레이어별 JSON으로 컴파일한다. 레이드 id는 서버가 만든 GUID다(EFT RaidId가 아님).
  3. **웹 UI**: React 18 + Vite + Leaflet 1.9.4. tarkov.dev `maps.json` 스냅숏과 CRS 코드를 그대로 쓴다.
- 위치 표본: `PlayerTrackingInterval = 5f` → `WaitForSeconds(0.2)`, 명목 5 Hz(실제로는 프레임 단위로 끊김). 한 틱의 모든 플레이어가 같은 ms 시각을 공유하고, 플레이어마다 x, y, z, HP, 봇 상태, 몸 방향(`atan2(forward.x, forward.z)`)을 남긴다. 23명이 나온 레이드 하나에 위치 표본 153,457개.
- 킬 행: `time`(`GameWorld.OnGameStarted`부터 잰 스톱워치 ms), 킬러·피해자 profileId, `weapon`(클라이언트 언어의 짧은 이름), `distance`(사망 순간 두 플레이어 사이 3D 직선거리), `bodyPart`(EBodyPart), 킬러·피해자 위치. 피해 종류는 보내기만 하고 저장하지 않는다. 킬러가 없는 사망(지뢰, 낙하)은 상태 행 'Dead'로만 남는다.
- EFT가 레이드 끝에 실제로 가진 데이터(SPT 모델 기준): `Stats.Eft.Victims[]`(이름, 진영, 부위, Time 문자열, 거리, 레벨, 무기, ColliderType, Role, Location. 위치는 없음), Aggressor·DamageHistory·DeathCause, `EndRaidResult`(result = ExitStatus(Transit 포함), killerId, exitName, playTime 초). 모두 `/client/match/local/end` 본문에 있어 우리 로그에는 없다. 우리에게 남은 길은 화면 OCR뿐이다. Raid Review의 기록 계층은 코드 주입이라 쓸 수 없고, 데이터 모델과 뷰어 아이디어만 빌린다.
- 참고: RaidRecord(SunYanbox)는 클라이언트 없이 서버에서 `/client/match/local/start`와 `end`만 가로챈다. 저장소 라이선스는 GPL-3.0인데 메타데이터에는 CC-BY-SA로 적혀 있어 서로 맞지 않는다. 코드는 가져오지 않는다.

### 6.2 빌려올 UX
- **선형 ms 타임라인**: Raid Review의 슬라이더는 표본 인덱스 기준(비선형)이라 드문드문한 스크린샷에는 맞지 않는다. 킬 ☠, 사망 ✕ 눈금을 두고, 호버하면 툴팁, 클릭하면 그 시각으로 이동하며 `flyToBounds`. 레이드 시간 `HH:MM:SS`와 벽시계를 함께 보여 준다.
- **재생**: `requestAnimationFrame` 고정 주기에 배속(1×/4×/16×/64×)만큼 시간을 진행하고 점 사이는 선형 보간한다(타이머 간격을 줄이는 방식은 16×에서 그리지 못했다). 궤적은 최근 N초만 보기와 전체 보기(`Preserve`)를 둔다. 폴리라인은 한 번 만들고 `setLatLngs`로 잘라 보여 준다.
- **따라가기**: 처음 한 번만 줌하고, 이후 매 프레임 `panTo(pos, {animate: false})`(애니메이션 이동은 떨린다, issue #24).
- **포커스·스타일**: 호버한 대상 외에는 투명도 0.1 정도. 조밀한 점에는 팝업 대신 `bindTooltip`. 마커는 유지한 채 `setLatLng`만 하고 아이콘 HTML이 바뀔 때만 교체. 기본 지도는 채도를 낮추고(`grayscale/sepia` 필터) `preferCanvas`, 줌에 따라 점 크기 조절.
- **층**: 현재 점의 y로 층을 자동 선택하고, 경로를 층별 구간으로 끊어 층을 관통하는 선을 그리지 않는다. "선택한 층만" 옵션, 위층을 볼 때 기본 지도 투명도 0.3.
- **방향 화살표**: 10 m 앞 점을 `map.project`로 투영해 화면 각도를 구한다(맵별 회전·반전 상수가 필요 없음).
- **불확실성 표시**(우리 추가): 시간 간격이 긴 구간은 점선이나 회색으로 그려 보간을 관측으로 오해하지 않게 한다.
- **데이터 모델**: 모든 이벤트는 레이드 상대 ms + 레이드당 절대 기준 시각 하나. 위치는 `{t_ms, x, y, z, yaw?, source}`. 킬 행은 EFT Victim 필드(이름, 진영, 레벨, 역할, 부위, 무기, 거리, 시간)와 1:1로 맞춘다. 레이드 키는 GUID 대신 EFT RaidId. ZIP/JSON 내보내기.
- 피할 점: 인덱스 기반 슬라이더, 실시간이 아닌 1×, 존재 여부만 세는 히트맵(체류 시간으로 가중), 층 자동 추적 없음.

### 6.3 라이브 EFT 도구 조사(2026-10 기준)
| 도구 | 위치 소스 | 기록 | 킬 | 메모 |
|---|---|---|---|---|
| TarkovMonitor (GPL-3.0) | 파일명 → tarkov.dev 원격 마커 | 없음 | 없음 | 활발(2.2.1.0). 레이드 후 스크린샷 자동 삭제 옵션 |
| tarkov.dev 원격 마커 | TarkovMonitor·Tarkov Nexus가 보냄 | 최신 점 1개 | 없음 | 궤적 없음 |
| Tarkov Pilot (tarkov-market) | 파일명을 서버로 보내 서버가 파싱 | 없음 | 없음 | 맵이 바뀌면 스크린샷 삭제. 2026-10-03 "Pilot heatmap" 추가(사용자 위치를 서버에서 모은 것으로 추정) |
| Tarkov Nexus (MIT) | 파일명(TarkovMonitor 정규식) | 없음 | 없음 | 파티 위치 공유 |
| Tarkov Map Companion (MIT) | 파일명 + 선택적 Windows OCR(탈출구 목록) | 메모리 궤적(기본 12개, 전체 가능) | 없음 | 가장 비슷하다. 메뉴로 돌아가도 다음 레이드까지 궤적 유지. 저장·타임라인·킬은 없음 |
| Tarkov Overlay (MIT) | 파일명 | 메모리 궤적 10개 | 없음 | 저장하지 않음 |
| Fiiish Pilot, EFT-Map, TarkovEyes, Tarkovy, TarkovQuestie | 파일명 + 로그 | 최신 점 | 없음 | 모두 사용자가 키를 누름. TarkovEyes는 "결과는 로그에 없다"고 문서화 |
| Wand | 파일명, 약 1 Hz 자동 스크린샷 | 없음 | 없음 | 연습·오프라인 레이드로 제한 |
| Overwolf EFT 이벤트 | 없음(맵, 레이드·세션 종류, match_start/end, 내용 없는 death) | 없음 | 없음 | 좌표와 킬 이벤트가 없다 |
| Tarkov Stammtisch Kill Counter | 없음 | 없음 | 있음 | 킬 목록 스크린샷을 서버에서 OCR(PaddleOCR). 독일어·영어 UI만, 시간·위치 없음 |
| SPT Raid Review | 클라이언트 플러그인 | 전체 재생 | 있음 | SPT 전용(라이브 불가) |
| TarkovReplay (2023) | 다른 플레이어 위치까지 → 메모리 리더로 추정 | 있음 | 없음 | 따라 하면 안 되는 사례 |

- tarkovxyz-agent는 기본 모드에서 스크린샷 키를 `SendInput`으로 자동 입력한다. TarkovMonitor의 자동 스크린샷 PR #152(SendInput, 관리자 권한 필요)는 "BSG가 곱게 보지 않을 수 있다"는 이유로 병합되지 않았다. SendInput에는 anti-cheat가 볼 수 있는 'injected' 플래그가 붙는다.
- "Raid Signal"은 웹, GitHub, Overwolf 어디서도 찾지 못했다. 링크가 있어야 평가할 수 있다.

**빈틈(이 프로젝트가 채우는 것)**: 라이브 PvP/PvE에서 레이드별로 저장되는 기록(경로, 맵, RaidId, 모드, 시작·종료), 되감아 볼 수 있는 타임라인, 경로 위의 킬 점, 사망 X, 한국어 UI를 함께 주는 도구는 없다. 처리도 전부 로컬이다(Pilot은 파일명을 서버로 보내고, 원격 마커는 socket.tarkov.dev를 거친다).

### 6.4 약관과 BattlEye
결론: BSG는 파일을 읽는 보조 도구에 대해 공식 판단을 낸 적이 없다. 약관의 스크립트·자동화 조항은 넓게 쓰여 있다. 위험도는 **키 자동 입력 > 레이드 중 실시간 오버레이 > 레이드 후 로컬 분석** 순이다(마지막 판단은 추론).

- 파일명·로그 읽기: 승인하거나 금지한 발언이 없다. 2019년 BSG 개발자가 "스크린샷에 지도 좌표가 저장돼 도움이 된다"며 버그 제보에 첨부를 요청했다. 좌표가 의도된 기능이라는 뜻일 뿐 제3자 도구를 허가한 것은 아니다. 2023년부터 널리 쓰였는데 관련 공개 밴 사례는 찾지 못했다(미확인). 2023-06 데이터마이닝 정책은 "게임 코드나 DB에 침투해 숨겨진 정보를 꺼내는 것"으로 정의하므로 게임이 사용자에게 남긴 파일은 해당하지 않는다. 2026-04 안티치트 개발 일지도 보조 앱은 언급하지 않는다.
- License Agreement(2026-06-15판):
  - 4.2.10: 정보를 수집하거나 게임과 관련된 동작을 하는 자동 스크립트 금지. 넓게 읽힐 수 있지만 로그·스크린샷 리더에 집행된 사례는 없다.
  - 4.3.4(a)(d): 게임 진행을 바꾸거나 쉽게 하거나 다른 플레이어보다 유리하게 만드는 미허가 소프트웨어 금지. 실시간 위치 오버레이가 걸릴 수 있다.
  - 4.3.4(b): 게임이나 그 기능을 자동으로 실행하는 봇 금지. 스크린샷 키 자동 입력이 여기에 해당할 수 있다.
  - 4.3.4(c), 4.2.5: 클라이언트 수정·리버스 엔지니어링 금지(SPT식 기록기를 쓸 수 없는 이유).
  - 4.2.2: 게임 영상물 무단 배포 금지(공유할 때만 문제).
- Rules of the Game(2024-02-27판): 2.6.5 팬 사이트·도구 허용(조건부), 2.6.7.2 상표·저작권 고지, 2.6.7.8 서면 허가 없는 상업적 이용 금지 → 비상업으로 두고 고지를 표시한다.
- 선례: 2023-02 BSG는 게임 프로세스를 건드리지 않는 화면 필터 DWM_lut을 "부당한 이득"을 이유로 막았다. 프로세스에 접근하는지가 아니라 레이드 중 이득으로 판단한다는 뜻이고, 레이드 후 분석은 이 기준에서 가장 안전하다.
- 매크로: 2018년 BSG는 "어떤 매크로도 지원하지 않는다"고 했다. BattlEye는 매크로 도구 사용자를 킥할 수 있다(밴이 아니라).
- BattlEye EULA: RAM과 게임·시스템 파일을 스캔할 수 있으므로 우리 프로세스도 보인다. 파일만 읽는 도구에는 문제가 없고, 주입이나 메모리 읽기는 배제된다. PvE에서도 BattlEye가 돈다(로그의 `BEClient`).
- 설계 원칙: 키는 사용자가 직접 누른다(자동 입력 없음). 레이드가 끝난 뒤에만 보여 준다. 외부 통신은 tarkov.dev 지도 자산(SVG·타일)과 뷰어의 Leaflet CDN뿐. 비상업. BSG와 지도 제작자 고지를 표시한다.

## 7. 구현 반영 현황(2026-10-30)

- **로그 파서**: `eftjourney/logs.py`를 4장의 규칙으로 다시 썼고, 리서치 인벤토리(레이드 605개)와 대조했다.
  - 605/605 레이드가 맵, 종류(local/online), 모드, 종료 사유, RaidId, 트랜싯 플래그, 보험 힌트, 위치 보정 개수에서 모두 같다.
  - 종료 시각 차이는 예상한 범위다. 로컬 레이드 ≤1 s(output GameEnd 스택 대신 `local/end` 요청 시각), 온라인 ≤30 s(온라인 종료 마커 선택의 차이), 레이드 중 종료된 레이드는 4 GB짜리 output 로그 대신 application/backend/push 로그의 마지막 줄에서 끝난다.
- **회귀 픽스처**: 익명화한 세션 4개가 `tests/fixtures/logs/`에 있다(세션마다 application, backend, push-notifications 로그).
  - `log_2026.10.27_22-32-44_1.2.5.0.48146`: 온라인 검색 취소 → 로컬 Customs 레이드 완료 → 로그아웃
  - `log_2026.10.18_14-27-00_1.2.5.0.48146`: 같은 RaidId로 factory4_day → laboratory 로컬 트랜싯, 두 번째 구간에서 클라이언트 종료
  - `log_2026.08.12_20-40-54_1.1.0.1.46699`: PvpSeason 온라인 그룹 레이드, PvE 로컬 레이드, `match/exit`과 `UserMatchOver`로 취소된 온라인 검색
  - `log_2026.10.22_21-19-37_1.2.5.0.48146`: 온라인 레이드, `Transit reconnect` 온라인 트랜싯, 종료 시 보험 메시지, 위치 보정 줄
- **스크린샷 파서**: `eftjourney/screenshots.py`가 리서치의 실제 예시 파일명 8개를 모두 파싱하고, heading이 기준값(191.73°, 206.85°, 122.53°, 20.97°, 35.54°, 15.32°, 268.52°, 286.01°)과 맞는다. 실제 이름 일부와 사망 센티널·결과 화면 이름 거부는 `tests/test_screenshots.py`에 회귀 테스트로 들어 있다.
- **독립 검토 후 수정(2026-10-30)**: 검증된 문제 36건(중복 포함)을 고쳤다. 이 문서의 권고와 관련된 것만 적는다.
  - 2.7의 권고대로 스크린샷은 처음 본 순간 `data/cache/shots.json`에 위치·시각을 기록한다. 파일이 지워져도 경로는 남는다.
  - 로그 폴더가 지워지거나 사라져도 이미 읽은 세션의 레이드는 캐시와 저장소에 남는다. 읽기 실패한 세션은 캐시하지 않고 다음 스캔에서 다시 읽는다.
  - 온라인 트랜싯에서 다음 구간의 `UserConfirmed`가 현재 구간의 종료 마커보다 몇 ms 먼저 찍히면, 그 알림을 다음 구간에 붙인다. 실제 데이터 605개 중 1개(Shoreline이 Icebreaker로 잘못 잡힌 구간)가 바로잡혔다.
  - 3.9의 Icebreaker 경고를 뷰어에 넣었다(`maps.json`의 `unreliable`). 3.7의 Ground Zero 삼림 탈출구 2개는 맵 범위 75 m 밖이라 뺀다. 야간 공장·Ground Zero 21+는 자기 탈출구 목록을 쓴다.
- **2차 재검증 후 수정(2026-10-30)**: 1차 수정을 새 에이전트로 다시 검증해 남은 문제 14건(일부만 고쳐진 3건, 수정이 새로 만든 11건)과, 그 과정에서 찾은 기존 문제를 고쳤다. 이 문서와 관련된 것만 적는다.
  - 4.1의 "이어 쓰기만 된다"는 성질을 써서, 프로그램이 켜진 채 로그 폴더를 지워도 캐시된 레이드를 지우지 않는다. 파싱 도중 파일이 사라지거나 짧아진 세션은 저장하지 않고 다음 스캔에서 다시 읽는다.
  - 진행 중 레이드: 그 세션 폴더가 보이지 않으면 게임이 꺼진 것을 확인할 때까지 숨긴다. 게임이 꺼진 것을 확인한 시각(`data/cache/state.json`)보다 먼저 끝난 레이드는 다시 숨기지 않는다.
  - 2.7의 저널: 읽지 못하면 그 스캔은 레이드를 쓰거나 지우지 않는다. 손상되면 `.bad-<시각>`으로 옮겨 두고 새로 시작한다. 확장자만 바뀐 파일은 같은 스크린샷으로 본다.
  - 4.10의 위치 보정 줄도 맵 범위에서 75 m 넘게 벗어나면 뺀다(실제 Icebreaker 293개 중 1개).
  - 3.4 층 판정에서, 층을 고르지 않은 지상에도 tarkov.dev의 'full' 규칙(어떤 층의 사각형과 높이 안에 완전히 들어가는 지명은 그 층 것)을 적용한다. Interchange 지상의 지명이 77개에서 14개가 됐다.
  - 3.5의 대체 정의를 `maps.json`에 넣었다. 1.3.0 이벤트 맵 레이드 9개와 튜토리얼 레이드 2개에도 지도가 붙는다.
  - 같은 데이터 폴더를 두 프로그램이 함께 감시하지 못하게 OS 잠금(`data/cache/eftjourney.lock`)을 잡는다. 감시 창과 뷰어(`주의 N`)에 스캔 경고가 보인다.
- **3차 재검증 후 수정(2026-10-31)**: 이 문서와 관련된 것만 적는다.
  - 2.7의 저널: 스크린샷을 지운 뒤에는 저널이 위치의 유일한 기록이어서, 저널이 지워지거나 손상되거나 예전 사본으로 바뀌면 다음 스캔이 경로를 비웠다. 이제 저장된 레이드 경로의 지점으로 빠진 위치를 다시 채운다(좌표는 파일명에서, 시각은 레이드 시작 + 지점의 t, 0.1 s 단위). 시작 시각은 적힌 벽시계 그대로 읽으므로 PC 시간대가 바뀌어도 결과가 같다. 파일이 남아 있고 그 시각이 맞으면 파일의 정확한 시각을 쓰고, t가 같은 지점은 저장된 순서를 지킨다. 읽을 수 없는 항목은 파일에 그대로 두고 경로에는 저장된 위치를 쓴다(그 스크린샷이 파일 시각을 잃은 사본으로 돌아와도 저장된 시각을 쓴다). 시작 + t가 파일 이름의 분과 맞지 않으면(자리표시 날짜, 시간대가 바뀐 시작 시각, 손상된 t) 이름의 분 단위로 되살리고, 이름과 크게 어긋난 저널 시각은 읽을 수 없는 항목으로 본다. 복원이 중간에 실패하면 다음 스캔에서 다시 시도한다. 이전 버전의 저널은 손상으로 보지 않고 그대로 읽고, 저널은 레이드를 쓰기 전에 저장한다.
- **4차 재검증 후 수정(2026-11-01)**: 이 문서와 관련된 것만 적는다.
  - 2.7의 저널 복원: 같은 스크린샷을 담은 저장 레이드가 여럿이면 시각이 맞는 쪽을 쓴다(먼저 읽은 레이드의 시작이 틀리면, 다른 레이드에 정확한 시각이 있어도 분 단위로 되살렸다). 적힌 벽시계가 이름의 분과 맞지 않으면, 그 시작이 가리키는 순간을 지금 시간대로 바꿔 한 번 더 맞춰 본다(다른 도구가 UTC로 다시 쓴 시작). 이름에 분이 없는 항목도 Windows가 다룰 수 없는 시각(1970년 이전, 9999년)이면 읽을 수 없는 항목으로 본다.
  - 로그 없이 진행 중 레이드를 가릴 때, 지금보다 늦게 찍힌 묶음도 레이드 최대 길이(3시간) 안이면 함께 검사한다. 레이드 중 PC 시계가 뒤로 맞춰지면 진행 중인 레이드가 공개됐다.
  - 손으로 고친 편집 파일을 읽을 수 없으면, 스캔이 그 편집을 옮기거나 덮어쓰지 않는다. BOM이 붙은 UTF-8 파일도 읽는다.
- 아직 안 한 것: 킬 화면 OCR, 1.3.0 로그에서 분류되지 않은 줄 전수 점검(8장). 1.3.0 파일명은 2.9에서 확인했다.

## 8. 남은 질문

| 영역 | 질문 | 확인 방법 |
|---|---|---|
| 스크린샷 | ~~1.3.0.48484에서도 파일명 형식이 같은가~~ 같다(2.9) | — |
| 스크린샷 | `SysReq` 기본 키가 이 PC에서 동작하는가, PrtSc로 바꾸면 Windows 캡처 도구가 가로채는가 | 다시 지정한 뒤 토스트와 파일 생성 확인 |
| 스크린샷 | `OneDrive\문서` 한글 경로에 게임이 저장하는가 — 저장한다(2.9) | — |
| 스크린샷 | PvE에서도 tod가 7배속 시계 공식을 따르는가(세관 PvE는 6.99배, 2.9). Labs·Labyrinth·Ground Zero 시계도 멈춰 있는가 | 그 맵에서 한 레이드 안 연속 샷의 tod 증가율 |
| 스크린샷 | 은신처·메뉴·트랜싯 샷의 이름, 사망 직후 카메라 좌표(배치 화면과 결과 화면은 tod만 있고 카운터가 오른다, 2.9) | 상황마다 한 장씩 |
| 스크린샷 | 생성 시각의 쓰기 지연, 1.x가 저장 경로를 로그에 남기는지 | 키 누른 시각과 비교, Player.log와 output 로그 검색 |
| 스크린샷·지도 | 카메라 y와 바닥의 높이 차(층 오판 위험), 맵별 +Z와 나침반 북쪽의 관계 | 층을 아는 곳에서 서서·앉아서 찍기, 게임 나침반과 비교 |
| 지도 | ~~스크린샷 좌표가 API·`maps.json`과 같은 좌표계인가~~ 같다(2.9: 스폰 0.2 m, 탈출구 7.9 m) | — |
| 지도 | Icebreaker 보정값. `Sandbox_start`·이벤트 맵을 3.5의 대체 정의로 그리고 있는데, 이벤트 맵의 탈출구가 기본 맵과 같은가 | tarkov.dev 갱신 확인, 기준점 3개 이상으로 자체 보정 |
| 지도 | GraphQL API가 폐기 중인가, `coordinateToCardinalRotation`의 의미 | tarkov.dev 저장소와 공지 확인 |
| 지도 | SVG의 NC 조건에서 후원·광고가 허용되는가, 관리자가 레이드 후 리뷰 도구를 어떻게 보는가, 래스터 타일 라이선스 | tarkov-dev-svg-maps Discussions에 문의 |
| 로그 | 1.3.0에서 마커 이름이나 순서가 바뀌었는가. 2026-10-30 세션 2개의 레이드 11개(온라인 PvE)는 그대로 읽혔지만, 분류되지 않은 줄은 전수 점검하지 않았다 | 분류되지 않은 줄 확인 |
| 로그 | PvpSeason의 정확한 의미와 그 모드의 로컬 레이드, `Flag:None`, PMC/스캐브 구분 | PvpSeason·스캐브 레이드 뒤 로그 비교 |
| 로그 | 로그 회전(`_001`, 373세션에서는 없었다. 생기면 4.1의 가정이 깨진다), 위치 보정 줄의 의미, 보험 메시지의 라이브 의미(지금은 SPT 해석) | 긴 세션 관찰, 결과를 아는 레이드와 대조 |
| OCR | 5.7의 한국어 화면 질문 전부 | 한국어 클라이언트의 실제 결과 화면 스크린샷 |
| OCR | 스캐브 레이드·트랜싯 체인에서 킬 TIME의 0점, 온라인 레이드 정렬 오차(종료 마커 0–20 s) | 킬 직후 스크린샷과 `T0 + TIME` 비교 |
| 도구 | "Raid Signal"은 무엇인가 | 링크 확보 |

## 9. 출처

스크린샷
- Unity Vector3 F2 / Quaternion 곱셈·F5 @a322ce5: https://github.com/Unity-Technologies/UnityCsReference/blob/a322ce5f78a82cf7ea211857a45338136ed7a22f/Runtime/Export/Math/Vector3.cs#L494-L500 , https://github.com/Unity-Technologies/UnityCsReference/blob/a322ce5f78a82cf7ea211857a45338136ed7a22f/Runtime/Export/Math/Quaternion.cs#L97-L117
- TarkovMonitor 파서·yaw 공식 @3cf361e: https://github.com/the-hideout/TarkovMonitor/blob/3cf361efc49fecf6bffce3cae567f55a303757cd/TarkovMonitor/GameWatcher.cs#L456-L522
- TarkovMonitor 기본 키 문제 #147, #166: https://github.com/the-hideout/TarkovMonitor/issues/147 , https://github.com/the-hideout/TarkovMonitor/issues/166
- kartograf 형식 문서(실제 이름 258개 픽스처) @e97936d: https://github.com/thorteo/kartograf/blob/e97936d6bebfcdf1c121c26160f1eee159b91344/docs/research/eft-screenshot-format.md
- devkey 블로그(forward 벡터, 아핀 보정): https://blog.devkey.jp/en/posts/escape-from-tarkov-screenshot-location/
- tarkovxyz-agent 사망·결과 화면 기록 @c323d0c: https://github.com/TheoM83/tarkovxyz-agent/blob/c323d0c8a51f0ef092186c81173e9b6781f6acb4/tests/test_out_of_world.py
- 2024년 F1 실제 PNG @7d904be: https://github.com/lxlermit/tarkov_where_i_am_on_map/tree/7d904bef60783541d0fd8e483fcef572b6db6267
- 타르코프 시계 공식 @90ebbbf: https://github.com/adamburgess/tarkov-time/blob/90ebbbf019a3dead22ead3d374b3e17e089282ad/src/time.ts#L1-L20
- BSG 개발자 2019년 글(좌표 저장): https://devtrackers.gg/escapefromtarkov/p/41f50896-mags-falling-through-ground

지도
- tarkov.dev `maps.json` @ec44177: https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/data/maps.json
- tarkov.dev CRS(`getCRS`, `applyRotation`, `pos`): https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/pages/map/index.jsx#L44-L90
- tarkov.dev 층 판정과 기본 레이어·SVG 배치: https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/pages/map/index.jsx#L131-L260 , https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/src/pages/map/index.jsx#L826-L1104
- Leaflet 1.9.4 `ImageOverlay._reset`: https://github.com/Leaflet/Leaflet/blob/v1.9.4/src/layer/ImageOverlay.js#L223-L234
- JSON API: https://json.tarkov.dev/endpoints , https://json.tarkov.dev/regular/maps , https://json.tarkov.dev/regular/maps_ko
- tarkov-api 스키마 @92f40c6: https://github.com/the-hideout/tarkov-api/blob/92f40c6ecc32dcfab67cb2ff9a00c864c54b79a3/schema-static.mjs#L860-L889
- tarkov.dev LICENSE(MIT): https://github.com/the-hideout/tarkov-dev/blob/ec44177bd3c21aa2b33938153acd0a96dbd939aa/LICENSE
- 지도 SVG 라이선스와 철회 조항 @5a8b611: https://github.com/the-hideout/tarkov-dev-svg-maps/blob/5a8b6115d1c0cf56f2ebaac1a96fa5ae3074d178/LICENSE.md , https://github.com/the-hideout/tarkov-dev-svg-maps/blob/5a8b6115d1c0cf56f2ebaac1a96fa5ae3074d178/README.md

로그·레이드 데이터 모델
- SPT 보험 메시지·메시지 종류 @8f1d50f: https://github.com/sp-tarkov/server/blob/8f1d50fb7ccf35d696a47b587448b9b79f7e8027/project/src/services/InsuranceService.ts#L70-L133 , https://github.com/sp-tarkov/server/blob/8f1d50fb7ccf35d696a47b587448b9b79f7e8027/project/src/models/enums/MessageType.ts
- SPT `EndLocalRaidRequestData`(결과·탈출구·playTime) @fe74b07: https://github.com/sp-tarkov/server-csharp/blob/fe74b07c6361e2c6d1532dc21ba1c3b981d88d93/Libraries/SPTarkov.Server.Core/Models/Eft/Match/EndLocalRaidRequestData.cs#L47-L71

레이드 후 화면·OCR
- SPT hollowed EFT 0.16.9 어셈블리(화면 클래스, `VictimStats`) @8dea32a: https://github.com/sp-tarkov/modules/blob/8dea32ab780eb32eecb444e5f6d318309651b241/project/Shared/Hollowed/hollowed.dll
- kr 로캘 @fe74b07: https://github.com/sp-tarkov/server-csharp/blob/fe74b07c6361e2c6d1532dc21ba1c3b981d88d93/Libraries/SPTarkov.Server.Assets/SPT_Data/database/locales/global/kr.json
- 실제 2560×1440 킬 목록과 Tesseract 실험: http://jonathanmh.com/p/ocr-in-go-for-eft-screenshots-part-1/
- Tarkov Stammtisch 킬 카운터: https://tarkov-stammtisch.de/news/world-s-first-escape-from-tarkov-kill-counter
- RapidOCR 기본 모델 목록(PP-OCRv5 korean/eslav/en): https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml

선행 사례·도구·약관
- Raid Review(Chazut 포크) @2567b37: https://github.com/Chazut/SPT-RaidReview/tree/2567b3729437cf71248bcec607bdfb8f1393828e
- Raid Review 원본(보관) @6e5b9a6: https://github.com/ekky-llc/spt-raid-review/tree/6e5b9a6f496b3ca4cfda998a3870592043d3fe97
- Tarkov Map Companion @13ea5c3: https://github.com/Battleroid/tarkov-map-companion/blob/13ea5c3a8836f08d7f8f888b376f6e6c50e3918e/README.md
- TarkovMonitor 자동 스크린샷 PR #152(병합 안 됨): https://github.com/the-hideout/TarkovMonitor/pull/152
- EFT License Agreement: https://www.escapefromtarkov.com/legals/license_agreement
- EFT Rules of the Game: https://www.escapefromtarkov.com/site/api/v1/legals/type/rules_of_the_game/en
- BattlEye FAQ: https://www.battleye.com/support/faq/
- DWM_lut 차단(2023-02): https://insider-gaming.com/tarkov-devs-block-filters/
