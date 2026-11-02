# 제3자 고지

## tarkov.dev

`eftjourney/data/maps.json`의 맵 변환값·층·지명과, 뷰어(`eftjourney/viewer/app.js`)의 좌표 변환·층 판정은
[the-hideout/tarkov-dev](https://github.com/the-hideout/tarkov-dev)의 데이터와 코드를 옮긴 것입니다.
원래의 라이선스는 다음과 같습니다.

```
MIT License

Copyright (c) 2019 Oskar Risberg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 실행할 때 불러오는 것 (저장소에는 들어 있지 않음)

- [Leaflet](https://leafletjs.com): 뷰어가 unpkg 또는 jsDelivr에서 불러옵니다. BSD-2-Clause.
- 지도 그림(SVG): Shebuka 외 [tarkov-dev-svg-maps](https://github.com/the-hideout/tarkov-dev-svg-maps) 기여자, CC BY-NC-SA 4.0.
  처음 쓸 때 받아 이 PC에만 캐시합니다.
- 지도 타일: tarkov.dev가 제공하는 이미지입니다. 브라우저가 불러오며, 공개된 라이선스가 없어 저장소에 넣지 않습니다.
- 탈출구·트랜짓·스폰과 한국어 이름: json.tarkov.dev API. `scripts/update_maps.py`가 이 데이터로 `maps.json`을 만듭니다.

## 게임

Escape from Tarkov와 게임 속 이름·그림은 Battlestate Games의 상표이자 저작물입니다.
MY EFT JOURNEY는 Battlestate Games와 관계없는 비공식 팬 도구입니다.
