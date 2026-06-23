/* バックグラウンドジョブUI: form[data-bg] をAJAX送信し、待たずに次へ進めるようにする。
 * 送信→即カードをフェード＋「処理中」トースト→/jobs/status をポーリング→完了をトースト。
 * collect（候補収集）完了時のみ、新着を表示するため自動リロード。 */
(function () {
  var pollTimer = null;
  var seen = {};            // 完了通知済みジョブID
  var needReload = false;   // 結果が現ページに出る種別の完了があればリロード（collect/generate）

  function el(tag, css, txt) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (txt != null) e.textContent = txt;
    return e;
  }

  function toast(msg, kind) {
    var bg = kind === 'error' ? '#fce8e8' : (kind === 'ok' ? '#e3f8ee' : '#eef2ff');
    var fg = kind === 'error' ? '#b3261e' : (kind === 'ok' ? '#0d8a6a' : '#3730a3');
    var t = el('div',
      'position:fixed;left:50%;transform:translateX(-50%);bottom:74px;z-index:9999;' +
      'background:' + bg + ';color:' + fg + ';padding:10px 16px;border-radius:12px;' +
      'font-size:13px;font-weight:700;box-shadow:0 6px 20px rgba(0,0,0,.18);max-width:88vw;', msg);
    document.body.appendChild(t);
    setTimeout(function () { t.style.transition = 'opacity .4s'; t.style.opacity = '0'; }, 2600);
    setTimeout(function () { t.remove(); }, 3100);
  }

  function pill(n) {
    var p = document.getElementById('jobpill');
    if (n > 0) {
      if (!p) {
        p = el('div', 'position:fixed;right:12px;bottom:74px;z-index:9998;background:#fff3e0;' +
          'color:#c2410c;padding:8px 14px;border-radius:20px;font-size:12px;font-weight:800;' +
          'box-shadow:0 4px 14px rgba(0,0,0,.15);');
        p.id = 'jobpill';
        document.body.appendChild(p);
      }
      p.textContent = '⏳ 処理中 ' + n + '件';
    } else if (p) {
      p.remove();
    }
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = setInterval(tick, 2500);
    tick();
  }

  function tick() {
    fetch('/jobs/status', { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) return;
        pill(d.active);
        (d.jobs || []).forEach(function (j) {
          if ((j.state === 'done' || j.state === 'error') && !seen[j.id]) {
            seen[j.id] = 1;
            toast((j.state === 'done' ? '✓ ' : '⚠ ') + (j.message || j.kind),
              j.state === 'done' ? 'ok' : 'error');
            if ((j.kind === 'collect' || j.kind === 'generate') && j.state === 'done') needReload = true;
          }
        });
        if (d.active === 0) {
          clearInterval(pollTimer); pollTimer = null;
          if (needReload) { needReload = false; setTimeout(function () { location.reload(); }, 1300); }
        }
      })
      .catch(function () { });
  }

  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || !f.matches || !f.matches('form[data-bg]')) return;
    e.preventDefault();
    var card = f.closest('.card');
    var btn = f.querySelector('button');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 送信中…'; }
    fetch(f.action, {
      method: 'POST', body: new FormData(f),
      headers: { 'X-Requested-With': 'fetch' }
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok) {
          if (card) {
            card.style.transition = 'opacity .35s, transform .35s';
            card.style.opacity = '.35';
            card.style.transform = 'scale(.97)';
            // 採用/却下系はリストから消えるので操作を無効化
            card.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
          }
          toast('⏳ バックグラウンドで処理中…次の作業へどうぞ');
          startPoll();
        } else {
          if (btn) { btn.disabled = false; }
          toast('送信に失敗しました', 'error');
        }
      })
      .catch(function () {
        if (btn) { btn.disabled = false; }
        toast('通信エラー', 'error');
      });
  }, true);

  // ページ表示時、既に動いているジョブがあればポーリング再開
  document.addEventListener('DOMContentLoaded', function () { startPoll(); });
})();
