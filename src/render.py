# src/render.py
from __future__ import annotations
import os

import json
from pathlib import Path
#from typing import Any, Dict, List

import yaml
from jinja2 import Template
from datetime import datetime, timedelta, timezone

from db import connect

from typing import Any, List
import time
from datetime import datetime, timezone, timedelta

def fmt_date(s):
    if not s:
        return ""
    dt = datetime.fromisoformat(s.replace("Z",""))
    return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y/%m/%d %H:%M")

def _now_sec():
    return time.perf_counter()

COMMON_CSS = r"""
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;line-height:1.6}
h1{margin:0 0 10px}
h2{margin:22px 0 10px}
.meta{color:#666;font-size:12px;margin:6px 0 14px}
.nav{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 18px}
.nav a{display:inline-block;border:1px solid #ddd;border-radius:999px;padding:6px 10px;text-decoration:none;color:#111}
.nav a.active{border-color:#333;font-weight:700}
.card{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:12px 14px;margin:10px 0}
.small{color:#666;font-size:12px}
.badge{display:inline-block;border:1px solid #ddd;border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:6px}
.btn{padding:6px 10px;border:1px solid #ddd;border-radius:10px;background:#fff;cursor:pointer;display:inline-block;text-decoration:none}
.btn:hover{background:#f7f7f7}
ul{margin:0;padding-left:18px}
li{margin:10px 0}
a{color:inherit}
"""
TECH_EXTRA_CSS = r"""
/* techの箱・構造を定義している部分をここへ集約 */
.summary-card, .topbox, .top-col, .insight{
  background:#fafafa;
  border:1px solid #eee;
  border-radius:12px;
  padding:12px 14px;
}
.top-col{ background:#fff; }

/* techの見出し間隔・小文字 */
.small{color:#666;font-size:12px}
.badge{display:inline-block;border:1px solid #ddd;border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:6px}

/* もしtechにタグの見た目があるなら寄せる */
.tag{display:inline-block;border:1px solid #ddd;border-radius:999px;padding:2px 8px;font-size:12px;color:#444;margin-left:6px}


     /* --- UX改善①: 上部サマリー + 横断TOP --- */
    .summary-card{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:12px 14px;margin:10px 0 14px}
    .summary-title{font-weight:800;font-size:16px;margin:0 0 6px}
    .summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:8px}
    .summary-item .k{color:#666;font-size:11px}
    .summary-item .v{font-size:13px;font-weight:650}

    .top-zone{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0 18px}
    .top-col{background:#fff;border:1px solid #eee;border-radius:12px;padding:10px 12px}
    .top-col h3{margin:0 0 8px;font-size:14px}
    .top-list{margin:0;padding-left:18px}
    .mini{color:#666;font-size:12px;margin-top:2px}

    .quick-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:10px}
    #q{padding:6px 10px;border:1px solid #ddd;border-radius:10px;min-width:260px}
    .btn{padding:6px 10px;border:1px solid #ddd;border-radius:10px;background:#fff;cursor:pointer}

    .badge.hot{font-weight:800}
    .badge.new{border-style:dashed}

    .imp-5{border-color:#f33}
    .imp-4{border-color:#f80}
    .imp-3{border-color:#cc0}
    .imp-2{border-color:#6c6}
    .imp-1{border-color:#9ad}
    .imp-0{border-color:#ccc}

    .category-section{margin-top:18px}
    .category-header{display:flex;align-items:center;gap:10px}
    .category-body{margin-top:8px}
    .category-section.collapsed .category-body{display:none}
    
    /* ===== Mobile-first overrides ===== */
    h1{font-size:26px}
    h2{font-size:18px}

    .summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
    .top-zone{grid-template-columns:1fr}
    .top-list{padding-left:18px}
    .top-item, .topic-row{margin:10px 0}

    /* 長いタイトル対策（はみ出し防止） */
    .topic-link, a{
      display:inline;
      overflow-wrap:anywhere;
      word-break:break-word;
    }

    /* 検索・フィルタは縦積み気味に */
    .quick-controls{gap:8px}
    #q{min-width:0; width:100%}
    .quick-controls label{font-size:12px}
    .btn{padding:8px 12px} /* タップ領域増 */

    /* カテゴリ見出し周り */
    .category-header{gap:8px}
    .category-header .btn{margin-left:auto}

    /* スマホで「今日の要点」ゾーンを見やすく */
    .summary-card{padding:12px}
    .top-col{padding:10px}

    /* 画面幅が広い時だけPCレイアウトへ */
    @media (min-width: 820px){
      body{margin:24px; font-size:16px}
      .summary-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
      .top-zone{grid-template-columns:1fr 1fr}
      #q{width:auto; min-width:260px}
    }

    /* デフォルト（スマホ）：固定しない */
    .summary-card{
      position: static;
    }

    /* PCサイズ以上のみ固定 */
    @media (min-width: 820px){
      .summary-card{
        position: sticky;
        top: 8px;
        z-index: 10;
      }
    }
    details.insight { margin-top:6px; }
    details.insight > summary {
      cursor:pointer;
      list-style:none;
    }
    details.insight > summary::-webkit-details-marker {
      display:none;
    }
    details.insight > summary::before {
      content:"▶ ";
    }
    details.insight[open] > summary::before {
      content:"▼ ";
    }
    /* details 展開時の視認性向上 */
    details.insight {
      border: 1px solid #eee;
      border-radius: 10px;
      padding: 6px 8px;
      background: #fff;
    }

    details.insight[open] {
      background: #f7faff;           /* 薄い青 */
      border-color: #dbe7ff;
    }

    /* summary（トグル）の見た目 */
    details.insight > summary {
      cursor: pointer;
      padding: 4px 0;
      font-weight: 500;
    }

    details.insight > summary::-webkit-details-marker {
      display: none;
    }

    /* 開閉アイコン */
    details.insight > summary::before {
      content: "▶ ";
      color: #4c6ef5;
    }
    details.insight[open] > summary::before {
      content: "▼ ";
    }

    /* 展開後の中身の余白 */
    details.insight[open] > *:not(summary) {
      margin-top: 6px;
    }
    /* 開いている要約だけ影を付ける */
    details.insight[open] {
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
    }

    /* スマホで浮きすぎないように微調整 */
    @media (max-width: 640px) {
      details.insight[open] {
        box-shadow: 0 3px 10px rgba(0, 0, 0, 0.07);
      }
    }
    /* ジャンプ時に sticky に隠れない */
    .topic-row { scroll-margin-top: 88px; }
    
    #filter-count { color:#555; }
    
    #filter-hint { color:#666; }
    #filter-hint strong { color:#444; }

    .nas .small{display:block;margin-top:2px}

    .close-floating{
      position: fixed;
      right: 16px;
      bottom: 16px;
      z-index: 9999;
      padding: 10px 14px;
      border-radius: 999px;
    }
    .category-header{
      position: sticky;
      top: 0;
      z-index: 10;
      background: #fff; /* 背景必須 */
    }

    /* Tag bar: wrap + mobile collapse */
    .tag-bar{
      display:flex;
      flex-wrap:wrap;
      gap:6px;
      align-items:center;
    }

    .btn-reset{
      background:#f5f5f5;
      border:1px solid #ccc;
      font-weight:700;
    }

    .btn-more{
      background:#fff;
      border:1px dashed #ddd;
      font-weight:650;
    }

    /* スマホ時：初期は7個まで表示（Reset + OR + タグ群含めて調整可） */
    @media (max-width: 640px){
      #tagBar.collapsed button:nth-of-type(n+8){
        display:none;
      }
      /* ORチェックのラベルは常に見せたいなら、上のnth-of-type対象外にするため別classで扱う */
      .tag-mode{ margin-left:4px; }
    }
    .date{
      margin-left: 6px;
      font-size: 0.85em;
      color: #666;
      white-space: nowrap;
    }
    /* techの主要ボックスをnewsの.cardに寄せる */
    .summary-card, .topbox, .top-col, .insight{
      background:#fafafa;
      border:1px solid #eee;
      border-radius:12px;
      padding:12px 14px;
    }
    .top-col{ background:#fff; } /* 白カードは残すなら */
"""

