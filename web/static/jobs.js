/* バックグラウンドジョブUI: form[data-bg] をAJAX送信し、待たずに次へ進めるようにする。
 * クリック→カードを即たたむ(楽観的)→裏でPOST→完了をトーストで通知。
 * 失敗時はカードを元に戻す。全画面オーバーレイ(ux.js)は出さない。
 * 任意: data-bg-confirm="…" があれば送信前にネイティブ確認。
 * collect/generate 完了時のみ、新着反映のため自動リロード。 */
(function () {
  var pollTimer = null, seen = {}, needReload = false;

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
      'position:fixed;left:50%;transform:translateX(-50%);bottom:74px;z-index:99999;background:' + bg +
      ';color:' + fg + ';padding:10px 16px;border-radius:12px;font-size:13px;font-weight:700;' +
      'box-shadow:0 6px 20px rgba(0,0,0,.18);max-width:88vw;text-align:center;', msg);
    document.body.appendChild(t);
    setTimeout(function () { t.style.transition = 'opacity .4s'; t.style.opacity = '0'; }, 2600);
    setTimeout(function () { t.remove(); }, 3100);
  }
  function pill(n) {
    var p = document.getElementById('jobpill');
    if (n > 0) {
      if (!p) {
        p = el('div', 'position:fixed;right:12px;bottom:74px;z-index:99998;background:#fff3e0;color:#c2410c;' +
          'padding:8px 14px;border-radius:20px;font-size:12px;font-weight:800;box-shadow:0 4px 14px rgba(0,0,0,.15);');
        p.id = 'jobpill'; document.body.appendChild(p);
      }
      p.textContent = '⏳ 処理中 ' + n + '件';
    } else if (p) { p.remove(); }
  }

  // カードをたたむ/戻す（楽観的UI）
  function collapse(card) {
    if (!card) return null;
    var h = card.offsetHeight;
    var snap = { h: card.style.height, o: card.style.opacity, m: card.style.margin,
                 p: card.style.padding, ov: card.style.overflow };
    card.style.overflow = 'hidden';
    card.style.height = h + 'px';
    void card.offsetWidth;
    card.style.transition = 'height .3s ease, opacity .3s ease, margin .3s ease, padding .3s ease';
    card.style.opacity = '0'; card.style.height = '0px';
    card.style.margin = '0'; card.style.padding = '0';
    return snap;
  }
  function restore(card, snap) {
    if (!card || !snap) return;
    card.style.transition = 'none';
    card.style.height = snap.h; card.style.opacity = snap.o; card.style.margin = snap.m;
    card.style.padding = snap.p; card.style.overflow = snap.ov;
  }

  function startPoll() { if (!pollTimer) { pollTimer = setInterval(tick, 2500); tick(); } }
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
            if (j.state === 'done' && (j.kind === 'collect' || j.kind === 'generate')) needReload = true;
          }
        });
        if (d.active === 0) {
          clearInterval(pollTimer); pollTimer = null;
          if (needReload) { needReload = false; setTimeout(function () { location.reload(); }, 1200); }
        }
      })
      .catch(function () { });
  }

  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || !f.matches || !f.matches('form[data-bg]')) return;
    e.preventDefault();
    e.stopPropagation();                 // ux.js の全画面オーバーレイ等を抑止
    var cfm = f.getAttribute('data-bg-confirm');
    if (cfm && !window.confirm(cfm)) return;
    var card = f.closest('.card');
    var snap = collapse(card);           // 押した瞬間にカードを畳む＝待たず次へ
    toast('処理中…次の作業へどうぞ');
    fetch(f.action, { method: 'POST', body: new FormData(f), headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json().catch(function () { return { ok: true }; }); })
      .then(function (d) {
        if (d && d.ok) { if (card) card.remove(); startPoll(); }
        else { restore(card, snap); toast('失敗しました。もう一度お試しください', 'error'); }
      })
      .catch(function () {
        // 通信エラー：サーバー側は処理済みの可能性が高い。畳んだままにし、状況だけ通知
        if (card) card.remove(); startPoll();
        toast('送信しました（通信が不安定です。反映を確認してください）');
      });
  }, true);

  document.addEventListener('DOMContentLoaded', function () { startPoll(); });
})();
