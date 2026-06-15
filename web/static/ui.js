/* 共通UI: 画面遷移・送信時の上部ローディングバー（反応の可視化） */
(function () {
  var bar = document.createElement('div');
  bar.style.cssText =
    'position:fixed;top:0;left:0;height:4px;width:0;z-index:99999;' +
    'background:linear-gradient(90deg,#ff9f1c,#ff5a5f);' +
    'box-shadow:0 0 10px rgba(255,126,95,.7);border-radius:0 3px 3px 0;' +
    'transition:width .3s ease, opacity .35s ease;opacity:0;';
  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }
  ready(function () { document.body.appendChild(bar); });

  var t1, t2;
  function start() {
    bar.style.opacity = '1';
    bar.style.width = '0';
    void bar.offsetWidth;        // reflow でアニメをリセット
    bar.style.width = '70%';
    clearTimeout(t1); clearTimeout(t2);
    t1 = setTimeout(function () { bar.style.width = '88%'; }, 500);
    t2 = setTimeout(function () { bar.style.width = '96%'; }, 2500);
  }
  function reset() {
    clearTimeout(t1); clearTimeout(t2);
    bar.style.width = '0'; bar.style.opacity = '0';
  }

  // 内部リンク/タブ遷移
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a') : null;
    if (!a) return;
    var h = a.getAttribute('href') || '';
    if (a.target === '_blank' || !h || h.charAt(0) === '#' ||
        h.indexOf('javascript') === 0 || a.hasAttribute('download')) return;
    start();
  }, true);

  // フォーム送信（公開/却下/保存/ログイン）
  document.addEventListener('submit', function () { start(); }, true);

  // 戻る/進む（bfcache）復帰時はリセット
  window.addEventListener('pageshow', reset);
})();