PORTAL_HTML = r"""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Tech Trend</title>
  <style>
    {{ common_css }}
    {{ tech_extra_css }}
  </style>
</head>
<body>
  <h1>Daily Tech Trend</h1>
   <div class="small">Generated: {{ generated_at }}</div>

  <div class="card">
    <h2 style="margin:0 0 6px">技術動向ダイジェスト</h2>
    <div class="small">技術トピックの整理（カテゴリ別・注目・解説）</div>
    <a class="btn" href="./tech/index.html">技術動向を見る →</a>
  </div>

  <div class="card">
    <h2 style="margin:0 0 6px">ニュースダイジェスト</h2>
    <div class="small">提案の背景となる国内/世界ニュース</div>
    <a class="btn" href="./news/index.html">ニュースを見る →</a>
  </div>

  <script>
    // 旧URL互換：以前の /#topic-xxx を /tech/index.html#topic-xxx に寄せる
    if (location.hash && location.hash.startsWith("#topic-")) {
      location.replace("./tech/index.html" + location.hash);
    }
  </script>
</body>
</html>
"""

HTML = r"""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>技術動向ダイジェスト（Daily）</title>
  <style>
    {{ common_css }}
    {{ tech_extra_css }}
  </style>
</head>
<body>
  <h1>技術動向ダイジェスト</h1>
  <div class="nav">
    <a href="{{ nav_prefix }}tech/index.html" class="{{ 'active' if page=='tech' else '' }}">技術</a>
    <a href="{{ nav_prefix }}news/index.html" class="{{ 'active' if page=='news' else '' }}">ニュース</a>
  </div>

    <div class="summary-card">
    <div class="summary-title">今日の要点（技術動向）</div>
    <button id="closeFloating" class="close-floating" hidden>閉じる</button>
    <div class="summary-grid">
      <div class="summary-item">
        <div class="k">Generated (JST)</div>
        <div class="v">{{ meta.generated_at_jst }}</div>
      </div>
      <div class="summary-item">
        <div class="k">Runtime</div>
        <div class="v">{{ meta.runtime_sec }} sec</div>
      </div>
      <div class="summary-item">
        <div class="k">Articles</div>
        <div class="v">{{ meta.total_articles }} <span class="small">(new48h {{ meta.new_articles_48h }})</span></div>
      </div>
      <div class="summary-item">
        <div class="k">RSS Sources</div>
        <div class="v">{{ meta.rss_sources }}</div>
      </div>
    </div>
    <div class="small" style="margin-top:10px">
      <span class="badge">Tags</span>

      <div id="tagBar" class="tag-bar collapsed" style="margin-top:6px">
        <button class="btn btn-reset" type="button" onclick="clearTagFilter()">🔄 Reset</button>

        <label class="small tag-mode">
          <input type="checkbox" id="tagModeOr"> OR（どれか）
        </label>

        {% for tg, cnt in tag_list %}
          <button class="btn" type="button" data-tag-btn="{{ tg }}" onclick="toggleTag('{{ tg }}')">
            {{ tg }} ({{ cnt }})
          </button>
        {% endfor %}
      </div>

      <button id="tagMore" class="btn btn-more" type="button" style="margin-top:6px">＋ more</button>

    </div>
    <div id="tag-active" class="small" style="margin-top:6px; display:none;"></div>
    <div class="quick-controls">
      <input id="q" type="search" placeholder="Search title/summary..." />
      <label class="small">imp ≥
        <select id="impMin">
          <option value="0">0</option><option value="1">1</option><option value="2">2</option>
          <option value="3">3</option><option value="4">4</option><option value="5">5</option>
        </select>
      </label>
      <label class="small">recent ≥
        <select id="recentMin">
          <option value="-999">any</option>
          <option value="0">0</option><option value="1">1</option><option value="3">3</option>
          <option value="5">5</option><option value="10">10</option>
        </select>
      </label>
      <button class="btn" type="button" onclick="toggleAllCats()">Toggle categories</button>
    </div>
    <div id="filter-count" class="small" style="margin-top:6px; display:none;"></div>
    <div id="filter-hint" class="small" style="margin-top:4px; display:none;"></div>
  </div>

  <section class="top-zone">
    <div class="top-col">
      <h3>🌍Global Top 10（importance × recent）</h3>
      <ol class="top-list">
        {% for t in global_top %}
          <li class="topic-row"
              data-title="{{ t.title|e }}"
              data-summary="{{ (t.summary or '')|e }}"
              data-imp="{{ t.importance or 0 }}"
              data-recent="{{ t.recent or 0 }}"
              data-tags="{{ t.tags|default([])|join(',') }}">
            <span class="badge imp-{{ t.importance or 0 }}">imp {{ t.importance or 0 }}</span>
            {% if (t.recent or 0) > 0 %}<span class="badge {% if (t.recent or 0) >= 5 %}hot{% endif %}">48h +{{ t.recent }}</span>{% endif %}
            <a href="#topic-{{ t.id }}">{{ t.title }}</a>
            <span class="date">{{ fmt_date(t.date) }}</span>
            {% if t.category %}
              <span class="badge"><a href="#cat-{{ t.category }}">{{ cat_name.get(t.category, t.category) }}</a></span>
            {% endif %}
            {% if t.one_liner %}<div class="mini">{{ t.one_liner }}</div>{% endif %}
          </li>
        {% endfor %}
      </ol>
    </div>

    <div class="top-col">
      <h3>🔥Trending（48h増分）</h3>
      <ol class="top-list">
        {% for t in trending_top %}
          <li class="topic-row"
              data-title="{{ t.title|e }}"
              data-summary="{{ (t.summary or '')|e }}"
              data-imp="{{ t.importance or 0 }}"
              data-recent="{{ t.recent or 0 }}"
              data-tags="{{ t.tags|default([])|join(',') }}">
            <span class="badge imp-{{ t.importance or 0 }}">imp {{ t.importance or 0 }}</span>
            <span class="badge hot">48h +{{ t.recent }}</span>
            <a href="#topic-{{ t.id }}">{{ t.title }}</a>
            <span class="date">{{ fmt_date(t.date) }}</span>
            {% if t.category %}
              <span class="badge"><a href="#cat-{{ t.category }}">{{ cat_name.get(t.category, t.category) }}</a></span>
            {% endif %}
            {% if t.one_liner %}<div class="mini">{{ t.one_liner }}</div>{% endif %}

          </li>
        {% endfor %}
      </ol>
    </div>
  </section>

    {% for cat in categories %}
  <section class="category-section" id="cat-{{ cat.id }}">
    <div class="category-header">
      <h2 style="margin:0">{{ cat.name }} <span class="tag">{{ cat.id }}</span></h2>
      <button class="btn" type="button" onclick="toggleCat('{{ cat.id }}')">Toggle</button>
    </div>

    <div class="category-body">
      <!-- ここに既存の topbox と topics list をそのまま置く -->


    <div class="topbox">
      <h3>⭐注目TOP5（48h増分）</h3>
      {% if hot_by_cat.get(cat.id) %}
        <ul>
          {% for item in hot_by_cat[cat.id] %}
            <li>
              <a href="#topic-{{ item.id }}">{{ item.title }}</a>
              <span class="date">
                {{ fmt_date(item.date) }}
              </span>
              <span class="badge">48h +{{ item.recent }}</span>
              <span class="small">（累計 {{ item.articles }}）</span>
            </li>
          {% endfor %}
        </ul>
      {% else %}
        <div class="small">該当なし</div>
      {% endif %}
    </div>

    {% if topics_by_cat.get(cat.id) %}
      <ul>
        {% for t in topics_by_cat[cat.id] %}
           <li id="topic-{{ t.id }}" class="topic-row"
              data-title="{{ t.title|e }}"
              data-summary="{{ (t.summary or '')|e }}"
              data-imp="{{ t.importance or 0 }}"
              data-recent="{{ t.recent or 0 }}"
              data-tags="{{ t.tags|default([])|join(',') }}">
            <div>
              {% if t.url and t.url != "#" %}
                <a href="{{ t.url }}" target="_blank" rel="noopener">{{ t.title }}</a>
              {% else %}
                {{ t.title }}
              {% endif %}
              {% if t.date %}
                <span class="small">（{{ fmt_date(t.date) }}）</span>
              {% endif %}
              {% if t.importance is not none %}
                <span class="badge imp">重要度 {{ t.importance }}</span>
              {% endif %}

              {% if t.recent > 0 %}
                <span class="badge">48h +{{ t.recent }}</span>
              {% endif %}
              {% if t.tags and t.tags|length>0 %}
                <span class="small">
                  {% for tg in t.tags %}
                    <span class="badge">{{ tg }}</span>
                  {% endfor %}
                </span>
              {% endif %}
            </div>

            {% if t.summary or (t.key_points and t.key_points|length>0) or t.impact_guess or (t.next_actions and t.next_actions|length>0) %}
              <details class="insight">
                <summary class="small">要約・解説を表示</summary>

                {% if t.summary %}
                  <div><strong>要約</strong>：{{ t.summary }}</div>
                {% endif %}

                {% if t.key_points and t.key_points|length>0 %}
                  <ul class="kps">
                    {% for kp in t.key_points %}
                      <li>{{ kp }}</li>
                    {% endfor %}
                  </ul>
                {% endif %}

                {% if t.impact_guess %}
                  <div style="margin-top:6px;">
                    <strong>影響・示唆（推測含む）</strong>：{{ t.impact_guess }}
                  </div>
                {% endif %}

                {% if t.next_actions and t.next_actions|length>0 %}
                  <div style="margin-top:6px;"><strong>次アクション</strong></div>
                  <ul class="nas">
                    {% for na in t.next_actions %}
                      {% if na is mapping %}
                        <li>
                          <div><strong>{{ na.action }}</strong>
                            {% if na.priority %}<span class="badge">{{ na.priority }}</span>{% endif %}
                          </div>
                          {% if na.expected_outcome %}
                            <div class="small">→ {{ na.expected_outcome }}</div>
                          {% endif %}
                        </li>
                      {% else %}
                        <li>{{ na }}</li>
                      {% endif %}
                    {% endfor %}
                  </ul>
                {% endif %}

                {% if t.evidence_urls and t.evidence_urls|length>0 %}
                  <div class="small" style="margin-top:6px;">
                    根拠：
                    {% for u in t.evidence_urls %}
                      <a href="{{ u }}" target="_blank" rel="noopener">{{ u }}</a>{% if not loop.last %}, {% endif %}
                    {% endfor %}
                  </div>
                {% endif %}
              </details>
            {% endif %}

          </li>
        {% endfor %}
      </ul>
    {% else %}
      <div class="meta">該当なし</div>
    {% endif %}
      </div>
  </section>
  {% endfor %}
  {% if category in ["manufacturing","security","system","dev"] %}
  <div class="small" style="margin:4px 0 10px">
    関連ニュース：
    {% if category != "dev" %}
      <a href="../news/japan.html#{{ category if category != 'system' else 'policy' }}">国内</a> /
    {% endif %}
    <a href="../news/global.html#{{ category if category != 'system' else 'policy' }}">世界</a>
  </div>
{% endif %}
<script>
const selectedTags = new Set(); // 複数タグ
let tagMode = "AND";            // "AND" or "OR"
const btn = document.getElementById('closeFloating');

function setFloatingClose(open, closeFn){
  if(!open){ btn.hidden = true; btn.onclick = null; return; }
  btn.hidden = false;
  btn.onclick = closeFn;
}

function updateTagActiveView(){
  const box = document.getElementById('tag-active');
  if (!box) return;

  if (selectedTags.size === 0){
    box.style.display = 'none';
    box.textContent = '';
    return;
  }
  box.style.display = '';
  box.textContent = `tags: ${[...selectedTags].join(', ')} (${tagMode})`;
}

function toggleTag(tg){
  if (selectedTags.has(tg)) selectedTags.delete(tg);
  else selectedTags.add(tg);

  // ボタン見た目（active クラス）
  document.querySelectorAll(`[data-tag-btn="${tg}"]`).forEach(b=>{
    b.classList.toggle('active', selectedTags.has(tg));
  });

  updateTagActiveView();
  applyFilter();
}

function clearTagFilter(){
  selectedTags.clear();
  document.querySelectorAll('[data-tag-btn]').forEach(b=>b.classList.remove('active'));
  updateTagActiveView();
  applyFilter();
}

function applyFilter() {
  const q = (document.getElementById('q')?.value || '').toLowerCase();
  const impMin = parseInt(document.getElementById('impMin')?.value || '0', 10);
  const recentMin = parseInt(document.getElementById('recentMin')?.value || '-999', 10);

  const rows = document.querySelectorAll('.category-body .topic-row');
  let hit = 0;

  rows.forEach(el => {
    const title = (el.dataset.title || '').toLowerCase();
    const summary = (el.dataset.summary || '').toLowerCase();
    const imp = parseInt(el.dataset.imp || '0', 10);
    const recent = parseInt(el.dataset.recent || '0', 10);
    const tags = (el.dataset.tags || ''); // "EU規制,CBAM" など

    const hitQ = !q || title.includes(q) || summary.includes(q);
    const hitImp = imp >= impMin;
    const hitRecent = recent >= recentMin;
    const itemTags = tags.split(',').map(s=>s.trim()).filter(Boolean);

    let hitTag = true;
    if (selectedTags.size > 0){
      const sel = [...selectedTags];
      hitTag = (tagMode === "AND")
        ? sel.every(t => itemTags.includes(t))
        : sel.some(t => itemTags.includes(t));
    }

    const show = hitQ && hitImp && hitRecent && hitTag;
    el.style.display = show ? '' : 'none';
    if (show) hit++;
  });

  // ★ 件数表示
  const box = document.getElementById('filter-count');
  if (!box) return;

  const isFiltering = q || impMin > 0 || recentMin > -999;
  if (isFiltering) {
    box.textContent = `該当: ${hit}件 / 全${rows.length}件`;
    box.style.display = '';
  } else {
    box.style.display = 'none';
  }
  
    // ★ 0件時のヒント表示
  const hint = document.getElementById('filter-hint');
  if (!hint) return;

  if (isFiltering && hit === 0) {
    const tips = [];
    if (q) tips.push('検索語を短くする／別表現にする');
    if (impMin > 0) tips.push('重要度の下限を下げる');
    if (recentMin > -999) tips.push('recent の条件を緩める');
    tips.push('フィルタをすべてリセットする');

    hint.innerHTML = `該当なし。<strong>条件を緩めてください：</strong> ${tips.join('・')}`;
    hint.style.display = '';
  } else {
    hint.style.display = 'none';
  }
}


document.getElementById('q')?.addEventListener('input', applyFilter);
document.getElementById('impMin')?.addEventListener('change', applyFilter);
document.getElementById('recentMin')?.addEventListener('change', applyFilter);
document.getElementById('tagModeOr')?.addEventListener('change', (e) => {
  tagMode = e.target.checked ? "OR" : "AND";
  updateTagActiveView();
  applyFilter();
});

function toggleCat(id) {
  const sec = document.getElementById('cat-' + id);
  if (sec) sec.classList.toggle('collapsed');
}
let catsCollapsed = false;
function toggleAllCats() {
  catsCollapsed = !catsCollapsed;
  document.querySelectorAll('.category-section').forEach(sec => {
    sec.classList.toggle('collapsed', catsCollapsed);
  });
}

function openSectionFor(el){
  const sec = el.closest('.category-section');
  if (sec) sec.classList.remove('collapsed');
}

function ensureVisible(el){
  // フィルタ等で非表示なら一旦表示に戻す（最低限）
  if (el.style.display === 'none') el.style.display = '';
}

function scrollToTopic(hash){
  if (!hash || !hash.startsWith('#topic-')) return false;
  const el = document.querySelector(hash);
  if (!el) return false;

  // カテゴリを開く
  openSectionFor(el);
  ensureVisible(el);

  // details を自動で開く（あれば）
  const det = el.querySelector('details.insight');
  if (det && !det.open) det.open = true;

  requestAnimationFrame(() => {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  return true;
}


document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href^="#topic-"]');
  if (!a) return;

  const hash = a.getAttribute('href');
  if (scrollToTopic(hash)) {
    e.preventDefault();
    history.replaceState(null, '', hash);
  }
});

window.addEventListener('load', () => {
  if (location.hash) scrollToTopic(location.hash);
});

// Tag bar: More toggle（スマホ圧迫対策）
document.getElementById('tagMore')?.addEventListener('click', () => {
  const bar = document.getElementById('tagBar');
  if (!bar) return;
  bar.classList.toggle('collapsed');

  // ボタン文言切替（任意）
  const more = document.getElementById('tagMore');
  if (more) more.textContent = bar.classList.contains('collapsed') ? '＋ more' : '− less';
});


// 初期状態：カテゴリを折りたたむ（スマホ向け）
toggleAllCats();

</script>

</body>
</html>
"""
NEWS_HTML = r"""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    {{ common_css }}
    {{ tech_extra_css }}
  </style>
</head>
<body>
  <h1>{{ heading }}</h1>

  <div class="nav">
    <a href="{{ nav_prefix }}tech/index.html" class="{{ 'active' if page=='tech' else '' }}">技術</a>
    <a href="{{ nav_prefix }}news/index.html" class="{{ 'active' if page=='news' else '' }}">ニュース</a>
  </div>

  <!-- techと同じ：今日の要点 -->
  <div class="summary-card">
    <div class="summary-title">今日の要点（ニュース）</div>

    <div class="summary-grid">
      <div class="summary-item">
        <div class="k">Generated (JST)</div>
        <div class="v">{{ meta.generated_at_jst }}</div>
      </div>
      <div class="summary-item">
        <div class="k">News</div>
        <div class="v">{{ meta.total_articles }} <span class="small">(new48h {{ meta.new_articles_48h }})</span></div>
      </div>
      <div class="summary-item">
        <div class="k">Japan</div>
        <div class="v">{{ meta.jp_count }}</div>
      </div>
      <div class="summary-item">
        <div class="k">Global</div>
        <div class="v">{{ meta.global_count }}</div>
      </div>
    </div>

    <!-- techと同じ：タグバー -->
    <div class="small" style="margin-top:10px">
      <span class="badge">Tags</span>
      <div id="tagBar" class="tag-bar collapsed" style="margin-top:6px">
        <button class="btn btn-reset" type="button" onclick="clearTagFilter()">🔄 Reset</button>
        <label class="small tag-mode">
          <input type="checkbox" id="tagModeOr"> OR（どれか）
        </label>
        {% for tg, cnt in tag_list %}
          <button class="btn" type="button" data-tag-btn="{{ tg }}" onclick="toggleTag('{{ tg }}')">
            {{ tg }} ({{ cnt }})
          </button>
        {% endfor %}
      </div>
      <button id="tagMore" class="btn btn-more" type="button" style="margin-top:6px">＋ more</button>
    </div>

    <div id="tag-active" class="small" style="margin-top:6px; display:none;"></div>

    <!-- techと同じ：検索（imp/recentはnewsでは使わないので固定） -->
    <div class="quick-controls">
      <input id="q" type="search" placeholder="Search title/summary..." />
      <input id="impMin" type="hidden" value="0" />
      <input id="recentMin" type="hidden" value="-999" />
      <button class="btn" type="button" onclick="toggleAllCats()">Toggle categories</button>
    </div>
    <div id="filter-count" class="small" style="margin-top:6px; display:none;"></div>
    <div id="filter-hint" class="small" style="margin-top:4px; display:none;"></div>
  </div>

  <!-- techと同じ：Top-zone 2カラム -->
  <section class="top-zone">
    <div class="top-col">
      <h3>🇯🇵 Japan Top 10（latest）</h3>
      <ol class="top-list">
        {% for it in jp_top %}
          <li class="topic-row"
              data-title="{{ it.title|e }}"
              data-summary="{{ (it.summary or '')|e }}"
              data-imp="0"
              data-recent="0"
              data-tags="{{ it.tags|default([])|join(',') }}">
            <span class="badge">{{ it.category }}</span>
            <a class="topic-link" href="{{ it.url }}" target="_blank" rel="noopener">{{ it.title }}</a>
            <span class="date">{{ it.dt_jst }}</span>
            {% if it.source %}<div class="mini">{{ it.source }}</div>{% endif %}
          </li>
        {% endfor %}
      </ol>
    </div>

    <div class="top-col">
      <h3>🌍 Global Top 10（latest）</h3>
      <ol class="top-list">
        {% for it in global_top %}
          <li class="topic-row"
              data-title="{{ it.title|e }}"
              data-summary="{{ (it.summary or '')|e }}"
              data-imp="0"
              data-recent="0"
              data-tags="{{ it.tags|default([])|join(',') }}">
            <span class="badge">{{ it.category }}</span>
            <a class="topic-link" href="{{ it.url }}" target="_blank" rel="noopener">{{ it.title }}</a>
            <span class="date">{{ it.dt_jst }}</span>
            {% if it.source %}<div class="mini">{{ it.source }}</div>{% endif %}
          </li>
        {% endfor %}
      </ol>
    </div>
  </section>

  <!-- techと同じ：カテゴリ（折りたたみ） -->
 {% for sec in sections %}
  <section class="category-section" id="cat-{{ sec.anchor }}">
    <div class="category-header">
      <h2>
        {{ sec.title }}
        <span class="badge">{{ sec.count }}</span>
        {% if sec.recent48 is defined %}
          <span class="badge">+{{ sec.recent48 }}/48h</span>
        {% endif %}
      </h2>
    </div>

    <div class="category-body">
      <ul>
        {% for it in sec.rows %}
          <li class="topic-row">
            <a href="{{ it.url }}" target="_blank">{{ it.title }}</a>
            <div class="small">{{ it.source }} / {{ it.dt_jst }}</div>
          </li>
        {% endfor %}
      </ul>
    </div>
  </section>
  {% endfor %}


<script>
/* techのJSをそのまま使う（newsではimp/recentはhidden固定） */
const selectedTags = new Set();
let tagMode = "AND";

function updateTagActiveView(){
  const box = document.getElementById('tag-active');
  if (!box) return;
  if (selectedTags.size === 0){
    box.style.display = 'none';
    box.textContent = '';
    return;
  }
  box.style.display = '';
  box.textContent = `tags: ${[...selectedTags].join(', ')} (${tagMode})`;
}

function toggleTag(tg){
  if (selectedTags.has(tg)) selectedTags.delete(tg);
  else selectedTags.add(tg);
  document.querySelectorAll(`[data-tag-btn="${tg}"]`).forEach(b=>{
    b.classList.toggle('active', selectedTags.has(tg));
  });
  updateTagActiveView();
  applyFilter();
}

function clearTagFilter(){
  selectedTags.clear();
  document.querySelectorAll('[data-tag-btn]').forEach(b=>b.classList.remove('active'));
  updateTagActiveView();
  applyFilter();
}

function applyFilter() {
  const q = (document.getElementById('q')?.value || '').toLowerCase();
  const rows = document.querySelectorAll('.category-body .topic-row, .top-zone .topic-row');
  let hit = 0;

  rows.forEach(el => {
    const title = (el.dataset.title || '').toLowerCase();
    const summary = (el.dataset.summary || '').toLowerCase();
    const tags = (el.dataset.tags || '');
    const hitQ = !q || title.includes(q) || summary.includes(q);

    const itemTags = tags.split(',').map(s=>s.trim()).filter(Boolean);
    let hitTag = true;
    if (selectedTags.size > 0){
      const sel = [...selectedTags];
      hitTag = (tagMode === "AND")
        ? sel.every(t => itemTags.includes(t))
        : sel.some(t => itemTags.includes(t));
    }

    const show = hitQ && hitTag;
    el.style.display = show ? '' : 'none';
    if (show) hit++;
  });

  const box = document.getElementById('filter-count');
  if (box){
    const isFiltering = q || selectedTags.size > 0;
    if (isFiltering) {
      box.textContent = `該当: ${hit}件`;
      box.style.display = '';
    } else {
      box.style.display = 'none';
    }
  }
}

document.getElementById('q')?.addEventListener('input', applyFilter);
document.getElementById('tagModeOr')?.addEventListener('change', (e) => {
  tagMode = e.target.checked ? "OR" : "AND";
  updateTagActiveView();
  applyFilter();
});

function toggleCat(id) {
  const sec = document.getElementById('cat-' + id);
  if (sec) sec.classList.toggle('collapsed');
}
let catsCollapsed = false;
function toggleAllCats() {
  catsCollapsed = !catsCollapsed;
  document.querySelectorAll('.category-section').forEach(sec => {
    sec.classList.toggle('collapsed', catsCollapsed);
  });
}

document.getElementById('tagMore')?.addEventListener('click', () => {
  const bar = document.getElementById('tagBar');
  if (!bar) return;
  bar.classList.toggle('collapsed');
  const more = document.getElementById('tagMore');
  if (more) more.textContent = bar.classList.contains('collapsed') ? '＋ more' : '− less';
});

</script>

</body>
</html>
"""



