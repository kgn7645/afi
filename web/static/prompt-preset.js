/* プロンプトのプリセット(A/B/C)UI: スタイル注入＋「＋新規」プロンプト。
 * 切替/保存/削除はサーバ往復（各ブロックが独立フォーム）。削除確認は ux.js が担当。 */
(function () {
  "use strict";
  var css = ''
    + '.ppset{background:#faf8fd;border:1.5px solid #ece9f1;border-radius:14px;padding:13px;margin:12px 0;}'
    + '.pphead{display:flex;align-items:center;gap:8px;margin-bottom:8px;}'
    + '.pphead .pplabel{font-size:14px;font-weight:800;color:#2b2b3a;}'
    + '.pphead .ppactive{font-size:11px;font-weight:700;color:#6a5fb0;background:#f3f0fb;'
    + 'border-radius:999px;padding:2px 9px;}'
    + '.ppchips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:9px;}'
    + '.ppchips form{display:inline;margin:0;}'
    + '.ppchip{border:1.5px solid #e6e1ee;background:#fff;color:#6a5fb0;border-radius:999px;'
    + 'padding:6px 13px;font-size:12.5px;font-weight:800;cursor:pointer;}'
    + '.ppchip.on{background:linear-gradient(135deg,#7a6fb0,#9b8fd0);color:#fff;border-color:transparent;}'
    + '.ppchip.ppadd{border-style:dashed;color:#9a8fc0;}'
    + '.ppset textarea{width:100%;padding:11px 12px;border:1.5px solid #ece9f1;border-radius:10px;'
    + 'font-size:13px;font-family:ui-monospace,Menlo,monospace;line-height:1.55;resize:vertical;}'
    + '.ppset textarea:focus{border-color:#7a6fb0;outline:none;}'
    + '.pprow{display:flex;gap:8px;margin-top:8px;}'
    + '.ppsave{border:none;border-radius:10px;padding:11px 16px;font-size:13px;font-weight:800;'
    + 'color:#fff;background:linear-gradient(135deg,#21c17a,#13b06b);cursor:pointer;}'
    + '.ppdelf{margin:6px 0 0;}'
    + '.ppdel{border:1.5px solid #f0d0d0;background:#fff;color:#c2410c;border-radius:10px;'
    + 'padding:9px 14px;font-size:12.5px;font-weight:800;cursor:pointer;}';
  var st = document.createElement('style');
  st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  window.ppAdd = function (field, back) {
    var nm = (window.prompt('新しいプリセット名（例: B / 攻めバージョン / 丁寧）\n※現在の内容を複製して作成します') || '').trim();
    if (!nm) return;
    var f = document.createElement('form');
    f.method = 'post';
    f.action = '/prompt-preset/add';
    function add(k, v) {
      var i = document.createElement('input');
      i.type = 'hidden'; i.name = k; i.value = v;
      f.appendChild(i);
    }
    add('field', field); add('name', nm); add('back', back);
    document.body.appendChild(f);
    f.submit();
  };
})();
