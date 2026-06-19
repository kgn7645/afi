/* 共通UX強化: ホバー/押下フィードバック・送信スピナー・処理中オーバーレイ・確認モーダル
 * 反応の可視化（ユーザーが「効いているか」迷わないように）。ui.js（上部バー）と併用可。
 * 使い方（テンプレ側・任意）:
 *   <button data-busy="記事化中…（最大15秒）">…</button>   送信時に全画面オーバーレイ
 *   <form data-confirm="削除しますか？" data-confirm-ok="削除する">…</form>  送信前に確認モーダル
 */
(function () {
  "use strict";

  // ---------- スタイル注入（各テンプレのCSSを編集せず一括適用） ----------
  var css = ''
    + 'button,.bar a,.acts a,a.badd,a.bcol,a.bpost,input[type=submit]{'
    + 'transition:transform .12s ease,filter .12s ease,box-shadow .12s ease,opacity .12s ease;}'
    + '@media(hover:hover){'
    + 'button:not(:disabled):hover,.bar a:hover,.acts a:hover,a.badd:hover,a.bcol:hover,'
    + 'a.bpost:hover,input[type=submit]:not(:disabled):hover{'
    + 'filter:brightness(1.06);transform:translateY(-1px);box-shadow:0 5px 16px rgba(40,30,70,.16);}}'
    + 'button:not(:disabled):active,.bar a:active,.acts a:active,a.badd:active,a.bcol:active,'
    + 'a.bpost:active,input[type=submit]:not(:disabled):active{'
    + 'transform:translateY(1px) scale(.985);filter:brightness(.93);box-shadow:none;}'
    + 'button:disabled{cursor:progress;opacity:.72;}'
    + 'label,a,button{ -webkit-tap-highlight-color:rgba(0,0,0,.04); }'
    + '@keyframes uxspin{to{transform:rotate(360deg)}}'
    + '.ux-spin{display:inline-block;width:1em;height:1em;border:2px solid currentColor;'
    + 'border-right-color:transparent;border-radius:50%;animation:uxspin .6s linear infinite;'
    + 'vertical-align:-.15em;margin-right:7px;}'
    + '.ux-ov,.ux-modal{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;'
    + 'justify-content:center;background:rgba(22,18,32,.46);-webkit-backdrop-filter:blur(2px);'
    + 'backdrop-filter:blur(2px);opacity:0;pointer-events:none;transition:opacity .18s ease;}'
    + '.ux-ov.on,.ux-modal.on{opacity:1;pointer-events:auto;}'
    + '.ux-ov .box{background:#fff;border-radius:18px;padding:24px 28px;display:flex;'
    + 'flex-direction:column;align-items:center;gap:13px;max-width:80%;'
    + 'box-shadow:0 20px 54px rgba(20,12,40,.32);animation:uxpop .2s ease;}'
    + '.ux-ov .big{width:44px;height:44px;border:4px solid #efeaf6;border-top-color:#ff9f1c;'
    + 'border-radius:50%;animation:uxspin .7s linear infinite;}'
    + '.ux-ov .msg{font-weight:800;color:#2b2b3a;font-size:14px;text-align:center;line-height:1.5;}'
    + '.ux-modal .m{background:#fff;border-radius:18px;padding:22px 22px 18px;width:86%;max-width:340px;'
    + 'box-shadow:0 20px 54px rgba(20,12,40,.32);animation:uxpop .2s ease;}'
    + '.ux-modal h3{margin:0 0 9px;font-size:16px;color:#2b2b3a;}'
    + '.ux-modal p{margin:0 0 18px;font-size:13px;color:#5b5870;line-height:1.6;white-space:pre-wrap;}'
    + '.ux-modal .row{display:flex;gap:10px;}'
    + '.ux-modal .row button{flex:1;border:none;border-radius:12px;padding:13px;font-size:13px;'
    + 'font-weight:800;cursor:pointer;}'
    + '.ux-modal .cancel{background:#f1eef6;color:#555;}'
    + '.ux-modal .ok{background:linear-gradient(135deg,#e0433f,#ff6a66);color:#fff;}'
    + '.ux-modal .ok.safe{background:linear-gradient(135deg,#21c17a,#13b06b);}'
    + '@keyframes uxpop{from{transform:scale(.94);opacity:.4}to{transform:scale(1);opacity:1}}';
  var st = document.createElement('style');
  st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  // ---------- 処理中オーバーレイ ----------
  var ov;
  function overlay() {
    if (!ov) {
      ov = document.createElement('div');
      ov.className = 'ux-ov';
      ov.innerHTML = '<div class="box"><div class="big"></div><div class="msg"></div></div>';
      document.body.appendChild(ov);
    }
    return ov;
  }
  function showOverlay(msg) {
    ready(function () {
      var o = overlay();
      o.querySelector('.msg').textContent = msg || '処理中…';
      void o.offsetWidth;
      o.classList.add('on');
    });
  }
  function hideOverlay() { if (ov) ov.classList.remove('on'); }

  // ---------- 確認モーダル ----------
  function uxConfirm(message, okLabel, danger) {
    return new Promise(function (resolve) {
      ready(function () {
        var m = document.createElement('div');
        m.className = 'ux-modal';
        var okCls = 'ok' + (danger ? '' : ' safe');
        m.innerHTML = '<div class="m"><h3>確認</h3><p></p>'
          + '<div class="row"><button type="button" class="cancel">キャンセル</button>'
          + '<button type="button" class="' + okCls + '"></button></div></div>';
        m.querySelector('p').textContent = message || 'よろしいですか？';
        var okBtn = m.querySelector('.ok');
        okBtn.textContent = okLabel || 'OK';
        document.body.appendChild(m);
        void m.offsetWidth;
        m.classList.add('on');
        function close(val) {
          m.classList.remove('on');
          setTimeout(function () { m.remove(); }, 200);
          resolve(val);
        }
        okBtn.addEventListener('click', function () { close(true); });
        m.querySelector('.cancel').addEventListener('click', function () { close(false); });
        m.addEventListener('click', function (e) { if (e.target === m) close(false); });
        document.addEventListener('keydown', function esc(e) {
          if (e.key === 'Escape') { document.removeEventListener('keydown', esc); close(false); }
        });
      });
    });
  }

  // ---------- 送信ボタンをスピナー化 ----------
  function setBusy(btn) {
    if (!btn || btn.__busy) return;
    btn.__busy = true;
    btn.__html = btn.innerHTML;                          // 復帰用に元テキストを保持
    var label = btn.getAttribute('data-busy-label') || btn.textContent.trim();
    btn.innerHTML = '<span class="ux-spin"></span>' + label;
    setTimeout(function () { btn.disabled = true; }, 0); // 送信値を欠落させないよう次tickで無効化
  }
  function clearBusy() {
    Array.prototype.forEach.call(document.querySelectorAll('button'), function (btn) {
      if (btn.__busy) { btn.__busy = false; btn.disabled = false; if (btn.__html != null) btn.innerHTML = btn.__html; }
    });
    Array.prototype.forEach.call(document.querySelectorAll('form'), function (f) { f.__submitting = false; });
  }

  // ---------- 確認ゲート（capture: 検証/送信より先に止める） ----------
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || form.tagName !== 'FORM') return;
    var cfm = form.getAttribute('data-confirm');
    if (cfm && !form.__uxOK) {
      e.preventDefault();
      e.stopPropagation();
      var btn = e.submitter;
      var danger = !form.hasAttribute('data-safe');
      uxConfirm(cfm, form.getAttribute('data-confirm-ok'), danger).then(function (ok) {
        if (ok) {
          form.__uxOK = true;
          if (form.requestSubmit) form.requestSubmit(btn || undefined);
          else form.submit();
        }
      });
    }
  }, true);

  // ---------- 送信フィードバック（bubble: 実際に送信される時だけ） ----------
  document.addEventListener('submit', function (e) {
    if (e.defaultPrevented) return;             // 検証/確認でキャンセルされた
    var form = e.target;
    if (!form || form.tagName !== 'FORM') return;
    if (form.__submitting) { e.preventDefault(); return; }  // 二重送信防止
    form.__submitting = true;
    form.__uxOK = false;
    var btn = e.submitter || form.querySelector('button:not([type=button]),[type=submit]');
    if (btn) setBusy(btn);
    var msg = (btn && btn.getAttribute('data-busy')) || form.getAttribute('data-busy');
    if (msg) showOverlay(msg);
  }, false);

  // 戻る/進む（bfcache）で復帰した時はオーバーレイ/スピナーを消して操作可能に戻す
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) { hideOverlay(); clearBusy(); }
  });

  // 外部公開（テンプレから手動で使いたい場合）
  window.UX = { confirm: uxConfirm, busy: showOverlay, done: hideOverlay };
})();