NAME_MAP = {
    "system": "システム",
    "manufacturing": "製造",
    "security": "セキュリティ",
    "ai": "AI",
    "dev": "開発",
    "other": "その他",
}
from typing import Any, List

def _safe_json_list(s: str | None) -> List[str]:
    """list[str] を想定（key_points / evidence_urls 用）"""
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            out = []
            for x in v:
                if x is None:
                    continue
                out.append(str(x))
            return out
    except Exception:
        pass
    return []

def _safe_json_any_list(s: str | None) -> List[Any]:
    """list[Any] を想定（next_actions が dict 配列になる想定）"""
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except Exception:
        return []
def load_categories_from_yaml() -> List[Dict[str, str]]:
    try:
        with open("src/sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cats = cfg.get("categories")
        if isinstance(cats, list):
            out = []
            for c in cats:
                if isinstance(c, dict) and "id" in c and "name" in c:
                    out.append({"id": str(c["id"]), "name": str(c["name"])})
            return out
    except Exception:
        return []
    return []

def build_categories_fallback(cur) -> List[Dict[str, str]]:
    """
    YAMLが無い場合でも表示が空にならないよう、DBからカテゴリを推定する。
    """
    cur.execute("SELECT DISTINCT category FROM topics WHERE category IS NOT NULL AND category != ''")
    cats = [r[0] for r in cur.fetchall()]
    if not cats:
        cur.execute("SELECT DISTINCT category FROM articles WHERE category IS NOT NULL AND category != ''")
        cats = [r[0] for r in cur.fetchall()]
    if not cats:
        cats = ["other"]
    return [{"id": c, "name": NAME_MAP.get(c, c)} for c in cats]


def ensure_category_coverage(cur, categories: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    YAMLのカテゴリに存在しないカテゴリがDBにある場合でも、表示対象に追加する。
    """
    ids = {c["id"] for c in categories}
    cur.execute("SELECT DISTINCT category FROM topics WHERE category IS NOT NULL AND category != ''")
    db_cats = [r[0] for r in cur.fetchall()]
    for c in db_cats:
        if c not in ids:
            categories.append({"id": c, "name": NAME_MAP.get(c, c)})
            ids.add(c)
    if not categories:
        categories = [{"id": "other", "name": NAME_MAP["other"]}]
    return categories

def render_news_pages(out_dir: Path, generated_at: str, cur) -> None:
    news_dir = out_dir / "news"
    news_dir.mkdir(exist_ok=True)
    
    now = datetime.now()
    cutoff_48h_str = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

    # 1) Japan / Global はカテゴリ見出しで分割
    sections_jp = render_news_region_page(cur, "jp", limit_each=30, cutoff_dt=cutoff_48h_str)
    sections_gl = render_news_region_page(cur, "global", limit_each=30, cutoff_dt=cutoff_48h_str)

        # --- techと同じ構成にするためのnews用データ ---
    # Top（最新）
    jp_top = fetch_news_articles(cur, "jp", 10)
    gl_top = fetch_news_articles(cur, "global", 10)

    def to_top_items(rows, region_label):
        out = []
        for r in rows:
            # fetch_news_articles(region指定) の戻り: title,url,source,category,dt
            title, url, source, category, dt = r
            out.append({
                "title": title,
                "url": url,
                "source": source,
                "category": category or "other",
                "region": region_label,
                "dt": dt,
                "dt_jst": fmt_date(dt),
                "tags": [region_label, (category or "other"), source] if source else [region_label, (category or "other")],
                "recent": 0,
                "importance": 0,
                "summary": f"{source} / {fmt_date(dt)}",
            })
        return out

    jp_top_items = to_top_items(jp_top, "jp")
    gl_top_items = to_top_items(gl_top, "global")

    # Tag list（source中心 + category/regionも混ぜる）
    tag_count = {}
    def add_tags_from_sections(sections, region_label):
        for sec in sections:
            cat = sec.get("anchor") or "other"
            for it in sec.get("rows", []):
                src = it.get("source") or ""
                tags = [region_label, cat]
                if src:
                    tags.append(src)
                for tg in tags:
                    tag_count[tg] = tag_count.get(tg, 0) + 1

    add_tags_from_sections(sections_jp, "jp")
    add_tags_from_sections(sections_gl, "global")
    tag_list_news = sorted(tag_count.items(), key=lambda x: (-x[1], x[0]))[:50]

    # meta（summary-cardに表示する）
    news_total = sum(s["count"] for s in sections_jp) + sum(s["count"] for s in sections_gl)
    news_new48 = sum(s.get("recent48", 0) for s in sections_jp) + sum(s.get("recent48", 0) for s in sections_gl)
    meta_news = {
        "generated_at_jst": generated_at,
        "total_articles": news_total,
        "new_articles_48h": news_new48,
        "jp_count": sum(s["count"] for s in sections_jp),
        "global_count": sum(s["count"] for s in sections_gl),
    }


    # 2) 総合は「全国」「世界」の2セクションにしてまず成立させる（最小）
    #    ※将来、カテゴリ横断にしたくなったらここを拡張
    def flatten(sections, limit=999):
        out = []
        for sec in sections:
            out.extend(sec.get("rows", []))   # ★ rows
        return out[:limit]


    sections_all = [
        {
            "anchor": "jp",
            "title": "🇯🇵 国内ニュース",
            "count": sum(s["count"] for s in sections_jp),
            "recent48": sum(s.get("recent48", 0) for s in sections_jp),
            "rows": flatten(sections_jp, 999),
        },
        {
            "anchor": "global",
            "title": "🌍 世界ニュース",
            "count": sum(s["count"] for s in sections_gl),
            "recent48": sum(s.get("recent48", 0) for s in sections_gl),
            "rows": flatten(sections_gl, 999),
        },
    ]


    pages = [
        ("news",   "ニュースダイジェスト（総合）", "ニュースダイジェスト（総合）", sections_all, "index.html"),
    ]

    for page, title, heading, sections, filename in pages:
        (news_dir / filename).write_text(
            Template(NEWS_HTML).render(
                common_css=COMMON_CSS,
                tech_extra_css=TECH_EXTRA_CSS,

                page=page,
                nav_prefix="../", 
                title=title,
                heading=heading,
                generated_at=generated_at,

                meta=meta_news,
                tag_list=tag_list_news,
                jp_top=jp_top_items,
                global_top=gl_top_items,

                sections=sections,
            ),

            encoding="utf-8",
        )

NEWS_SECTIONS = [
    ("news",          "一般ニュース（未分類）"),
    ("manufacturing", "製造業・鉄鋼（現場/プラント）"),
    ("policy",        "政策・制度・規制"),
    ("security",      "セキュリティ/事故"),
    ("industry",      "産業・市況・サプライチェーン"),
    ("company",       "企業動向（提携/投資/決算）"),
    ("other",         "その他"),
]

NEWS_SECTION_POINTS = {
    "news": "社会・産業全体の動き。技術導入や投資判断の背景として確認。",
    "manufacturing": "現場改善・省人化・品質保証に直結。設備更新やDX提案の根拠。",
    "policy": "制度変更・規制強化の兆し。中長期のIT投資・対応計画に影響。",
    "security": "事業継続・リスク管理の観点。対策投資の説明材料。",
    "industry": "市況・サプライチェーン変化。需要予測やシステム刷新の背景。",
    "company": "競合・先行事例。顧客への『他社事例』として利用可能。",
    "other": "個別要因。将来の技術動向と結び付けて整理。",
}

def render_news_region_page(cur, region, limit_each=30, cutoff_dt=None):
    sections = []
    for cat, title in NEWS_SECTIONS:
        rows = fetch_news_articles_by_category(cur, region, cat, limit_each)
        items = [{
            "title": r[0], "url": r[1], "source": r[2],
            "dt_jst": fmt_date(r[4]),
        } for r in rows]

        recent48 = 0
        if cutoff_dt:
            recent48 = count_news_recent_48h(cur, region, cat, cutoff_dt)

        TECH_LINK_MAP = {
            "manufacturing": ("manufacturing", "製造業・現場DX"),
            "security": ("security", "セキュリティ"),
            "policy": ("system", "制度・ガバナンス"),
            "industry": ("system", "基幹・業務システム"),
            "company": ("dev", "開発・内製化"),
        }

        tech_link = TECH_LINK_MAP.get(cat)

        sections.append({
            "title": title,
            "count": len(items),
            "recent48": recent48,
            "point": NEWS_SECTION_POINTS.get(cat, ""),
            "rows": items,
            "anchor": cat,
            "tech_link": tech_link[0] if tech_link else None,
            "tech_label": tech_link[1] if tech_link else None,
        })

    return sections


def fetch_news_articles(cur, region: str, limit: int = 60):
    # region: ""(all) / "jp" / "global"
    if region:
        cur.execute(
            """
            SELECT
              COALESCE(NULLIF(title,''), url) AS title,
              url,
              COALESCE(NULLIF(source,''), '') AS source,
              COALESCE(NULLIF(category,''), '') AS category,
              COALESCE(NULLIF(published_at,''), fetched_at) AS dt
            FROM articles
            WHERE kind='news' AND region=?
            ORDER BY
              datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) DESC,
              id DESC
            LIMIT ?
            """,
            (region, limit),
        )
    else:
        cur.execute(
            """
            SELECT
              COALESCE(NULLIF(title,''), url) AS title,
              url,
              COALESCE(NULLIF(source,''), '') AS source,
              COALESCE(NULLIF(category,''), '') AS category,
              COALESCE(NULLIF(region,''), '') AS region,
              COALESCE(NULLIF(published_at,''), fetched_at) AS dt
            FROM articles
            WHERE kind='news'
            ORDER BY
              datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) DESC,
              id DESC
            LIMIT ?
            """,
            (limit,),
        )

    return cur.fetchall()

def fetch_news_articles_by_category(cur, region: str, category: str, limit: int = 40):
    cur.execute(
        """
        SELECT
          COALESCE(NULLIF(title,''), url) AS title,
          url,
          COALESCE(NULLIF(source,''), '') AS source,
          COALESCE(NULLIF(category,''), '') AS category,
          COALESCE(NULLIF(published_at,''), fetched_at) AS dt
        FROM articles
        WHERE kind='news'
          AND region=?
          AND COALESCE(NULLIF(category,''), 'other')=?
        ORDER BY
          datetime(
            substr(
              replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
              1, 19
            )
          ) DESC,
          id DESC
        LIMIT ?
        """,
        (region, category, limit),
    )
    return cur.fetchall()

def count_news_recent_48h(cur, region: str, category: str, cutoff_dt: str) -> int:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE kind='news'
          AND region=?
          AND COALESCE(NULLIF(category,''), 'other')=?
          AND datetime(
                substr(
                  replace(replace(COALESCE(NULLIF(published_at,''), fetched_at),'T',' '),'+00:00',''),
                  1, 19
                )
              ) >= datetime(?)
        """,
        (region, category, cutoff_dt),
    )
    return int(cur.fetchone()[0] or 0)


