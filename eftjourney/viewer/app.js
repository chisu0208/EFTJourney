'use strict';
/* MY EFT JOURNEY viewer - plain JS + Leaflet 1.9 (no build step). */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const SVG_NS = 'http://www.w3.org/2000/svg';

  const GAP_SEC = 120; // longer gaps between screenshots are drawn dashed: the straight line is a guess
  const OUTCOME_LABEL = { survived: '생존', killed: '사망', mia: 'MIA', runthrough: '런스루', transit: '트랜짓', left: '나감', unknown: '결과 미상' };
  const SIDE_LABEL = { '': '진영', pmc: 'PMC', usec: 'USEC', bear: 'BEAR', scav: '스캐브', player_scav: '플레이어 스캐브', raider: '레이더', rogue: '로그', boss: '보스', follower: '보스 호위', cultist: '컬티스트', other: '기타' };
  const PART_LABEL = { '': '부위', head: '머리', thorax: '흉부', stomach: '복부', leftarm: '왼팔', rightarm: '오른팔', leftleg: '왼다리', rightleg: '오른다리' };
  const FACTION_LABEL = { pmc: 'PMC 탈출', scav: '스캐브 탈출', shared: '공용 탈출', transit: '트랜짓' };
  const FIX_LABEL = { Stuck: '끼임 보정', PacketsQueue: '패킷 지연 보정', Speed: '속도 보정', Lift: '승강기' };
  const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];

  const prefs = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem(`eftjourney:${key}`);
        return v === null ? fallback : JSON.parse(v);
      } catch (_) {
        return fallback;
      }
    },
    set(key, value) {
      try { localStorage.setItem(`eftjourney:${key}`, JSON.stringify(value)); } catch (_) { /* private mode */ }
    },
  };

  const state = {
    maps: [],
    attribution: {},
    raids: [],
    raid: null,
    track: [],
    mapEntry: null,
    rotated: false,
    t: 0,
    tEnd: 0,
    playing: false,
    speed: 10,
    lastFrame: 0,
    statusVersion: null,
    status: {},
    layersOn: Object.assign({ exfils: true, spawns: false, samples: true, kills: true, labels: true, fixes: true }, prefs.get('layers', {})),
    level: '',
    autoLevel: true,
    follow: false,
  };

  // ------------------------------------------------------------------ format
  const pad = (n) => String(n).padStart(2, '0');
  function fmtClock(sec) {
    if (sec == null || !isFinite(sec)) return '--:--';
    const s = Math.max(0, Math.round(sec));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
  }
  function parseClock(text) {
    // seconds, m:ss or h:mm:ss (the in-game kill list's TIME); only the first part may pass 59
    const v = String(text ?? '').trim();
    if (!/^\d+(:[0-5]?\d){0,2}(\.\d+)?$/.test(v)) return null;
    return v.split(':').reduce((acc, p) => acc * 60 + Number(p), 0);
  }
  const fmtTod = (h) => `${pad(Math.floor(h))}:${pad(Math.floor((h % 1) * 60))}`;
  const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)}km` : `${Math.round(m)}m`);
  function fmtDate(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return `${d.getMonth() + 1}/${d.getDate()} (${WEEKDAY[d.getDay()]}) ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function dayKey(iso) {
    const d = new Date(iso);
    return isNaN(d) ? '날짜 미상' : `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAY[d.getDay()]})`;
  }
  function wallClock(t) {
    const start = Date.parse(state.raid?.startedAt || '');
    if (!isFinite(start) || t == null) return '';
    const d = new Date(start + t * 1000);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  function mapEntryFor(id) {
    if (!id) return null;
    const key = String(id).toLowerCase();
    return state.maps.find((m) => (m.ids || []).includes(key)) || null;
  }
  function mapLabel(id) {
    const e = mapEntryFor(id);
    if (!e) return { ko: id ? `알 수 없는 맵 (${id})` : '맵 미상', en: '' };
    const v = (e.variants || {})[String(id).toLowerCase()] || e;
    const ko = v.nameKo || v.name;
    return { ko, en: v.name && v.name !== ko ? v.name : '' };
  }

  function toast(msg, ms = 2600) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    $('#main').appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  async function getJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      let msg = `${res.status}`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* not json */ }
      throw new Error(msg);
    }
    return res.json();
  }

  // ------------------------------------------------- coordinates (tarkov.dev math)
  // Game position {x, y(up), z} -> LatLng(z, x); the CRS then rotates by the map's
  // coordinateRotation and applies transform [scaleX, marginX, scaleY, marginY].
  function rotate(x, y, deg) {
    if (!deg) return [x, y];
    const a = (deg * Math.PI) / 180;
    const c = Math.cos(a);
    const s = Math.sin(a);
    return [x * c - y * s, x * s + y * c];
  }
  function makeCRS(entry, rotated) {
    const [sx, mx, sy, my] = entry.transform || [1, 0, 1, 0];
    const rot = (entry.coordinateRotation || 0) + (rotated ? 180 : 0);
    return L.extend({}, L.CRS.Simple, {
      projection: {
        project(latlng) {
          const [x, y] = rotate(latlng.lng, latlng.lat, rot);
          return L.point(x, y);
        },
        unproject(point) {
          const [x, y] = rotate(point.x, point.y, -rot);
          return L.latLng(y, x);
        },
        bounds: L.bounds([-1e6, -1e6], [1e6, 1e6]),
      },
      transformation: new L.Transformation(sx, mx, -sy, my),
    });
  }
  const toLatLng = (p) => L.latLng(p.z, p.x);
  const boundsOf = (b) => L.latLngBounds([b[0][1], b[0][0]], [b[1][1], b[1][0]]); // [[x1, z1], [x2, z2]]
  const offset = (p, yawDeg, dist) => {
    const a = (yawDeg * Math.PI) / 180; // yaw: 0 = +Z, 90 = +X (Unity, seen from above)
    return { x: p.x + Math.sin(a) * dist, y: p.y, z: p.z + Math.cos(a) * dist };
  };
  function inRect(rect, p) {
    const [[x1, z1], [x2, z2]] = rect;
    return p.x >= Math.min(x1, x2) && p.x <= Math.max(x1, x2) && p.z >= Math.min(z1, z2) && p.z <= Math.max(z1, z2);
  }

  // ------------------------------------------------------------- floors (levels)
  const canRotate = (entry) => Boolean(entry && entry.svgUrl);
  const defaultLevel = (entry) => ((entry?.layers || []).find((l) => l.show) || {}).name || ''; // first view, before any position
  const usableLayers = (entry) => (entry?.layers || []).filter((l) => l.svgLayer || l.tileUrl);
  const currentLayer = () => usableLayers(state.mapEntry).find((l) => l.name === state.level) || null;

  function levelFor(p) {
    // same test as tarkov.dev: min <= y < max, inside one of the extent's rectangles if it has any
    if (!Number.isFinite(p.y)) return null; // height unknown (a raid file edited by hand): keep the floor shown
    for (const layer of usableLayers(state.mapEntry)) {
      for (const ext of layer.extents || []) {
        if (p.y >= ext.height[0] && p.y < ext.height[1] && (!ext.bounds || ext.bounds.some((b) => inRect(b, p)))) return layer.name;
      }
    }
    return ''; // outside every floor: the base map alone (outdoors, Interchange's garage)
  }
  function onActiveLevel(p, top, bottom) {
    const layer = currentLayer();
    // tarkov.dev's 'full' rule: a label lying wholly inside a floor's rectangle and heights is that floor's, not the ground's
    const ownFloor = (ext) => ext.bounds && bottom >= ext.height[0] && top <= ext.height[1] && ext.bounds.some((b) => inRect(b, p));
    if (!layer && usableLayers(state.mapEntry).some((l) => (l.extents || []).some(ownFloor))) return false;
    const extents = layer ? layer.extents || [] : [{ height: state.mapEntry?.heightRange || [-1e9, 1e9] }];
    return extents.some((ext) => top >= ext.height[0] && bottom < ext.height[1] && (!ext.bounds || ext.bounds.some((b) => inRect(b, p))));
  }

  // ------------------------------------------------------------- map lifecycle
  let lmap = null;
  let baseLayer = null;
  let floorTiles = null;
  let svgRoot = null;
  let svgInner = null;
  let mapToken = 0;
  let staticMapId = ''; // game map id the extract/transit/spawn markers were built for (variants differ)
  let staticEntry = null; // and the map entry their lists came from
  const groups = {};

  function tileOptions(entry, className) {
    return {
      tileSize: entry.tileSize || 256,
      bounds: boundsOf(entry.bounds),
      minZoom: -2,
      maxZoom: 8,
      minNativeZoom: 0,
      maxNativeZoom: entry.maxZoom || 6,
      noWrap: true,
      className,
    };
  }

  function buildSvg(text, entry, rotated) {
    const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
    const inner = document.importNode(doc.documentElement, true);
    let viewBox = inner.getAttribute('viewBox');
    if (!viewBox) viewBox = `0 0 ${parseFloat(inner.getAttribute('width')) || 1000} ${parseFloat(inner.getAttribute('height')) || 1000}`;
    // like tarkov.dev: the base group stays, every other top-level group is a floor shown on demand
    for (const g of Array.from(inner.children)) {
      if (g.tagName.toLowerCase() !== 'g' || !g.id) continue;
      if (g.id === entry.svgLayer || g.getAttribute('data-keep-with-group') === entry.svgLayer) g.classList.add('base-layer');
      else g.classList.add('overlay-layer', 'hidden-layer');
    }
    const outer = document.createElementNS(SVG_NS, 'svg');
    outer.setAttribute('viewBox', viewBox);
    if (rotated) {
      // the CRS turns by 180 deg, so the drawing turns about its own centre to stay aligned
      const [x, y, w, h] = viewBox.split(/[\s,]+/).map(Number);
      const wrap = document.createElementNS(SVG_NS, 'g');
      wrap.setAttribute('transform', `rotate(180 ${x + w / 2} ${y + h / 2})`);
      wrap.appendChild(inner);
      outer.appendChild(wrap);
    } else {
      outer.appendChild(inner);
    }
    svgRoot = outer;
    svgInner = inner;
    return outer;
  }

  function attributionHtml(entry) {
    const data = '지도 데이터 <a href="https://tarkov.dev" target="_blank" rel="noopener">tarkov.dev</a>';
    const game = 'Escape from Tarkov © Battlestate Games · 비공식 도구';
    if (entry.svgUrl) {
      const link = state.attribution.svgLicense || 'https://github.com/the-hideout/tarkov-dev-svg-maps';
      return `${data} · 지도 그림 <a href="${esc(link)}" target="_blank" rel="noopener">${esc(entry.author || 'Shebuka')} 외, CC BY-NC-SA 4.0</a> · ${game}`;
    }
    return `${entry.author ? `${data} · 타일 ${esc(entry.author)}` : data} · ${game}`;
  }

  function destroyMap() {
    mapToken++; // a map image still downloading must not land on whatever comes next
    if (lmap) {
      const old = lmap;
      const container = old.getContainer();
      old._animatingZoom = false; // a zoom animation (fitRaid) would otherwise finish 250 ms later on the removed map and throw
      old.remove();
      // Leaflet 1.9.4 never removes the 'scroll' listener _initContainer put on the container,
      // and #map is reused, so every removed map would stay alive through it
      L.DomEvent.off(container, 'scroll', old._onScroll, old);
    }
    lmap = null;
    baseLayer = floorTiles = svgRoot = svgInner = null;
    state.mapEntry = null;
    resetRaidLayers();
    // the layers card acts on this map: gone with it, the next map draws its own once its overlays are built
    const card = $('#layers');
    card.classList.add('hidden');
    card.innerHTML = '';
  }

  async function ensureMap(entry, force = false) {
    const rotated = canRotate(entry) && Boolean(prefs.get(`rotate:${entry.key}`, false));
    if (!force && lmap && state.mapEntry && state.mapEntry.key === entry.key && state.rotated === rotated) return true;
    destroyMap();
    const token = ++mapToken;
    const stale = () => token !== mapToken || !lmap; // another raid replaced or dropped this map meanwhile
    state.mapEntry = entry;
    state.rotated = rotated;
    state.level = defaultLevel(entry);
    state.autoLevel = true;
    lmap = L.map('map', { crs: makeCRS(entry, rotated), zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 90, minZoom: -2, maxZoom: 8, zoomControl: false });
    L.control.zoom({ position: 'bottomleft', zoomInTitle: '확대', zoomOutTitle: '축소' }).addTo(lmap);
    lmap.attributionControl.setPrefix(false);
    lmap.attributionControl.addAttribution(attributionHtml(entry));
    const bounds = boundsOf(entry.bounds);
    lmap.fitBounds(bounds);
    for (const name of ['static', 'labels', 'route', 'fixes', 'samples', 'kills', 'marks', 'hover']) groups[name] = L.layerGroup().addTo(lmap);
    buildStaticOverlays(entry);
    buildLabels(entry);
    lmap.on('zoomend', updateNowMarker);
    if (entry.svgUrl) {
      try {
        const res = await fetch(`/mapsvg/${encodeURIComponent(entry.key)}.svg`);
        if (stale()) return false;
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.status);
        const text = await res.text();
        if (stale()) return false;
        const svg = buildSvg(text, entry, rotated);
        baseLayer = L.svgOverlay(svg, entry.svgBounds ? boundsOf(entry.svgBounds) : bounds, { interactive: false, className: 'map-svg' }).addTo(lmap);
        baseLayer.bringToBack();
      } catch (err) {
        if (stale()) return false; // the failure belongs to a map nobody looks at any more
        toast(`지도 이미지를 불러오지 못했습니다: ${err.message}`, 5000);
      }
    } else if (entry.tileUrl) {
      baseLayer = L.tileLayer(entry.tileUrl, tileOptions(entry, 'map-tiles')).addTo(lmap);
    }
    if (stale()) return false;
    applyLevel();
    renderLayersCard();
    return true;
  }

  function applyLevel() {
    if (!state.mapEntry || !lmap) return;
    const layer = currentLayer();
    const svgFloor = Boolean(layer && layer.svgLayer && svgInner);
    if (svgInner) {
      for (const g of Array.from(svgInner.children)) {
        if (g.classList.contains('overlay-layer')) g.classList.toggle('hidden-layer', !(svgFloor && g.id === layer.svgLayer));
      }
    }
    if (floorTiles) {
      floorTiles.remove();
      floorTiles = null;
    }
    // tiles cannot turn with the 180-degree view like the SVG does: they would land mirrored through game (0, 0)
    if (layer && !svgFloor && layer.tileUrl && !state.rotated) floorTiles = L.tileLayer(layer.tileUrl, tileOptions(state.mapEntry, 'map-tiles floor')).addTo(lmap);
    const dim = Boolean(layer && !layer.show && (svgFloor || floorTiles)); // the selected floor stands out, the ground fades
    if (svgRoot) svgRoot.classList.toggle('off-level', dim);
    else if (baseLayer && baseLayer.getContainer) baseLayer.getContainer()?.classList.toggle('off-level', dim);
    updateLabelLevels();
    syncLevelControls();
  }

  function syncLevelControls() {
    const select = $('#level');
    if (select) select.value = state.autoLevel ? '__auto' : state.level;
    const layer = currentLayer();
    const note = $('#level-note');
    if (note) note.classList.toggle('hidden', !(state.rotated && layer && layer.tileUrl && !(layer.svgLayer && svgInner)));
  }

  function pinIcon(cls) {
    return L.divIcon({ className: '', html: `<div class="${cls}"></div>`, iconSize: [14, 14], iconAnchor: [7, 14] });
  }

  function buildStaticOverlays(entry) {
    // alternate locations (night Factory, Labs dark, Ground Zero 21+) may bring their own marker lists
    staticEntry = entry;
    staticMapId = String(state.raid?.map || '').toLowerCase();
    const variant = (entry.variants || {})[staticMapId] || {};
    const list = (kind) => (Array.isArray(variant[kind]) ? variant[kind] : entry[kind]) || [];
    groups.static.clearLayers();
    if (state.layersOn.exfils) {
      for (const ex of list('extracts')) {
        const faction = ['pmc', 'scav', 'shared'].includes(ex.faction) ? ex.faction : 'shared';
        L.marker(toLatLng(ex), { icon: pinIcon(`mk-exfil ${faction}`), keyboard: false })
          .bindTooltip(`<div class="t">${esc(ex.nameKo || ex.name)}</div><div class="sub">${FACTION_LABEL[faction]}</div>`, { className: 'tip', direction: 'top', offset: [0, -14] })
          .addTo(groups.static);
      }
      for (const tr of list('transits')) {
        L.marker(toLatLng(tr), { icon: pinIcon('mk-exfil transit'), keyboard: false })
          .bindTooltip(`<div class="t">${esc(tr.nameKo || tr.name || '트랜짓')}</div><div class="sub">트랜짓</div>`, { className: 'tip', direction: 'top', offset: [0, -14] })
          .addTo(groups.static);
      }
    }
    if (state.layersOn.spawns) {
      for (const sp of list('spawns')) {
        L.marker(toLatLng(sp), {
          icon: L.divIcon({ className: '', html: '<div class="mk-spawn"></div>', iconSize: [8, 8], iconAnchor: [4, 4] }),
          keyboard: false,
          interactive: false,
        }).addTo(groups.static);
      }
    }
  }

  function buildLabels(entry) {
    groups.labels.clearLayers();
    if (!state.layersOn.labels) return;
    for (const lab of entry.labels || []) {
      const m = L.marker(toLatLng(lab), {
        icon: L.divIcon({
          className: 'map-label',
          html: `<div class="label" style="font-size:${Number(lab.size) || 100}%;transform:translate(-50%,-50%) rotate(${Number(lab.rotation) || 0}deg)">${esc(lab.text)}</div>`,
          iconSize: [0, 0],
        }),
        interactive: false,
        keyboard: false,
        zIndexOffset: -100000,
      });
      m.lab = lab;
      m.addTo(groups.labels);
    }
    updateLabelLevels();
  }

  function updateLabelLevels() {
    if (!groups.labels) return;
    groups.labels.eachLayer((m) => {
      const el = m.getElement();
      if (!el || !m.lab) return;
      const lab = m.lab;
      el.style.display = onActiveLevel({ x: lab.x, z: lab.z }, lab.top ?? 1000, lab.bottom ?? -1000) ? '' : 'none';
    });
  }

  function renderLayersCard() {
    const card = $('#layers');
    const entry = state.mapEntry;
    if (!entry || !$('#editor').classList.contains('hidden')) {
      card.classList.add('hidden');
      return;
    }
    const levels = usableLayers(entry);
    const box = (key, color, label) =>
      `<label class="chk"><input type="checkbox" data-layer="${key}" ${state.layersOn[key] ? 'checked' : ''}><span class="swatch" style="background:${color}"></span>${label}</label>`;
    card.innerHTML = `
      <div class="title">표시</div>
      ${box('exfils', 'var(--pmc)', '탈출구')}
      ${box('labels', '#d8c27c', '지명')}
      ${box('spawns', '#45d16e', 'PMC 스폰')}
      ${box('samples', '#fff', '기록 지점')}
      ${box('kills', 'var(--kill)', '킬')}
      ${(state.raid?.logFixes || []).length ? box('fixes', '#9aa1a9', '서버 위치 기록') : ''}
      ${levels.length ? `<div class="title">층</div><select id="level">
          <option value="__auto">자동 (현재 위치)</option>
          <option value="">지상</option>
          ${levels.map((l) => `<option value="${esc(l.name)}">${esc(l.nameKo || l.name)}</option>`).join('')}
        </select><div class="note hidden" id="level-note">회전한 지도에서는 이 층 그림을 표시할 수 없습니다.</div>` : ''}
      ${canRotate(entry) ? `<label class="chk"><input type="checkbox" id="rotate" ${state.rotated ? 'checked' : ''}>지도 180° 회전</label>` : ''}`;
    card.classList.remove('hidden');
    card.querySelectorAll('input[data-layer]').forEach((input) =>
      input.addEventListener('change', () => {
        state.layersOn[input.dataset.layer] = input.checked;
        prefs.set('layers', state.layersOn);
        const key = input.dataset.layer;
        // from the map on screen, never from whatever map this card was drawn for
        if (key === 'exfils' || key === 'spawns') buildStaticOverlays(state.mapEntry);
        else if (key === 'labels') buildLabels(state.mapEntry);
        else renderRaidLayers();
      }),
    );
    const level = $('#level', card);
    if (level) {
      syncLevelControls();
      level.addEventListener('change', () => {
        state.autoLevel = level.value === '__auto';
        state.level = state.autoLevel ? (positionAt(state.t) ? levelFor(positionAt(state.t)) ?? state.level : defaultLevel(state.mapEntry)) : level.value;
        applyLevel();
      });
    }
    const rot = $('#rotate', card);
    if (rot) {
      rot.addEventListener('change', async () => {
        prefs.set(`rotate:${state.mapEntry.key}`, rot.checked);
        // with another raid on its way, reloading this one would cancel it; that raid's map reads the setting itself
        if (state.raid && requestedId === state.raid.id) await selectRaid(state.raid.id, { keepTime: true, forceMap: true });
      });
    }
  }

  // ---------------------------------------------------------------- track math
  function segIndex(t) {
    // last i with track[i].t <= t
    const tr = state.track;
    let lo = 0;
    let hi = tr.length - 1;
    if (!tr.length || t < tr[0].t) return -1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (tr[mid].t <= t) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }
  function positionAt(t) {
    const tr = state.track;
    if (!tr.length) return null;
    const i = segIndex(t);
    if (i < 0) return { ...tr[0], i: 0, frac: 0, before: true };
    if (i >= tr.length - 1) return { ...tr[tr.length - 1], i: tr.length - 1, frac: 0 };
    const a = tr[i];
    const b = tr[i + 1];
    const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
    // null is no height, not 0 m; at a point itself (f 0: a click on it, arrow keys) its own height counts
    const y = f === 0 ? (a.y ?? null) : a.y != null && b.y != null ? a.y + (b.y - a.y) * f : null;
    return { x: a.x + (b.x - a.x) * f, y, z: a.z + (b.z - a.z) * f, yaw: a.yaw, i, frac: f };
  }
  function travelYaw(i) {
    const tr = state.track;
    const a = tr[i];
    const b = tr[Math.min(i + 1, tr.length - 1)];
    if (a && b && Math.hypot(b.x - a.x, b.z - a.z) > 0.5) return (Math.atan2(b.x - a.x, b.z - a.z) * 180) / Math.PI;
    return a?.yaw ?? 0;
  }

  // -------------------------------------------------------------- raid layers
  let progressLine = null;
  let progressCasing = null;
  let nowMarker = null;
  const killMarkers = [];
  let deathMarker = null;
  let deathT = null;

  function viewCone(p, yaw, len = 28, half = 30) {
    const pts = [toLatLng(p)];
    for (let a = -half; a <= half; a += 6) pts.push(toLatLng(offset(p, yaw + a, len)));
    return L.polygon(pts, { color: css('--route'), weight: 1, fillOpacity: 0.22, opacity: 0.6, interactive: false });
  }
  function ring(p, radius) {
    const pts = [];
    for (let a = 0; a < 360; a += 6) pts.push(toLatLng(offset(p, a, radius)));
    return L.polygon(pts, { color: css('--kill'), weight: 1.5, dashArray: '6 6', fill: false, interactive: false });
  }

  // the builder keeps a point whose screenshot file was deleted later ("gone"); there is nothing to fetch for it
  const GONE_NOTE = '스크린샷 파일이 삭제되어 미리보기가 없습니다';
  // a raid file edited by hand can lack a height or heading: the server sends null for those
  const metres = (v) => (v != null ? `${v.toFixed(1)}m` : '?');

  function samplePopup(s, idx) {
    const img = s.gone
      ? `<div class="muted">${GONE_NOTE}.</div>`
      : s.shot ? `<img src="/thumbs/${encodeURIComponent(s.shot)}" data-full="/shots/${encodeURIComponent(s.shot)}" alt="스크린샷" class="shot">` : '';
    return `<div class="pop"><h4>기록 #${idx + 1} · ${fmtClock(s.t)}</h4><dl>
      <dt>시각</dt><dd class="num">${wallClock(s.t)}${s.approx ? ' <span class="muted">(분 단위)</span>' : ''}</dd>
      ${s.tod != null ? `<dt>게임 내 시각</dt><dd class="num">${fmtTod(s.tod)}</dd>` : ''}
      <dt>좌표</dt><dd class="num">x ${s.x.toFixed(1)} · z ${s.z.toFixed(1)}</dd>
      <dt>높이</dt><dd class="num">${metres(s.y)}</dd>
      <dt>방향</dt><dd class="num">${s.yaw != null ? `${Math.round(((s.yaw % 360) + 360) % 360)}°` : '?'}</dd></dl>${img}</div>`;
  }

  function killPopup(k, idx) {
    const rows = [
      ['시각', `${fmtClock(k.t)} <span class="muted">${wallClock(k.t)}</span>`],
      ['진영', SIDE_LABEL[k.side] && k.side ? SIDE_LABEL[k.side] : ''],
      ['무기', esc(k.weapon)],
      ['부위', k.bodyPart ? PART_LABEL[k.bodyPart] : ''],
      ['거리', k.distance != null ? `${Number(k.distance).toFixed(1)}m` : ''],
    ].filter(([, v]) => v);
    return `<div class="pop"><h4><span class="badge killed">킬 ${idx + 1}</span>${esc(k.name || '이름 미상')}${k.level ? ` <small class="muted">Lv.${k.level}</small>` : ''}</h4>
      <dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl>
      <div class="muted">점은 그 시각에 내가 있던 위치(기록 사이 보간)입니다.${k.distance != null ? ' 점선 원은 상대까지의 거리입니다.' : ''}</div>${k.note ? `<div class="muted">${esc(k.note)}</div>` : ''}</div>`;
  }

  function deathPopup(o) {
    const rows = [
      ['시각', o.t != null ? `${fmtClock(o.t)} <span class="muted">${wallClock(o.t)}</span>` : ''],
      ['상대', esc(o.killer)],
      ['진영', o.killerSide ? SIDE_LABEL[o.killerSide] : ''],
      ['무기', esc(o.weapon)],
      ['부위', o.bodyPart ? PART_LABEL[o.bodyPart] : ''],
      ['거리', o.distance != null ? `${Number(o.distance).toFixed(1)}m` : ''],
    ].filter(([, v]) => v);
    return `<div class="pop"><h4><span class="badge killed">사망</span>${esc(o.killer ? `${o.killer}에게 사망` : '사망 지점')}</h4>
      ${rows.length ? `<dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl>` : ''}
      <div class="muted">${o.t != null ? '사망 시각 위치(보간)' : '마지막 기록 위치'} 기준 추정입니다.</div></div>`;
  }

  const DEATH_SVG = '<svg width="34" height="34" viewBox="0 0 34 34"><path d="M8 8L26 26M26 8L8 26" stroke="#000" stroke-width="9" stroke-linecap="round"/><path d="M8 8L26 26M26 8L8 26" stroke="#ff2e2e" stroke-width="5.5" stroke-linecap="round"/></svg>';
  const NOW_HTML = '<div class="mk-now"><svg class="arrow" viewBox="0 0 26 26"><path d="M24 13 L15 8 L15 18 Z" fill="#2f8cff" stroke="#fff" stroke-width="1.2"/></svg><div class="core"></div></div>';

  function deathTime() {
    // at the entered death time, else at the last record
    const o = state.raid?.outcome || {};
    if (o.status !== 'killed') return null;
    if (o.t != null) return o.t;
    return state.track.length ? state.track[state.track.length - 1].t : null;
  }

  // forget the previous raid's markers (also when the map goes away); the death time is the current raid's
  function resetRaidLayers() {
    for (const name of ['route', 'fixes', 'samples', 'kills', 'marks', 'hover']) groups[name]?.clearLayers();
    killMarkers.length = 0;
    progressLine = progressCasing = nowMarker = deathMarker = null;
    deathT = deathTime();
  }

  function renderRaidLayers() {
    if (!lmap) return;
    resetRaidLayers();
    const raid = state.raid;
    const tr = state.track;
    const route = css('--route');

    if (tr.length > 1) {
      // faint full route: solid where screenshots are close together, dashed across long gaps
      let run = [toLatLng(tr[0])];
      const flush = () => {
        if (run.length > 1) {
          L.polyline(run, { color: '#0b1220', weight: 7, opacity: 0.35, interactive: false }).addTo(groups.route);
          L.polyline(run, { color: route, weight: 4, opacity: 0.35, interactive: false }).addTo(groups.route);
        }
      };
      for (let i = 1; i < tr.length; i++) {
        if (tr[i].t - tr[i - 1].t > GAP_SEC) {
          flush();
          L.polyline([toLatLng(tr[i - 1]), toLatLng(tr[i])], { color: route, weight: 3, opacity: 0.45, dashArray: '2 9', interactive: false }).addTo(groups.route);
          run = [toLatLng(tr[i])];
        } else {
          run.push(toLatLng(tr[i]));
        }
      }
      flush();
      progressCasing = L.polyline([], { color: '#0b1220', weight: 8, opacity: 0.55, interactive: false }).addTo(groups.route);
      progressLine = L.polyline([], { color: route, weight: 4.5, opacity: 1, interactive: false }).addTo(groups.route);
    }

    if (state.layersOn.fixes && (raid.logFixes || []).length) {
      const fixes = [...raid.logFixes].sort((a, b) => a.t - b.t);
      if (fixes.length > 1) {
        L.polyline(fixes.map(toLatLng), { color: '#cfd4da', weight: 2, opacity: 0.55, dashArray: '1 7', lineCap: 'round', interactive: false }).addTo(groups.fixes);
      }
      for (const f of fixes) {
        L.circleMarker(toLatLng(f), { radius: 4.5, color: '#15171a', weight: 2, fillColor: '#cfd4da', fillOpacity: 0.95 })
          .bindTooltip(`<div class="t">서버 위치 기록 · ${fmtClock(f.t)}</div><div class="sub">${esc(FIX_LABEL[f.reason] || f.reason)} · 온라인 레이드에서 게임이 남긴 좌표(참고용)</div>`, { className: 'tip', direction: 'top', offset: [0, -4] })
          .addTo(groups.fixes);
      }
    }

    if (tr.length) {
      L.marker(toLatLng(tr[0]), {
        icon: L.divIcon({ className: '', html: '<div class="mk-start"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }),
        keyboard: false,
      })
        .bindTooltip(`<div class="t">첫 기록 · ${fmtClock(tr[0].t)}</div><div class="sub">${wallClock(tr[0].t)}</div>`, { className: 'tip', direction: 'top', offset: [0, -10] })
        .addTo(groups.marks);
    }

    if (state.layersOn.samples) {
      tr.forEach((s, idx) => {
        const m = L.circleMarker(toLatLng(s), { radius: 4.5, color: route, weight: 2, fillColor: '#fff', fillOpacity: 1 });
        m.bindTooltip(
          () => `<div class="t">${fmtClock(s.t)} <span class="sub">${wallClock(s.t)}</span></div><div class="sub">높이 ${metres(s.y)} · 클릭하면 자세히</div>${s.gone ? `<div class="sub">${GONE_NOTE}</div>` : s.shot ? `<img src="/thumbs/${encodeURIComponent(s.shot)}" alt="">` : ''}`,
          { className: 'tip', direction: 'top', offset: [0, -6] },
        );
        m.bindPopup(() => samplePopup(s, idx), { maxWidth: 360 });
        m.on('mouseover', () => { groups.hover.clearLayers(); if (s.yaw != null) viewCone(s, s.yaw).addTo(groups.hover); });
        m.on('mouseout', () => groups.hover.clearLayers());
        m.on('click', () => setTime(s.t, true));
        m.addTo(groups.samples);
      });
    }

    const kills = raid.kills || [];
    if (tr.length && state.layersOn.kills) {
      kills.forEach((k, idx) => {
        const p = positionAt(k.t);
        const marker = L.marker(toLatLng(p), {
          icon: L.divIcon({ className: '', html: `<div class="mk-kill">${idx + 1}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }),
          riseOnHover: true,
          zIndexOffset: 500,
        });
        marker.bindTooltip(`<div class="t">${esc(k.name || '이름 미상')}</div><div class="sub">${fmtClock(k.t)}${k.weapon ? ` · ${esc(k.weapon)}` : ''}</div>`, { className: 'tip', direction: 'top', offset: [0, -12] });
        marker.bindPopup(() => killPopup(k, idx), { maxWidth: 340 });
        marker.on('popupopen', () => {
          groups.hover.clearLayers();
          if (k.distance) ring(p, Number(k.distance)).addTo(groups.hover);
        });
        marker.on('popupclose', () => groups.hover.clearLayers());
        marker.addTo(groups.kills);
        killMarkers.push({ marker, t: k.t });
      });
    }

    const outcome = raid.outcome || {};
    if (tr.length && deathT != null) {
      const p = outcome.t != null ? positionAt(outcome.t) : tr[tr.length - 1];
      deathMarker = L.marker(toLatLng(p), {
        icon: L.divIcon({ className: 'mk-death', html: DEATH_SVG, iconSize: [34, 34], iconAnchor: [17, 17] }),
        zIndexOffset: 1000,
      }).bindPopup(() => deathPopup(outcome), { maxWidth: 340 });
      deathMarker.addTo(groups.marks);
    }

    if (tr.length) {
      nowMarker = L.marker(toLatLng(tr[0]), {
        icon: L.divIcon({ className: '', html: NOW_HTML, iconSize: [26, 26], iconAnchor: [13, 13] }),
        interactive: false,
        keyboard: false,
        zIndexOffset: 2000,
      }).addTo(groups.marks);
    }
    setTime(state.t, true);
  }

  function updateNowMarker() {
    if (!nowMarker || !lmap) return;
    const p = positionAt(state.t);
    if (!p) return;
    const ll = toLatLng(p);
    nowMarker.setLatLng(ll);
    const yaw = travelYaw(p.i);
    const a = lmap.latLngToLayerPoint(ll);
    const b = lmap.latLngToLayerPoint(toLatLng(offset(p, yaw, 10)));
    const deg = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
    const el = nowMarker.getElement();
    const arrow = el && el.querySelector('.arrow');
    if (arrow) arrow.style.transform = `rotate(${deg}deg)`;
    if (el) el.style.opacity = p.before ? '0.45' : '1';
  }

  let scrubbing = false; // the timeline thumb is held by the pointer: leave it where the user puts it

  function setTime(t, force = false) {
    const nt = Math.min(Math.max(t, 0), state.tEnd || 0);
    if (!force && nt === state.t && !state.playing) return;
    state.t = nt;
    const tr = state.track;
    if (progressLine) {
      const i = segIndex(nt);
      const pts = i < 0 ? [] : tr.slice(0, i + 1).map(toLatLng);
      if (i >= 0 && i < tr.length - 1) pts.push(toLatLng(positionAt(nt)));
      progressLine.setLatLngs(pts);
      progressCasing.setLatLngs(pts);
    }
    updateNowMarker();
    const p = positionAt(nt);
    if (state.autoLevel && usableLayers(state.mapEntry).length) {
      const lvl = p ? levelFor(p) : defaultLevel(state.mapEntry); // no positions at all: the map's first view
      if (lvl != null && lvl !== state.level) {
        state.level = lvl;
        applyLevel();
      }
    }
    if (p && state.follow && lmap) lmap.panTo(toLatLng(p), { animate: false });
    for (const k of killMarkers) {
      const el = k.marker.getElement()?.firstElementChild;
      if (el) el.classList.toggle('future', k.t > nt + 0.01);
    }
    if (deathMarker) {
      const el = deathMarker.getElement();
      if (el) el.style.display = nt + 0.01 >= deathT ? '' : 'none';
    }
    if (!scrubbing) $('#scrub').value = String(nt);
    $('#clock').innerHTML = `<b>${fmtClock(nt)}</b> / ${state.raid?.durationApprox ? '약 ' : ''}${fmtClock(state.tEnd)}`;
  }

  // ------------------------------------------------------------------ playback
  function setPlaying(on) {
    if (on && state.t >= state.tEnd - 0.01) setTime(0, true);
    state.playing = on && state.tEnd > 0;
    const btn = $('#play');
    btn.innerHTML = state.playing ? '&#10073;&#10073;' : '&#9654;';
    btn.setAttribute('aria-label', state.playing ? '정지' : '재생');
    if (state.playing) {
      state.lastFrame = performance.now();
      requestAnimationFrame(frame);
    }
  }
  function frame(now) {
    if (!state.playing) return;
    const dt = (now - state.lastFrame) / 1000;
    state.lastFrame = now;
    const next = state.t + dt * state.speed;
    setTime(next);
    if (next >= state.tEnd) {
      setPlaying(false);
      return;
    }
    requestAnimationFrame(frame);
  }

  function renderTicks() {
    const ticks = $('#ticks');
    const end = state.tEnd || 1;
    const pct = (t) => `${Math.min(100, Math.max(0, (t / end) * 100))}%`;
    let html = state.track.map((s) => `<span class="tick" style="left:${pct(s.t)}"></span>`).join('');
    html += (state.raid.logFixes || []).map((f) => `<span class="tick fix" style="left:${pct(f.t)}"></span>`).join('');
    html += (state.raid.kills || []).map((k) => `<span class="tick kill" style="left:${pct(k.t)}" title="${esc(k.name)}"></span>`).join('');
    if (deathT != null) html += `<span class="tick death" style="left:${pct(deathT)}">&#10005;</span>`;
    ticks.innerHTML = html;
  }

  // ------------------------------------------------------------------- summary
  function renderSummary() {
    const raid = state.raid;
    const card = $('#summary');
    const tr = state.track;
    const label = mapLabel(raid.map);
    const status = raid.outcome?.status || 'unknown';
    let dist = 0;
    for (let i = 1; i < tr.length; i++) dist += Math.hypot(tr[i].x - tr[i - 1].x, tr[i].z - tr[i - 1].z);
    const notes = [];
    const fixes = (raid.logFixes || []).length;
    const entry = mapEntryFor(raid.map);
    if (!entry) notes.push('맵을 알 수 없습니다. 편집에서 맵을 골라 주세요.');
    else if (!tr.length) notes.push(fixes
      ? `위치 스크린샷은 없지만 게임이 남긴 서버 위치 기록 ${fixes}개를 회색 점으로 표시했습니다.`
      : '이 레이드에는 위치 스크린샷이 없습니다. 레이드 중 스크린샷 키를 누르면 경로가 남습니다.');
    else if (tr.length === 1) notes.push('기록 지점이 1개뿐이라 경로를 그릴 수 없습니다.');
    // event locations (Factory, Labs) have no tarkov.dev data of their own: the base map's drawing and markers stand in
    if ((entry?.variants || {})[String(raid.map).toLowerCase()]?.event) notes.push('이벤트 맵이라 기본 맵의 지도와 탈출구로 보여 줍니다. 실제와 다를 수 있습니다.');
    if (entry?.unreliable && (tr.length || fixes)) notes.push('이 맵은 tarkov.dev 좌표 보정값이 지도 그림과 맞지 않아 위치가 어긋나게 그려질 수 있습니다 (참고용).');
    if (raid.endReason === 'open') notes.push('레이드 도중 게임을 종료한 것으로 보입니다. 끝난 시각은 마지막 로그 기준 추정입니다.');
    if (raid.transitFromPrev) notes.push('이전 맵에서 트랜짓으로 이어진 레이드입니다.');
    if (raid.exitViaTransit && raid.outcome?.source === 'log') notes.push('트랜짓으로 다음 맵에 이동했습니다 (로그 기준).');
    if (raid.insuranceHint) notes.push('레이드 직후 보험 메시지가 왔습니다. 장비를 잃었을 가능성이 있습니다 (사망 또는 MIA 추정).');
    if (raid.demo) notes.push('데모 레이드입니다. 실제 기록이 아닙니다.');
    card.innerHTML = `
      <div class="sum-head"><h2>${esc(raid.title || label.ko)}</h2>${raid.title ? `<span class="muted">${esc(label.ko)}</span>` : ''}<span class="badge ${status}">${OUTCOME_LABEL[status] || status}</span>${raid.demo ? '<span class="badge demo">데모</span>' : ''}</div>
      <div class="sum-grid">
        <div><div class="k">시작</div><div class="v num">${fmtDate(raid.startedAt)}</div></div>
        <div><div class="k">레이드 시간</div><div class="v num">${raid.durationApprox ? '약 ' : ''}${fmtClock(state.tEnd)}</div></div>
        <div><div class="k">이동 거리</div><div class="v num">${tr.length > 1 ? fmtDist(dist) : '-'}</div></div>
        <div><div class="k">킬</div><div class="v num">${(raid.kills || []).length}</div></div>
        <div><div class="k">기록 지점</div><div class="v num">${tr.length}</div></div>
        <div><div class="k">모드</div><div class="v">${raid.mode === 'pve' ? 'PvE' : raid.mode === 'pvp' ? 'PvP' : '-'}${raid.kind === 'online' ? ' 온라인' : ''}</div></div>
      </div>
      ${notes.map((n) => `<div class="sum-note">${esc(n)}</div>`).join('')}`;
    card.classList.remove('hidden');
    $('#raid-title').innerHTML = `<b>${esc(label.ko)}</b>${label.en ? ` ${esc(label.en)}` : ''} · ${fmtDate(raid.startedAt)}`;
  }

  // --------------------------------------------------------------- raid select
  let selectToken = 0;
  let requestedId = null; // the raid asked for last, on screen or still loading: the last request wins
  async function selectRaid(id, { keepTime = false, forceMap = false } = {}) {
    const token = ++selectToken;
    requestedId = id;
    let raid;
    try {
      raid = await getJSON(`/api/raids/${encodeURIComponent(id)}`);
    } catch (err) {
      if (token !== selectToken) return; // a newer request took over: its raid (or its own failure) is what the user sees
      requestedId = state.raid?.id ?? null; // it never came: the raid on screen is the last request again
      toast(`레이드를 불러오지 못했습니다: ${err.message}`);
      return;
    }
    if (token !== selectToken) return;
    if (id !== editorRaidId && !$('#editor').classList.contains('hidden')) closeEditor(); // its form holds another raid
    setPlaying(false);
    state.raid = raid;
    state.track = (raid.track || []).filter((s) => isFinite(s.x) && isFinite(s.z) && isFinite(s.t)).sort((a, b) => a.t - b.t);
    const lastEvent = Math.max(
      0,
      ...state.track.map((s) => s.t),
      ...(raid.logFixes || []).map((f) => f.t),
      ...(raid.kills || []).map((k) => k.t),
      raid.outcome?.t ?? 0,
    );
    state.tEnd = Math.max(raid.duration || 0, lastEvent);
    if (!keepTime) state.t = state.tEnd;
    $('#scrub').max = String(state.tEnd || 1);
    if (location.hash !== `#/raid/${id}`) history.replaceState(null, '', `#/raid/${id}`);
    document.querySelectorAll('.raid-item').forEach((el) => el.classList.toggle('active', el.dataset.id === id));

    const entry = mapEntryFor(raid.map);
    $('#empty').classList.add('hidden');
    $('#timeline').classList.remove('hidden');
    renderSummary();
    if (!entry) {
      destroyMap(); // also drops the previous raid's markers and death time, and any map image still loading
      $('#layers').classList.add('hidden');
      renderTicks();
      setTime(state.t, true);
      return;
    }
    const fresh = forceMap || !(lmap && state.mapEntry && state.mapEntry.key === entry.key);
    // drawn to the end unless another raid took the screen meanwhile: a newer request still on its way,
    // or one that failed, must not leave this raid's map without its route
    if (!(await ensureMap(entry, forceMap)) || state.raid !== raid) return;
    if (staticEntry !== entry || staticMapId !== String(raid.map).toLowerCase()) buildStaticOverlays(entry); // other entry, or same map in another variant (Factory day/night)
    renderLayersCard();
    renderRaidLayers();
    renderTicks();
    if (fresh || !keepTime) fitRaid();
  }

  function fitRaid() {
    if (!lmap || !state.mapEntry) return;
    const pts = state.track.length ? state.track : state.raid?.logFixes || [];
    if (pts.length > 1) lmap.fitBounds(L.latLngBounds(pts.map(toLatLng)).pad(0.25), { maxZoom: 4 });
    else if (pts.length === 1) lmap.setView(toLatLng(pts[0]), 3);
    else lmap.fitBounds(boundsOf(state.mapEntry.bounds));
  }

  // A map created while its container is hidden (background tab, collapsed pane) measures
  // 0x0 and fits everything at the minimum zoom; refit once the container gets a real size.
  let lastMapSize = 0;
  new ResizeObserver(() => {
    const el = $('#map');
    const size = el.clientWidth * el.clientHeight;
    if (lmap) {
      lmap.invalidateSize();
      if (!lastMapSize && size) fitRaid();
    }
    lastMapSize = size;
  }).observe(document.getElementById('map'));

  // ---------------------------------------------------------------- raid list
  function renderMapFilter() {
    const sel = $('#map-filter');
    const current = sel.value;
    const used = [...new Set(state.raids.map((r) => r.map).filter(Boolean))];
    sel.innerHTML = '<option value="">모든 맵</option>' + used.map((id) => `<option value="${esc(id)}">${esc(mapLabel(id).ko)}</option>`).join('');
    if (used.includes(current)) sel.value = current;
  }

  const hasPositions = (r) => r.samples > 0 || r.fixes > 0 || r.demo; // screenshots or the server's own position records

  function renderList() {
    const list = $('#raid-list');
    const onlyTracked = $('#only-tracked').checked;
    const mapFilter = $('#map-filter').value;
    const onMap = state.raids.filter((r) => !mapFilter || r.map === mapFilter);
    const rows = onlyTracked ? onMap.filter(hasPositions) : onMap;
    const hidden = onMap.length - rows.length;
    const hint = hidden ? `위치 기록이 없는 레이드 ${hidden}개가 숨겨져 있습니다. 위의 체크를 끄면 보입니다.` : '';
    if (!rows.length) {
      list.innerHTML = `<li class="list-empty">${state.raids.length ? `표시할 레이드가 없습니다.${hint ? ` ${hint}` : ''}` : '아직 레이드가 없습니다.'}</li>`;
      return;
    }
    let html = hint ? `<li class="list-note">${hint}</li>` : '';
    let day = '';
    for (const r of rows) {
      const d = dayKey(r.startedAt);
      if (d !== day) {
        day = d;
        html += `<li class="day-head">${esc(d)}</li>`;
      }
      const label = mapLabel(r.map);
      const time = r.startedAt ? new Date(r.startedAt) : null;
      html += `<li class="raid-item${state.raid && state.raid.id === r.id ? ' active' : ''}" data-id="${esc(r.id)}">
        <div class="ri-top"><span class="ri-map">${esc(r.title || label.ko)}${label.en && !r.title ? `<small>${esc(label.en)}</small>` : ''}</span>
          <span class="badge ${esc(r.outcome)}">${OUTCOME_LABEL[r.outcome] || esc(r.outcome)}</span></div>
        <div class="ri-meta num">${time ? `${pad(time.getHours())}:${pad(time.getMinutes())}` : ''}${r.duration ? ` · ${r.durationApprox ? '약 ' : ''}${fmtClock(r.duration)}` : ''}${r.mode === 'pve' ? ' · PvE' : r.mode === 'pvp' ? ' · PvP' : ''}${r.demo ? ' · 데모' : ''}</div>
        <div class="ri-stats num">${r.samples ? `기록 ${r.samples}` : r.fixes ? `<span class="warn">서버 기록 ${r.fixes}</span>` : '<span class="warn">위치 기록 없음</span>'} · 킬 ${r.kills}${r.distance ? ` · ${fmtDist(r.distance)}` : ''}</div>
      </li>`;
    }
    list.innerHTML = html;
  }

  async function loadRaids() {
    state.raids = await getJSON('/api/raids');
    renderMapFilter();
    renderList();
    if (!state.raids.length) showOnboarding();
  }

  function showOnboarding() {
    const s = state.status || {};
    const empty = $('#empty');
    empty.innerHTML = `<div>
      <h2>아직 기록된 레이드가 없습니다</h2>
      <ol>
        <li>레이드 중 <b>인게임 스크린샷 키</b>를 자주 누르세요. 파일 이름에 그 순간의 좌표가 남습니다. (기본값 <span class="kbd">SysReq</span>이 안 되면 게임 설정 &gt; 조작에서 다른 키로 바꾸세요.)</li>
        <li>이 프로그램을 켜 둔 채로 플레이하면, 레이드가 끝날 때 경로가 자동으로 여기에 나타납니다.</li>
        <li>킬·사망 정보는 레이드 화면 하단의 <b>편집</b>에서 입력할 수 있습니다.</li>
      </ol>
      <p>먼저 화면을 보고 싶다면 <code>py -m eftjourney demo</code> 로 데모 레이드를 만들어 보세요.</p>
      <div class="paths">로그 폴더: ${esc(s.logsDir || '찾지 못함')}<br>스크린샷 폴더: ${esc(s.screenshotsDir || '-')}${s.screenshotsDir && !s.screenshotsDirExists ? ' (아직 없음 - 첫 스크린샷을 찍으면 생깁니다)' : ''}</div>
    </div>`;
    empty.classList.remove('hidden');
    $('#summary').classList.add('hidden');
    $('#timeline').classList.add('hidden');
  }

  // -------------------------------------------------------------------- status
  let warningsShown = ''; // the warning texts on screen: a poll with the same ones leaves an open list alone

  function renderWarnings(list) {
    // the scan's warnings (an older server sends none): a small count by the status, the texts on demand
    const texts = (Array.isArray(list) ? list : []).filter((w) => typeof w === 'string' && w.trim());
    const key = JSON.stringify(texts);
    if (key === warningsShown) return;
    warningsShown = key;
    const box = $('#scan-warnings');
    box.classList.toggle('hidden', !texts.length);
    if (!texts.length) box.open = false;
    $('.sw-count', box).textContent = String(texts.length);
    $('summary', box).title = `스캔 주의 사항 ${texts.length}개 · 클릭하면 자세히`;
    $('ul', box).innerHTML = texts.map((w) => `<li>${esc(w)}</li>`).join('');
  }

  async function pollStatus() {
    try {
      const s = await getJSON('/api/status');
      state.status = s;
      const pill = $('#status');
      pill.className = 'status';
      let label = '정적 보기';
      if (s.error) { pill.classList.add('warn'); label = `확인 필요: ${s.error}`; }
      else if (s.inRaid) { pill.classList.add('live'); label = `레이드 진행 중${s.inRaid.map ? ` · ${mapLabel(s.inRaid.map).ko}` : ''} (끝나면 기록 표시)`; }
      else if (s.watching) { pill.classList.add('ok'); label = '감시 중'; }
      $('.label', pill).textContent = label;
      // the label first: a narrow top bar cuts it short
      pill.title = [label, `로그: ${s.logsDir || '찾지 못함'}`, `스크린샷: ${s.screenshotsDir || '-'}`, s.lastScan ? `마지막 스캔: ${s.lastScan}` : ''].filter(Boolean).join('\n');
      renderWarnings(s.warnings);
      if (state.statusVersion !== null && s.version !== state.statusVersion) {
        const before = state.raids.find((r) => r.id === state.raid?.id);
        await loadRaids();
        const after = state.raids.find((r) => r.id === state.raid?.id);
        const editing = !$('#editor').classList.contains('hidden');
        const pending = state.raid && requestedId !== state.raid.id; // another raid on its way: refreshing this one would cancel it
        if (state.raid && after && JSON.stringify(before) !== JSON.stringify(after) && !editing && !pending) selectRaid(state.raid.id, { keepTime: true });
        if (!state.raid && state.raids.length) pickInitialRaid();
      }
      state.statusVersion = s.version;
    } catch (err) {
      const pill = $('#status');
      pill.className = 'status warn';
      $('.label', pill).textContent = pill.title = '서버 연결 끊김'; // the folders in the tooltip were that server's
      renderWarnings([]); // and so were its warnings; whatever runs next reports its own
    }
  }

  // -------------------------------------------------------------------- editor
  const sideOptions = (v) => Object.entries(SIDE_LABEL).map(([k, l]) => `<option value="${k}" ${k === (v || '') ? 'selected' : ''}>${l}</option>`).join('');
  const partOptions = (v) => Object.entries(PART_LABEL).map(([k, l]) => `<option value="${k}" ${k === (v || '') ? 'selected' : ''}>${l}</option>`).join('');
  const CLOCK_HINT = '시각을 00:05:01(시:분:초) 또는 5:01(분:초) 형식으로 입력해 주세요.';
  let editorRaidId = null; // the raid whose values the form holds; saving goes there and nowhere else

  function killRowHtml(k) {
    return `<div class="kill-row">
      <input type="text" data-f="t" value="${esc(fmtClock(k.t))}" placeholder="00:05:01" title="레이드 시작부터의 경과 시간 (예: 00:05:01 또는 5:01)">
      <input type="text" class="wide" data-f="name" value="${esc(k.name)}" placeholder="상대 이름">
      <button class="del" title="삭제" type="button">&#10005;</button>
      <input type="number" data-f="level" value="${esc(k.level ?? '')}" placeholder="Lv" min="0" max="200">
      <select data-f="side">${sideOptions(k.side)}</select>
      <select data-f="bodyPart">${partOptions(k.bodyPart)}</select>
      <span></span>
      <input type="number" data-f="distance" value="${esc(k.distance ?? '')}" placeholder="거리 m" min="0" step="0.1">
      <input type="text" class="wide" data-f="weapon" value="${esc(k.weapon)}" placeholder="사용한 무기">
      <input type="text" class="full" data-f="note" value="${esc(k.note)}" placeholder="메모 (예: 저격수, 감염자)" maxlength="500">
    </div>`;
  }

  function addKillRow(k) {
    const wrap = document.createElement('div');
    wrap.innerHTML = killRowHtml(k);
    const row = wrap.firstElementChild;
    row.dataset.id = k.id || '';
    row.dataset.source = k.source || 'manual';
    $('.del', row).addEventListener('click', () => row.remove());
    $('#ed-kills').appendChild(row);
  }

  function syncDeathFields() {
    const status = $('#ed-status').value;
    $('#ed-death').classList.toggle('hidden', status !== 'killed');
    $('#ed-exit-row').classList.toggle('hidden', status !== 'survived');
  }

  function openEditor() {
    const raid = state.raid;
    if (!raid) return;
    setPlaying(false);
    editorRaidId = raid.id;
    const o = raid.outcome || {};
    $('#ed-status').value = o.status || 'unknown';
    $('#ed-exit').value = o.exit || '';
    $('#ed-killer').value = o.killer || '';
    $('#ed-killer-side').innerHTML = sideOptions(o.killerSide);
    $('#ed-death-weapon').value = o.weapon || '';
    $('#ed-death-part').innerHTML = partOptions(o.bodyPart);
    $('#ed-death-dist').value = o.distance ?? '';
    $('#ed-death-time').value = o.t != null ? fmtClock(o.t) : '';
    $('#ed-death-time').style.borderColor = '';
    $('#ed-kills').innerHTML = '';
    (raid.kills || []).forEach(addKillRow);
    const detected = raid.detectedMap;
    $('#ed-map').innerHTML =
      `<option value="">자동 감지${detected ? ` (${esc(mapLabel(detected).ko)})` : ''}</option>` +
      state.maps.map((m) => `<option value="${esc(m.ids[0])}">${esc(m.nameKo || m.name)}${m.nameKo ? ` (${esc(m.name)})` : ''}</option>`).join('');
    $('#ed-map').value = raid.map && raid.map !== detected ? raid.map : '';
    $('#ed-title').value = raid.title || '';
    $('#ed-notes').value = raid.notes || '';
    syncDeathFields();
    $('#editor').classList.remove('hidden');
    $('#layers').classList.add('hidden');
  }

  function closeEditor() {
    $('#editor').classList.add('hidden');
    renderLayersCard();
  }

  async function saveEditor() {
    const raid = state.raid;
    const id = editorRaidId;
    if (!raid || id !== raid.id) {
      // another raid is on screen now: never write this form into it
      closeEditor();
      toast('편집하던 레이드가 바뀌어 저장하지 않았습니다. 다시 열어 주세요.', 4000);
      return;
    }
    const kills = [];
    let bad = false;
    document.querySelectorAll('#ed-kills .kill-row').forEach((row) => {
      const get = (f) => $(`[data-f="${f}"]`, row).value;
      const t = parseClock(get('t'));
      $(`[data-f="t"]`, row).style.borderColor = t == null ? 'var(--death)' : '';
      if (t == null) { bad = true; return; }
      kills.push({
        id: row.dataset.id || undefined,
        t,
        name: get('name'),
        level: get('level') === '' ? null : Number(get('level')),
        side: get('side'),
        weapon: get('weapon'),
        bodyPart: get('bodyPart'),
        distance: get('distance') === '' ? null : Number(get('distance')),
        note: get('note'),
        source: row.dataset.source || 'manual',
      });
    });
    const status = $('#ed-status').value;
    // the death time may stay empty (unknown), but whatever is typed there must parse like a kill time
    const deathField = $('#ed-death-time');
    const deathText = status === 'killed' ? deathField.value.trim() : '';
    const deathAt = deathText ? parseClock(deathText) : null;
    deathField.style.borderColor = deathText && deathAt == null ? 'var(--death)' : '';
    if (bad || (deathText && deathAt == null)) { toast(CLOCK_HINT); return; }
    const outcome = { status, source: 'manual' };
    if (status === 'killed') {
      Object.assign(outcome, {
        killer: $('#ed-killer').value,
        killerSide: $('#ed-killer-side').value,
        weapon: $('#ed-death-weapon').value,
        bodyPart: $('#ed-death-part').value,
        distance: $('#ed-death-dist').value === '' ? null : Number($('#ed-death-dist').value),
        t: deathAt,
      });
    }
    if (status === 'survived') outcome.exit = $('#ed-exit').value;
    const edits = { outcome, kills, title: $('#ed-title').value, notes: $('#ed-notes').value };
    if ($('#ed-map').value) edits.map = $('#ed-map').value;
    // a time past the end of the raid is most likely a typo; save it anyway, but say so
    const limit = raid.duration || 0;
    const late = limit > 0 && [...kills.map((k) => k.t), deathAt].some((t) => t != null && t > limit + 1);
    try {
      await getJSON(`/api/raids/${encodeURIComponent(id)}/edits`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(edits),
      });
    } catch (err) {
      toast(`저장하지 못했습니다: ${err.message}`);
      return;
    }
    closeEditor();
    if (late) toast(`저장했습니다. 레이드 시간(${raid.durationApprox ? '약 ' : ''}${fmtClock(limit)})보다 늦은 시각이 있으니 확인해 주세요.`, 5000);
    else toast('저장했습니다');
    await loadRaids();
    if (requestedId === state.raid.id) await selectRaid(state.raid.id, { keepTime: true }); // not over a raid asked for meanwhile
  }

  // ---------------------------------------------------------------------- boot
  function hashRaidId() {
    const raw = (location.hash.match(/^#\/raid\/(.+)$/) || [])[1] || '';
    try {
      return decodeURIComponent(raw);
    } catch (_) {
      return ''; // a broken %-escape in a pasted link
    }
  }

  function pickInitialRaid() {
    const fromHash = hashRaidId();
    const target = state.raids.find((r) => r.id === fromHash) || state.raids.find((r) => r.samples > 0 || r.demo) || state.raids.find(hasPositions) || state.raids[0];
    if (target) selectRaid(target.id);
  }

  function bindUi() {
    $('#toggle-sidebar').addEventListener('click', () => {
      const app = $('#app');
      if (window.matchMedia('(max-width: 820px)').matches) app.classList.toggle('side-open');
      else app.classList.toggle('side-collapsed');
      setTimeout(() => lmap && lmap.invalidateSize(), 50);
    });
    $('#raid-list').addEventListener('click', (e) => {
      const item = e.target.closest('.raid-item');
      if (!item) return;
      $('#app').classList.remove('side-open');
      if (!$('#editor').classList.contains('hidden')) closeEditor();
      selectRaid(item.dataset.id);
    });
    $('#only-tracked').addEventListener('change', renderList);
    $('#map-filter').addEventListener('change', renderList);
    $('#play').addEventListener('click', () => setPlaying(!state.playing));
    $('#speed').addEventListener('change', (e) => { state.speed = Number(e.target.value) || 10; });
    const scrub = $('#scrub');
    scrub.addEventListener('input', (e) => setTime(Number(e.target.value), true));
    scrub.addEventListener('pointerdown', () => { scrubbing = true; });
    const endScrub = () => {
      if (!scrubbing) return;
      scrubbing = false;
      scrub.blur(); // Space and the arrow keys go back to the player
    };
    // the button can come up outside the slider, so listen on the window
    window.addEventListener('pointerup', endScrub);
    window.addEventListener('pointercancel', endScrub);
    scrub.addEventListener('change', endScrub);
    $('#follow').addEventListener('click', () => {
      state.follow = !state.follow;
      $('#follow').classList.toggle('on', state.follow);
      if (state.follow) setTime(state.t, true);
    });
    $('#edit-toggle').addEventListener('click', () => ($('#editor').classList.contains('hidden') ? openEditor() : closeEditor()));
    $('#ed-close').addEventListener('click', closeEditor);
    $('#ed-cancel').addEventListener('click', closeEditor);
    $('#ed-save').addEventListener('click', saveEditor);
    $('#ed-status').addEventListener('change', syncDeathFields);
    $('#ed-add-kill').addEventListener('click', () => addKillRow({ t: state.t, name: '', side: '', bodyPart: '', weapon: '', distance: null, level: null }));
    document.addEventListener('click', (e) => {
      const img = e.target.closest('img.shot');
      if (!img) return;
      const box = $('#lightbox');
      $('img', box).src = img.dataset.full;
      $('.caption', box).textContent = decodeURIComponent(img.dataset.full.split('/').pop());
      box.classList.remove('hidden');
    });
    $('#lightbox').addEventListener('click', () => $('#lightbox').classList.add('hidden'));
    document.addEventListener('click', (e) => {
      const box = $('#scan-warnings');
      if (box.open && !box.contains(e.target)) box.open = false; // the warning list closes like a menu
    });
    document.addEventListener('keydown', (e) => {
      // text fields keep their keys; the timeline slider gets the player's Space and arrows, not its own ±step
      if (e.target.isContentEditable || e.target.closest('input:not([type=range]), textarea, select')) return;
      if ((e.code === 'Space' || e.key === 'ArrowRight' || e.key === 'ArrowLeft') && e.target.matches('input[type=range]')) e.preventDefault();
      if (e.key === 'Escape') {
        $('#lightbox').classList.add('hidden');
        $('#scan-warnings').open = false;
        if (!$('#editor').classList.contains('hidden')) closeEditor();
      }
      if (!state.raid) return;
      if (e.code === 'Space') { e.preventDefault(); setPlaying(!state.playing); }
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        const tr = state.track;
        if (!tr.length) return;
        e.preventDefault();
        const target = e.key === 'ArrowRight'
          ? tr.find((s) => s.t > state.t + 0.01) || { t: state.tEnd }
          : [...tr].reverse().find((s) => s.t < state.t - 0.01) || { t: 0 };
        setTime(target.t, true);
      }
    });
    window.addEventListener('hashchange', () => {
      const id = hashRaidId();
      // against the raid asked for last, not only the one on screen: going back to it while another loads must win
      if (id && id !== requestedId) selectRaid(id); // closes an editor that holds the previous raid
    });
  }

  async function boot() {
    if (!window.L) {
      document.body.innerHTML = '<p style="padding:24px">지도 라이브러리(Leaflet)를 불러오지 못했습니다. 인터넷 연결을 확인해 주세요.</p>';
      return;
    }
    bindUi();
    try {
      const maps = await getJSON('/api/maps');
      state.maps = maps.maps || [];
      state.attribution = maps.attribution || {};
    } catch (err) {
      toast(`맵 정보를 불러오지 못했습니다: ${err.message}`, 5000);
    }
    await pollStatus();
    try {
      await loadRaids();
    } catch (err) {
      toast(`레이드 목록을 불러오지 못했습니다: ${err.message}`, 5000);
    }
    setInterval(pollStatus, 4000); // first, so nothing in the raid selection can keep the status from updating
    pickInitialRaid();
  }

  boot();
})();
