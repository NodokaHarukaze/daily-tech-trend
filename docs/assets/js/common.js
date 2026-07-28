// --- アクセス計測（プライバシーフレンドリー・Cookie不使用） ---
// GoatCounter (https://www.goatcounter.com) でサイト登録後、
// 下記にエンドポイントを設定すると計測が有効になる（空のままなら何もしない）。
// 例: 'https://daily-tech-trend.goatcounter.com/count'
const DTT_GOATCOUNTER_ENDPOINT = '';
if (DTT_GOATCOUNTER_ENDPOINT) {
  const s = document.createElement('script');
  s.async = true;
  s.dataset.goatcounter = DTT_GOATCOUNTER_ENDPOINT;
  s.src = 'https://gc.zgo.at/count.js';
  document.head.appendChild(s);
}

(() => {
  const selectedTags = new Set();
  let tagMode = 'AND';
  let catsCollapsed = false;
  let currentQuery = '';

  function renderStateChips(states){
    return states.map(([label, value]) => `<span class="state-chip"><b>${label}</b>${value}</span>`).join('');
  }

  function updateTagActiveView(){
    const box = document.getElementById('tag-active');
    if (!box) return;
    const states = [];
    if (currentQuery) states.push(['検索', `「${currentQuery}」`]);
    if (selectedTags.size > 0) states.push(['タグ', [...selectedTags].join(', ')]);
    if (selectedTags.size > 1) states.push(['モード', tagMode]);
    if (states.length === 0){ box.style.display = 'none'; box.textContent = ''; return; }
    box.style.display = '';
    box.innerHTML = renderStateChips(states);
  }

  // タグ選択を localStorage に永続化し、次回訪問時に復元する（ウォッチリスト的な使い方）
  function saveTagState(){
    try {
      localStorage.setItem('dttSelectedTags', JSON.stringify([...selectedTags]));
      localStorage.setItem('dttTagMode', tagMode);
    } catch (_) {}
  }

  function restoreTagState(){
    try {
      const saved = JSON.parse(localStorage.getItem('dttSelectedTags') || '[]');
      const mode = localStorage.getItem('dttTagMode');
      if (mode === 'OR' || mode === 'AND'){
        tagMode = mode;
        const cb = document.getElementById('tagModeOr');
        if (cb) cb.checked = (mode === 'OR');
      }
      for (const tg of saved){
        // このページに存在するタグだけ復元する
        const btns = document.querySelectorAll(`[data-tag-btn="${CSS.escape(tg)}"]`);
        if (btns.length === 0) continue;
        selectedTags.add(tg);
        btns.forEach(b => b.classList.add('active'));
      }
    } catch (_) {}
  }

  function toggleTag(tg){
    selectedTags.has(tg) ? selectedTags.delete(tg) : selectedTags.add(tg);
    document.querySelectorAll(`[data-tag-btn="${tg}"]`).forEach(b => b.classList.toggle('active', selectedTags.has(tg)));
    saveTagState();
    syncTagMoreLabel();
    updateTagActiveView();
    applyFilter();
  }

  function clearTagFilter(){
    selectedTags.clear();
    document.querySelectorAll('[data-tag-btn]').forEach(b => b.classList.remove('active'));
    saveTagState();
    syncTagMoreLabel();
    updateTagActiveView();
    applyFilter();
  }

  function applyFilter(){
    const q = (document.getElementById('q')?.value || '').toLowerCase();
    currentQuery = q.trim();
    const rows = document.querySelectorAll('.category-body .topic-row, .top-zone .topic-row');
    let hit = 0;

    rows.forEach(el => {
      const title = (el.dataset.title || '').toLowerCase();
      const summary = (el.dataset.summary || '').toLowerCase();
      const tags = (el.dataset.tags || '');
      const hitQ = !q || title.includes(q) || summary.includes(q);
      const itemTags = tags.split(',').map(s => s.trim()).filter(Boolean);
      const sel = [...selectedTags];
      const hitTag = sel.length === 0 || (tagMode === 'AND' ? sel.every(t => itemTags.includes(t)) : sel.some(t => itemTags.includes(t)));
      const show = hitQ && hitTag;
      el.style.display = show ? '' : 'none';
      if (show) hit++;
    });

    const box = document.getElementById('filter-count');
    if (box){
      const byQuery = !!currentQuery;
      const byTag = selectedTags.size > 0;
      const isFiltering = byQuery || byTag;
      if (isFiltering) {
        const mode = byQuery && byTag ? '（検索＋タグ）' : byQuery ? '（検索）' : '（タグ）';
        box.textContent = `該当 ${hit} / 全 ${rows.length}${mode}`;
        box.style.display = '';
      } else box.style.display = 'none';
    }

    const hint = document.getElementById('filter-hint');
    if (hint){
      if ((q || selectedTags.size > 0) && hit === 0) {
        hint.innerHTML = '該当なし。<strong>条件を緩めてください：</strong> 検索語を短くする／別表現にする・フィルタをすべてリセットする';
        hint.style.display = '';
      } else hint.style.display = 'none';
    }
    updateTagActiveView();
  }

  function syncStickyOffsets(){
    const summary = document.querySelector('.summary-card');
    const category = document.querySelector('.category-header');
    const root = document.documentElement;
    if (summary) root.style.setProperty('--mobile-summary-height', `${Math.ceil(summary.getBoundingClientRect().height)}px`);
    if (category) root.style.setProperty('--mobile-category-height', `${Math.ceil(category.getBoundingClientRect().height)}px`);
  }

  function syncToggleAllCatsLabel(){
    const label = catsCollapsed ? 'すべて開く' : 'すべて閉じる';
    document.querySelectorAll('[data-toggle-all-cats]').forEach(btn => { btn.textContent = label; });
  }

  function toggleCat(id){ const sec = document.getElementById('cat-' + id); if (sec) sec.classList.toggle('collapsed'); }
  function toggleAllCats(){
    catsCollapsed = !catsCollapsed;
    document.querySelectorAll('.category-section').forEach(sec => sec.classList.toggle('collapsed', catsCollapsed));
    syncToggleAllCatsLabel();
  }
  function parseDateValue(v){ const t = Date.parse(String(v || '').replace(' ', 'T')); return Number.isNaN(t) ? 0 : t; }

  function applySort(){
    const key = document.getElementById('sortKey')?.value || 'date';
    const dir = document.getElementById('sortDir')?.value || 'desc';
    const sign = dir === 'asc' ? 1 : -1;
    const lists = [...document.querySelectorAll('ol.top-list'), ...document.querySelectorAll('.category-body > ul'), ...document.querySelectorAll('.topbox > ul')];
    for (const list of lists){
      const items = [...list.querySelectorAll(':scope > li.topic-row')];
      items.sort((a,b) => {
        let av, bv;
        if (key === 'composite') {
          av = (parseInt(a.dataset.imp || '0', 10) || 0) + (parseInt(a.dataset.recent || '0', 10) || 0) * 10;
          bv = (parseInt(b.dataset.imp || '0', 10) || 0) + (parseInt(b.dataset.recent || '0', 10) || 0) * 10;
        } else if (key === 'importance') {
          av = parseInt(a.dataset.imp || '0', 10) || 0;
          bv = parseInt(b.dataset.imp || '0', 10) || 0;
        } else {
          av = parseDateValue(a.dataset.date);
          bv = parseDateValue(b.dataset.date);
        }
        if (av !== bv) return (av - bv) * sign;
        return (a.dataset.title || '').localeCompare(b.dataset.title || '');
      });
      items.forEach(li => list.appendChild(li));
    }
    try { localStorage.setItem('sortKey', key); localStorage.setItem('sortDir', dir); } catch (_) {}
  }

  function initSortUI(){
    try {
      const k = localStorage.getItem('sortKey');
      const d = localStorage.getItem('sortDir');
      if (k && document.getElementById('sortKey')) document.getElementById('sortKey').value = k;
      if (d && document.getElementById('sortDir')) document.getElementById('sortDir').value = d;
    } catch (_) {}
    applySort();
  }

  function openSectionFor(el){ const sec = el.closest('.category-section'); if (sec) sec.classList.remove('collapsed'); }
  function ensureVisible(el){ if (el.style.display === 'none') el.style.display = ''; }
  function scrollToHash(prefix, hash){
    if (!hash || !hash.startsWith(`#${prefix}-`)) return false;
    const el = document.querySelector(hash);
    if (!el) return false;
    openSectionFor(el); ensureVisible(el);
    const det = el.querySelector('details.insight'); if (det && !det.open) det.open = true;
    requestAnimationFrame(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    return true;
  }

  function openTopZoneFor(el){
    const fold = el?.closest('details[data-top-zone-details]');
    if (fold) fold.open = true;
  }

  function collapseTopZonesOnFirstView(){
    const folds = document.querySelectorAll('details[data-top-zone-details]');
    if (!folds.length) return;
    if (location.hash) return;
    let seen = false;
    try { seen = sessionStorage.getItem('dtt-top-zone-seen') === '1'; } catch (_) {}
    if (seen) return;
    folds.forEach(f => { f.open = false; });
    try { sessionStorage.setItem('dtt-top-zone-seen', '1'); } catch (_) {}
  }

  function enableMobileTopZoneAccordion(){
    const folds = [...document.querySelectorAll('details[data-top-zone-details]')];
    if (!folds.length) return;
    folds.forEach(fold => {
      fold.addEventListener('toggle', () => {
        if (!fold.open || window.innerWidth > 640) return;
        folds.forEach(other => {
          if (other !== fold) other.open = false;
        });
      });
    });
  }

  function revealHashTarget(prefix, hash){
    if (!hash) return;
    if (scrollToHash(prefix, hash)) {
      const topicEl = document.querySelector(hash);
      if (topicEl) openTopZoneFor(topicEl);
      return;
    }
    if (hash.startsWith('#cat-')) {
      const sec = document.querySelector(hash);
      if (sec) {
        sec.classList.remove('collapsed');
        requestAnimationFrame(() => sec.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      }
    }
  }

  function bindHashNavigation(prefix){
    document.addEventListener('click', e => {
      const a = e.target.closest(`a[href^="#${prefix}-"], a[href^="#cat-"]`);
      if (!a) return;
      const hash = a.getAttribute('href');
      revealHashTarget(prefix, hash);
      e.preventDefault();
      history.replaceState(null, '', hash);
    });
    window.addEventListener('load', () => { if (location.hash) revealHashTarget(prefix, location.hash); });
  }

  function syncTagMoreLabel(){
    const bar = document.getElementById('tagBar');
    const more = document.getElementById('tagMore');
    if (!bar || !more) return;
    more.textContent = bar.classList.contains('collapsed') ? '＋ よく使うタグ以外も表示' : '− タグ一覧をたたむ';
  }


  function setupCategoryToc(){
    const toc = document.querySelector('.category-toc');
    if (!toc) return;

    const links = [...toc.querySelectorAll('[data-category-link]')];
    const select = toc.querySelector('#category-toc-select');
    const sections = links
      .map(link => document.getElementById(`cat-${link.dataset.categoryLink}`))
      .filter(Boolean);

    const setActive = (id) => {
      links.forEach(link => {
        const active = link.dataset.categoryLink === id;
        link.classList.toggle('active', active);
      });
      if (select) {
        const nextValue = id ? `#cat-${id}` : '';
        if (select.value !== nextValue) select.value = nextValue;
      }
    };

    if (select) {
      select.addEventListener('change', e => {
        const hash = e.target.value;
        if (!hash) return;
        revealHashTarget('topic', hash);
        history.replaceState(null, '', hash);
      });
    }

    if (!sections.length || !('IntersectionObserver' in window)) {
      const first = links[0]?.dataset.categoryLink;
      if (first) setActive(first);
      return;
    }

    const observer = new IntersectionObserver(entries => {
      const visible = entries
        .filter(entry => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
      if (!visible.length) return;
      const id = visible[0].target.id.replace('cat-', '');
      setActive(id);
    }, {
      rootMargin: '-20% 0px -60% 0px',
      threshold: [0.1, 0.25, 0.5],
    });

    sections.forEach(sec => observer.observe(sec));
    const first = links[0]?.dataset.categoryLink;
    if (first) setActive(first);
  }


  function moveImportanceBasisIntoInsight(){
    const rows = document.querySelectorAll('.topic-row');
    rows.forEach(row => {
      const basis = [...row.querySelectorAll(':scope > div.small, :scope > .small')]
        .find(el => (el.textContent || '').trim().startsWith('算出根拠（簡易）'));
      if (!basis) return;

      let details = row.querySelector(':scope > details.insight');
      if (!details) {
        details = document.createElement('details');
        details.className = 'insight';
        const summary = document.createElement('summary');
        summary.className = 'small';
        summary.textContent = '要約・解説を表示';
        details.appendChild(summary);
        basis.insertAdjacentElement('afterend', details);
      }

      const alreadyMoved = details.querySelector('[data-importance-basis]');
      if (alreadyMoved) {
        basis.remove();
        return;
      }

      const wrapped = document.createElement('div');
      wrapped.className = 'small';
      wrapped.style.marginTop = '6px';
      wrapped.dataset.importanceBasis = '1';
      wrapped.innerHTML = `<strong>算出根拠（簡易）</strong>：${basis.textContent.replace(/^\s*算出根拠（簡易）\s*[:：]\s*/, '')}`;

      const summaryEl = details.querySelector(':scope > summary');
      if (summaryEl) summaryEl.insertAdjacentElement('afterend', wrapped);
      else details.prepend(wrapped);

      basis.remove();
    });
  }

  // --- トップページ軽量化: details.insight の中身を初回展開時に遅延fetch ---
  // 検索インデックス(search.html内 ensureIndex())と同型のパターン。
  const INSIGHTS_FILE_BY_PREFIX = { topic: 'tech', news: 'news' };
  let insightsData = null;
  let insightsPromise = null;

  function ensureInsights(pagePrefix){
    if (insightsPromise) return insightsPromise;
    const name = INSIGHTS_FILE_BY_PREFIX[pagePrefix] || pagePrefix;
    insightsPromise = fetch(`/daily-tech-trend/assets/data/insights_${name}.json`)
      .then(r => r.ok ? r.json() : {})
      .then(data => { insightsData = data || {}; return insightsData; })
      .catch(() => { insightsData = {}; return insightsData; });
    return insightsPromise;
  }

  function truncateUrlLike(s, length){
    // Jinja2 の truncate(60, True) と同じ算出方法（leeway既定5）
    const leeway = 5, end = '...';
    s = String(s || '');
    if (s.length <= length + leeway) return s;
    return s.slice(0, length - end.length) + end;
  }

  function buildTechKeyPoints(list){
    const out = [];
    let limited = false;
    const seen = new Set();
    (list || []).forEach(raw => {
      const k = raw || '';
      if (k.includes('本文中の明確な事実は限定的') || k.includes('本文が短く')) {
        if (!limited) { out.push('推測：本文情報が限られるため、影響範囲・当事者・時系列をリンク先で要確認'); limited = true; }
      } else if (k.startsWith('推測')) {
        if (!seen.has(k)) { out.push(k); seen.add(k); }
      } else {
        out.push(k);
      }
    });
    if (out.length === 0) out.push('推測：リンク先の本文確認が必要');
    return out;
  }

  function buildNewsKeyPoints(list){
    const out = [];
    let hasGuess = false;
    (list || []).forEach(raw => {
      const kp = raw || '';
      if (kp.includes('推測') || kp.includes('本文確認')) {
        if (!hasGuess) { out.push(kp); hasGuess = true; }
      } else {
        out.push(kp);
      }
    });
    if (out.length === 0) out.push('推測：記事情報が限定的なため、リンク先の本文確認が必要');
    return out;
  }

  function appendPerspectiveBlock(container, obj, className, heading){
    if (!obj) return;
    const rows = [['engineer', '技術者目線'], ['management', '経営者目線'], ['consumer', '消費者目線']];
    if (!rows.some(([k]) => obj[k])) return;
    const box = document.createElement('div');
    box.className = className;
    if (heading) {
      const h = document.createElement('div');
      h.style.fontSize = '12px'; h.style.color = '#666'; h.style.marginBottom = '2px';
      h.textContent = heading;
      box.appendChild(h);
    }
    rows.forEach(([key, label]) => {
      if (!obj[key]) return;
      const div = document.createElement('div');
      const b = document.createElement('b');
      b.textContent = label;
      div.appendChild(b);
      div.appendChild(document.createTextNode(': ' + obj[key]));
      box.appendChild(div);
    });
    container.appendChild(box);
  }

  function renderInsightBody(container, data, variant){
    container.innerHTML = '';
    if (!data) {
      container.textContent = '（読み込みに失敗しました）';
      return;
    }

    if (variant === 'news' && data.importance_basis) {
      const div = document.createElement('div');
      div.className = 'small';
      div.style.marginTop = '6px';
      const b = document.createElement('strong');
      b.textContent = '算出根拠（簡易）';
      div.appendChild(b);
      div.appendChild(document.createTextNode('：' + data.importance_basis));
      container.appendChild(div);
    }

    if (data.summary) {
      const div = document.createElement('div');
      if (variant === 'tech') { div.style.fontSize = '13px'; div.style.lineHeight = '1.6'; }
      const b = document.createElement('strong');
      b.textContent = '要約';
      div.appendChild(b);
      div.appendChild(document.createTextNode('：' + data.summary));
      container.appendChild(div);
    }

    if (data.key_points && data.key_points.length) {
      const lis = variant === 'tech' ? buildTechKeyPoints(data.key_points) : buildNewsKeyPoints(data.key_points);
      if (lis.length) {
        const ul = document.createElement('ul');
        ul.className = 'kps';
        lis.forEach(text => {
          const li = document.createElement('li');
          li.textContent = text;
          ul.appendChild(li);
        });
        container.appendChild(ul);
      }
    }

    appendPerspectiveBlock(container, data.perspectives, 'perspectives', null);
    appendPerspectiveBlock(container, data.perspective_digest, 'perspective-digest', '立場別くわしい解説');

    if (data.evidence_urls && data.evidence_urls.length) {
      const div = document.createElement('div');
      div.className = 'evidence-urls';
      const b = document.createElement('strong');
      b.textContent = '根拠';
      div.appendChild(b);
      div.appendChild(document.createTextNode('：'));
      data.evidence_urls.forEach((u, i) => {
        const a = document.createElement('a');
        a.href = u;
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = truncateUrlLike(u, 60);
        div.appendChild(a);
        if (i < data.evidence_urls.length - 1) div.appendChild(document.createTextNode(' | '));
      });
      container.appendChild(div);
    }
  }

  function initInsightLazyLoad(pagePrefix){
    const variant = pagePrefix === 'news' ? 'news' : 'tech';
    document.querySelectorAll('details.insight[data-insight-topic]').forEach(det => {
      det.addEventListener('toggle', () => {
        if (!det.open) return;
        const body = det.querySelector('[data-insight-pending]');
        if (!body) return;
        ensureInsights(pagePrefix).then(data => {
          renderInsightBody(body, data[det.dataset.insightTopic], variant);
        });
      }, { once: true });
    });
  }

  function setupCommon(pagePrefix){
    document.getElementById('q')?.addEventListener('input', applyFilter);
    document.getElementById('tagModeOr')?.addEventListener('change', e => { tagMode = e.target.checked ? 'OR' : 'AND'; saveTagState(); updateTagActiveView(); applyFilter(); });
    document.getElementById('tagMore')?.addEventListener('click', () => {
      const bar = document.getElementById('tagBar'); if (!bar) return;
      bar.classList.toggle('collapsed');
      syncTagMoreLabel();
      syncStickyOffsets();
    });
    window.addEventListener('resize', syncStickyOffsets);
    window.addEventListener('load', syncStickyOffsets);
    moveImportanceBasisIntoInsight();
    initSortUI();
    collapseTopZonesOnFirstView();
    enableMobileTopZoneAccordion();
    initInsightLazyLoad(pagePrefix);
    bindHashNavigation(pagePrefix);
    setupCategoryToc();
    syncToggleAllCatsLabel();
    restoreTagState();
    syncTagMoreLabel();
    updateTagActiveView();
    applyFilter();
  }

  window.toggleTag = toggleTag;
  window.clearTagFilter = clearTagFilter;
  window.applyFilter = applyFilter;
  window.toggleCat = toggleCat;
  window.toggleAllCats = toggleAllCats;
  window.applySort = applySort;
  window.DTTCommon = { setupCommon };
})();