def main():
    t0 = _now_sec()
    print("[TIME] step=render start")

    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)

    conn = connect()
    cur = conn.cursor()
    # categories: YAML -> DB -> other
    categories = load_categories_from_yaml()
    if not categories:
        categories = build_categories_fallback(cur)
    categories = ensure_category_coverage(cur, categories)

    # ★ ここを追加（完全決定順）
    categories = sorted(categories, key=lambda c: c["id"])
    # カテゴリID → 表示名マップ（Global/Trending表示用）
    cat_name = {c["id"]: c["name"] for c in categories}

    LIMIT_PER_CAT = 20
    HOT_TOP_N = 5

    topics_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    hot_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    cutoff_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")

    for cat in categories:
        cat_id = cat["id"]

        # (A) 注目TOP5（48h増分、published_atベース）
        if cat_id == "other":
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS ttitle,
                  COUNT(ta.article_id) AS total_count,
                  SUM(
                    CASE
                      WHEN datetime(
                        substr(
                          replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                          1, 19
                        )
                      ) >= datetime(?) THEN 1
                      ELSE 0
                    END
                  ) AS recent_count,
                  MAX(
                    datetime(
                      substr(
                        replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                        1, 19
                      )
                    )
                  ) AS article_date
                FROM topics t
                JOIN topic_articles ta ON ta.topic_id = t.id
                JOIN articles a ON a.id = ta.article_id
                WHERE t.category IS NULL OR t.category = ''
                GROUP BY t.id
                HAVING recent_count > 0
                ORDER BY recent_count DESC, total_count DESC, t.id DESC
                LIMIT ?
                """,
                (cutoff_48h, HOT_TOP_N),
            )


        else:
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS ttitle,
                  COUNT(ta.article_id) AS total_count,
                  SUM(
                    CASE
                      WHEN datetime(
                        substr(
                          replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                          1, 19
                        )
                      ) >= datetime(?) THEN 1
                      ELSE 0
                    END
                  ) AS recent_count,
                  MAX(
                    datetime(
                      substr(
                        replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),
                        1, 19
                      )
                    )
                  ) AS article_date
                FROM topics t
                JOIN topic_articles ta ON ta.topic_id = t.id
                JOIN articles a ON a.id = ta.article_id
                WHERE t.category = ?
                GROUP BY t.id
                HAVING recent_count > 0
                ORDER BY recent_count DESC, total_count DESC, t.id DESC
                LIMIT ?
                """,
                (cutoff_48h, cat_id, HOT_TOP_N),
            )


        rows = cur.fetchall()
        hot_by_cat[cat_id] = [
            {"id": tid, "title": title, "articles": int(total), "recent": int(recent),"date": article_date}
            for (tid, title, total, recent, article_date) in rows
        ]

        # ★ 注目TOP5の並びも完全決定（揺れ防止）
        hot_by_cat[cat_id] = sorted(
            hot_by_cat[cat_id],
            key=lambda x: (-x["recent"], -x["articles"], x["id"]),
        )

        # (B) 一覧（topics + insights + 代表URL + 48h増分）
        if cat_id == "other":
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS title,
                  (
                      SELECT a2.url
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                          CASE
                            WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                            ELSE 1
                          END,
                          datetime(a2.fetched_at) DESC,
                          datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                          a2.url ASC
                        LIMIT 1

                    ) AS url,
                    (
                      SELECT COALESCE(
                        NULLIF(a2.published_at,''),
                        a2.fetched_at
                      )
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                        CASE
                          WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                          ELSE 1
                        END,
                        datetime(a2.fetched_at) DESC,
                        datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                        a2.url ASC
                      LIMIT 1
                    ) AS article_date,
                  (
                      SELECT COALESCE(SUM(
                        CASE
                          WHEN datetime(
                            substr(
                              replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                              1, 19
                            )
                          ) >= datetime(?) THEN 1
                          ELSE 0
                        END
                      ), 0)
                      FROM topic_articles ta3
                      JOIN articles a3 ON a3.id = ta3.article_id
                      WHERE ta3.topic_id = t.id
                    ) AS recent,
                  i.importance,
                  i.summary,
                  i.key_points,
                  i.impact_guess,
                  i.next_actions,
                  i.evidence_urls,
                  i.tags
                FROM topics t
                LEFT JOIN topic_insights i ON i.topic_id = t.id
                WHERE t.category IS NULL OR t.category = ''
                ORDER BY
                  COALESCE(i.importance, 0) DESC,
                  COALESCE(recent, 0) DESC,
                  t.id DESC
                LIMIT ?
                """,
                (cutoff_48h, LIMIT_PER_CAT),
            )
        else:
            cur.execute(
                """
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS title,
                  (
                      SELECT a2.url
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                          CASE
                            WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                            ELSE 1
                          END,
                          datetime(a2.fetched_at) DESC,
                          datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                          a2.url ASC
                        LIMIT 1
                    ) AS url,
                    (
                      SELECT COALESCE(
                        NULLIF(a2.published_at,''),
                        a2.fetched_at
                      )
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                        CASE
                          WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                          ELSE 1
                        END,
                        datetime(a2.fetched_at) DESC,
                        datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                        a2.url ASC
                      LIMIT 1
                    ) AS article_date,
                  (
                      SELECT COALESCE(SUM(
                        CASE
                          WHEN datetime(
                            substr(
                              replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                              1, 19
                            )
                          ) >= datetime(?) THEN 1
                          ELSE 0
                        END
                      ), 0)
                      FROM topic_articles ta3
                      JOIN articles a3 ON a3.id = ta3.article_id
                      WHERE ta3.topic_id = t.id
                    ) AS recent,

                  i.importance,
                  i.summary,
                  i.key_points,
                  i.impact_guess,
                  i.next_actions,
                  i.evidence_urls,
                  i.tags
                FROM topics t
                LEFT JOIN topic_insights i ON i.topic_id = t.id
                WHERE t.category = ?
                ORDER BY
                  COALESCE(i.importance, 0) DESC,
                  COALESCE(recent, 0) DESC,
                  t.id DESC
                LIMIT ?
                """,
                (cutoff_48h, cat_id, LIMIT_PER_CAT),
            )


        rows = cur.fetchall()
        items: List[Dict[str, Any]] = []
        for r in rows:
            tid, title, url, article_date, recent, importance, summary, key_points, impact_guess, next_actions, evidence_urls, tags = r
            items.append(
                {
                    "id": tid,
                    "title": title,  # ← ここはSQLで title_ja 優先済み
                    "url": url or "#",
                    "date": article_date,
                    "recent": int(recent or 0),
                    "importance": int(importance) if importance is not None else None,
                    "summary": summary or "",
                    "key_points": _safe_json_list(key_points),
                    "impact_guess": impact_guess or "",
                    "next_actions": _safe_json_any_list(next_actions),
                    "evidence_urls": _safe_json_list(evidence_urls),
                    "tags": _safe_json_list(tags),
                }
            )

        # トピック順を完全決定（最後の揺れ防止）
        items = sorted(
            items,
            key=lambda x: (
                -(x["importance"] or 0),
                -(x["recent"] or 0),
                x["id"]
            )
        )

        # ===== A: 確実対応：注目TOP5を詳細リストにも必ず混ぜる =====
        hot_ids = [x["id"] for x in hot_by_cat.get(cat_id, [])]
        item_ids = {x["id"] for x in items}
        missing_ids = [tid for tid in hot_ids if tid not in item_ids]

        if missing_ids:
            # IN句プレースホルダを生成
            placeholders = ",".join(["?"] * len(missing_ids))

            if cat_id == "other":
                sql_missing = f"""
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS title,
                  (
                      SELECT a2.url
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                          CASE
                            WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                            ELSE 1
                          END,
                          datetime(a2.fetched_at) DESC,
                          datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                          a2.url ASC
                        LIMIT 1
                    ) AS url,
                    (
                      SELECT COALESCE(
                        NULLIF(a2.published_at,''),
                        a2.fetched_at
                      )
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                        CASE
                          WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                          ELSE 1
                        END,
                        datetime(a2.fetched_at) DESC,
                        datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                        a2.url ASC
                      LIMIT 1
                    ) AS article_date,
                  (
                      SELECT COALESCE(SUM(
                        CASE
                          WHEN datetime(
                            substr(
                              replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                              1, 19
                            )
                          ) >= datetime(?) THEN 1
                          ELSE 0
                        END
                      ), 0)
                      FROM topic_articles ta3
                      JOIN articles a3 ON a3.id = ta3.article_id
                      WHERE ta3.topic_id = t.id
                    ) AS recent,
                  i.importance,
                  i.summary,
                  i.key_points,
                  i.impact_guess,
                  i.next_actions,
                  i.evidence_urls,
                  i.tags
                FROM topics t
                LEFT JOIN topic_insights i ON i.topic_id = t.id
                WHERE (t.category IS NULL OR t.category = '')
                  AND t.id IN ({placeholders})
                """
                params = [cutoff_48h, *missing_ids]
            else:
                sql_missing = f"""
                SELECT
                  t.id,
                  COALESCE(t.title_ja, t.title) AS title,
                  (
                      SELECT a2.url
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                          CASE
                            WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                            ELSE 1
                          END,
                          datetime(a2.fetched_at) DESC,
                          datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                          a2.url ASC
                        LIMIT 1
                    ) AS url,
                    (
                      SELECT COALESCE(
                        NULLIF(a2.published_at,''),
                        a2.fetched_at
                      )
                      FROM topic_articles ta2
                      JOIN articles a2 ON a2.id = ta2.article_id
                      WHERE ta2.topic_id = t.id
                      ORDER BY
                        CASE
                          WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                          ELSE 1
                        END,
                        datetime(a2.fetched_at) DESC,
                        datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
                        a2.url ASC
                      LIMIT 1
                    ) AS article_date,
                  (
                      SELECT COALESCE(SUM(
                        CASE
                          WHEN datetime(
                            substr(
                              replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                              1, 19
                            )
                          ) >= datetime(?) THEN 1
                          ELSE 0
                        END
                      ), 0)
                      FROM topic_articles ta3
                      JOIN articles a3 ON a3.id = ta3.article_id
                      WHERE ta3.topic_id = t.id
                    ) AS recent,
                  i.importance,
                  i.summary,
                  i.key_points,
                  i.impact_guess,
                  i.next_actions,
                  i.evidence_urls,
                  i.tags
                FROM topics t
                LEFT JOIN topic_insights i ON i.topic_id = t.id
                WHERE t.category = ?
                  AND t.id IN ({placeholders})
                """
                params = [cutoff_48h, cat_id, *missing_ids]

            cur.execute(sql_missing, params)
            for r in cur.fetchall():
                tid, title, url, article_date, recent, importance, summary, key_points, impact_guess, next_actions, evidence_urls, tags = r
                items.append(
                    {
                        "id": tid,
                        "title": title,
                        "url": url or "#",
                        "date": article_date,
                        "recent": int(recent or 0),
                        "importance": int(importance) if importance is not None else None,
                        "summary": summary or "",
                        "key_points": _safe_json_list(key_points),
                        "impact_guess": impact_guess or "",
                        "next_actions": _safe_json_any_list(next_actions),
                        "evidence_urls": _safe_json_list(evidence_urls),
                        "tags": _safe_json_list(tags),
                    }
                )

            # 再ソート（表示順の規則を維持）
            items = sorted(
                items,
                key=lambda x: (
                    -(x["importance"] or 0),
                    -(x["recent"] or 0),
                    x["id"]
                )
            )

            # 表示件数を LIMIT_PER_CAT に戻す（ただし注目TOP5は落とさない）
            hot_set = set(hot_ids)
            kept = []
            for it in items:
                if len(kept) >= LIMIT_PER_CAT and it["id"] not in hot_set:
                    continue
                kept.append(it)
            items = kept
        # ===== A: 確実対応ここまで =====


        topics_by_cat[cat_id] = items

        all_tags = {}
        for cat_id, items in topics_by_cat.items():
            for t in items:
                for tg in (t.get("tags") or []):
                    all_tags[tg] = all_tags.get(tg, 0) + 1
        tag_list = sorted(all_tags.items(), key=lambda x: (-x[1], x[0]))[:50]  # 上位50など
        # --- UX改善①: 上部サマリー用meta ---
    runtime_sec = int(os.environ.get("RUNTIME_SEC", "0") or "0")

    # 記事総数（最終採用＝articlesテーブル件数）
    cur.execute("SELECT COUNT(*) FROM articles")
    total_articles = int(cur.fetchone()[0] or 0)

    # 新規記事数（48h）
    cur.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE datetime(COALESCE(NULLIF(published_at,''), fetched_at)) >= datetime(?)
        """,
        (cutoff_48h,),
    )
    new_articles_48h = int(cur.fetchone()[0] or 0)

    # RSS数（sources.yamlから拾える範囲でカウント。取れなければ0）
    rss_sources = 0
    try:
        with open("src/sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cats = cfg.get("categories") or []
        if isinstance(cats, list):
            for c in cats:
                if isinstance(c, dict):
                    srcs = c.get("sources") or c.get("feeds") or []
                    if isinstance(srcs, list):
                        for s in srcs:
                            if isinstance(s, str) and s.startswith("http"):
                                rss_sources += 1
                            elif isinstance(s, dict) and isinstance(s.get("url"), str):
                                rss_sources += 1
        # fallback: top-level sources
        if rss_sources == 0:
            srcs = cfg.get("sources") or cfg.get("feeds") or []
            if isinstance(srcs, list):
                for s in srcs:
                    if isinstance(s, str) and s.startswith("http"):
                        rss_sources += 1
                    elif isinstance(s, dict) and isinstance(s.get("url"), str):
                        rss_sources += 1
    except Exception:
        rss_sources = 0

    meta = {
        "generated_at_jst": None,  # 後で入れる
        "runtime_sec": runtime_sec,
        "total_articles": total_articles,
        "new_articles_48h": new_articles_48h,
        "rss_sources": rss_sources,
    }

    # --- UX改善①: カテゴリ横断TOP ---
    # Global Top 10: importance desc, recent desc, id asc（完全決定）
    TECH_CATS = {"ai", "dev", "security", "system", "manufacturing", "cloud", "data"}  # 必要に応じて調整
    tech_cat_ids = [c.get("id") for c in categories if c.get("id") in TECH_CATS]
    # もし TECH_CATS 側が空なら、other以外全部を技術扱いにフォールバック
    if not tech_cat_ids:
        tech_cat_ids = [c.get("id") for c in categories if c.get("id") and c.get("id") != "other"]

    ph = ",".join(["?"] * len(tech_cat_ids))
    cur.execute(
    f"""
    SELECT
      t.id,
      COALESCE(t.title_ja, t.title) AS title,
      COALESCE(NULLIF(t.category,''), 'other') AS category,
      (
        SELECT a2.url
        FROM topic_articles ta2
        JOIN articles a2 ON a2.id = ta2.article_id
        WHERE ta2.topic_id = t.id
        ORDER BY a2.id DESC
        LIMIT 1
      ) AS url,
      (
        SELECT COALESCE(
          NULLIF(a2.published_at,''),
          a2.fetched_at
        )
        FROM topic_articles ta2
        JOIN articles a2 ON a2.id = ta2.article_id
        WHERE ta2.topic_id = t.id
        ORDER BY
          CASE
            WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
            ELSE 1
          END,
          datetime(a2.fetched_at) DESC,
          datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
          a2.url ASC
        LIMIT 1
      ) AS article_date,
      (
        SELECT COALESCE(SUM(
          CASE
            WHEN datetime(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at)) >= datetime(?) THEN 1
            ELSE 0
          END
        ), 0)
        FROM topic_articles ta3
        JOIN articles a3 ON a3.id = ta3.article_id
        WHERE ta3.topic_id = t.id
      ) AS recent,
      i.importance,
      i.summary,
      i.tags
    FROM topics t
    LEFT JOIN topic_insights i ON i.topic_id = t.id
    ORDER BY
      CASE
        WHEN COALESCE(NULLIF(t.category,''), 'other') IN ({ph}) THEN 1
        ELSE 0
      END DESC,
      COALESCE(i.importance,0) DESC,
      COALESCE(recent,0) DESC,
      t.id ASC
    LIMIT 10
    """,
    (cutoff_48h, *tech_cat_ids),
)

    global_top = []
    for tid, title, category, url, article_date,recent, importance, summary, tags in cur.fetchall():
        global_top.append({
            "id": tid,
            "title": title,
            "category": category,   # ★追加
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "one_liner": "",  # 今は空でOK（後で短文化したければ追加）
            "date": article_date,
        })

    # Trending Top 10: recent desc, importance desc, id asc（完全決定）
    cur.execute(
        """
        SELECT
          t.id,
          COALESCE(t.title_ja, t.title) AS title,
          COALESCE(NULLIF(t.category,''), 'other') AS category,
          (
            SELECT a2.url
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY a2.id DESC
            LIMIT 1
          ) AS url,
          (
            SELECT COALESCE(
              NULLIF(a2.published_at,''),
              a2.fetched_at
            )
            FROM topic_articles ta2
            JOIN articles a2 ON a2.id = ta2.article_id
            WHERE ta2.topic_id = t.id
            ORDER BY
              CASE
                WHEN COALESCE(NULLIF(a2.content,''), '') != '' THEN 0
                ELSE 1
              END,
              datetime(a2.fetched_at) DESC,
              datetime(COALESCE(NULLIF(a2.published_at,''), a2.fetched_at)) DESC,
              a2.url ASC
            LIMIT 1
          ) AS article_date,
          (
            SELECT COALESCE(SUM(
              CASE
                WHEN datetime(
                  substr(
                    replace(replace(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at),'T',' '),'+00:00',''),
                    1, 19
                  )
                ) >= datetime(?) THEN 1
              END
            ), 0)
            FROM topic_articles ta3
            JOIN articles a3 ON a3.id = ta3.article_id
            WHERE ta3.topic_id = t.id
          ) AS recent,
          i.importance,
          i.summary,
          i.tags
        FROM topics t
        LEFT JOIN topic_insights i ON i.topic_id = t.id
        WHERE (
          SELECT COALESCE(SUM(
            CASE
              WHEN datetime(COALESCE(NULLIF(a3.published_at,''), a3.fetched_at)) >= datetime(?) THEN 1
              ELSE 0
            END
          ), 0)
          FROM topic_articles ta3
          JOIN articles a3 ON a3.id = ta3.article_id
          WHERE ta3.topic_id = t.id
        ) > 0
        ORDER BY COALESCE(recent,0) DESC, COALESCE(i.importance,0) DESC, t.id ASC
        LIMIT 10
        """,
        (cutoff_48h, cutoff_48h),
    )
    trending_top = []
    for tid, title, category, url, article_date, recent, importance, summary, tags in cur.fetchall():
        trending_top.append({
            "id": tid,
            "title": title,
            "category": category,   # ★追加
            "url": url or "#",
            "recent": int(recent or 0),
            "importance": int(importance) if importance is not None else 0,
            "summary": summary or "",
            "tags": _safe_json_list(tags),
            "one_liner": "",
            "date": article_date,
        })

    
    # 生成日時（JST）
    generated_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S JST")
    meta["generated_at_jst"] = generated_at

    tech_dir = out_dir / "tech"
    tech_dir.mkdir(exist_ok=True)

    tech_html_sub = Template(HTML).render(
        common_css=COMMON_CSS,
        tech_extra_css=TECH_EXTRA_CSS,
        page="tech",
        nav_prefix="../",
        categories=categories,
        cat_name=cat_name,
        topics_by_cat=topics_by_cat,
        hot_by_cat=hot_by_cat,
        generated_at=generated_at,
        meta=meta,
        global_top=global_top,
        trending_top=trending_top,
        tag_list=tag_list,
        fmt_date=fmt_date,
    )

    tech_html_root = Template(HTML).render(
        common_css=COMMON_CSS,
        tech_extra_css=TECH_EXTRA_CSS,
        page="tech",
        nav_prefix="./",
        categories=categories,
        cat_name=cat_name,
        topics_by_cat=topics_by_cat,
        hot_by_cat=hot_by_cat,
        generated_at=generated_at,
        meta=meta,
        global_top=global_top,
        trending_top=trending_top,
        tag_list=tag_list,
        fmt_date=fmt_date,
    )

    (tech_dir / "index.html").write_text(tech_html_sub, encoding="utf-8")
    (out_dir / "index.html").write_text(tech_html_root, encoding="utf-8")

    render_news_pages(out_dir, generated_at, cur)

    conn.close()
    print(f"[TIME] step=render end sec={_now_sec() - t0:.1f}")

if __name__ == "__main__":
    main()
