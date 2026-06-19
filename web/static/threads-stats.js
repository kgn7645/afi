/* Threads: ヘッダーの数値チップ＋ナビタブのバッジ（LINE未読風）を /threads/stats から描画。
 * 各テンプレのナビ(.seg)を編集せずJSで注入。30秒ごとに自動更新（キュー処理の反映が見える）。 */
(function () {
  "use strict";

  var css = ''
    + '.seg a{position:relative;}'
    + '.th-badge{position:absolute;top:-4px;right:0;min-width:17px;height:17px;padding:0 4px;'
    + 'border-radius:999px;background:#e0433f;color:#fff;font-size:10px;font-weight:800;'
    + 'line-height:17px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.28);'
    + 'transform:scale(0);transition:transform .18s cubic-bezier(.3,1.6,.5,1);}'
    + '.th-badge.on{transform:scale(1);}'
    + '.th-stats{max-width:680px;margin:6px auto 0;display:flex;gap:6px;justify-content:center;'
    + 'flex-wrap:wrap;padding:0 12px;}'
    + '.th-stats .chip{background:#fff;border-radius:999px;padding:4px 11px;font-size:11.5px;'
    + 'font-weight:700;color:#8a86a0;box-shadow:0 1px 4px rgba(40,30,70,.06);white-space:nowrap;}'
    + '.th-stats .chip b{color:#2b2b3a;margin-left:3px;}'
    + '.th-stats .chip.warn b{color:#c2410c;}'
    + '.th-stats .chip.go b{color:#21c17a;}';
  var st = document.createElement('style');
  st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  function setBadge(href, n, color) {
    var a = document.querySelector('.seg a[href="' + href + '"]');
    if (!a) return;
    var b = a.querySelector('.th-badge');
    if (!n) { if (b) b.classList.remove('on'); return; }
    if (!b) { b = document.createElement('i'); b.className = 'th-badge'; a.appendChild(b); }
    b.textContent = n > 99 ? '99+' : n;
    if (color) b.style.background = color;
    void b.offsetWidth;
    b.classList.add('on');
  }

  function chip(label, n, cls) {
    var show = (n != null && n !== 0);
    return '<span class="chip ' + (cls && show ? cls : '') + '">' + label
      + ' <b>' + (n == null ? '…' : n) + '</b></span>';
  }

  function header(s) {
    var bar = document.querySelector('.th-stats');
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'th-stats';
      var nav = document.querySelector('.nav');
      if (nav && nav.parentNode) nav.parentNode.insertBefore(bar, nav.nextSibling);
      else ready(function () { document.body.insertBefore(bar, document.body.firstChild); });
    }
    bar.innerHTML =
        chip('🛍 選定', s.select)
      + chip('✍️ 生成待ち', s.gen, 'warn')
      + chip('📝 承認待ち', s.drafts, 'warn')
      + chip('📅 公開待ち', s.queue, 'go')
      + chip('⏳ 取得待ち', s.fetch, 'warn');
  }

  function load() {
    fetch('/threads/stats', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) return;
        var s = d.stats || {};
        header(s);
        setBadge('/threads/select', s.select);            // 選定: 候補数
        setBadge('/threads', s.drafts);                   // 投稿: 承認待ち
        setBadge('/threads/add', s.fetch, '#f59e0b');     // 追加: 取得待ち(処理中=橙)
      })
      .catch(function () {});
  }

  ready(load);
  setInterval(load, 30000);   // 30秒ごとに更新（キュー処理の反映が自動で見える）
  window.addEventListener('pageshow', function (e) { if (e.persisted) load(); });
})();
