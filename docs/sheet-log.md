# 生成実績のスプレッドシート書き戻し（Issue #4）

生成・承認の結果を **Googleスプレッドシートに「1記事=1行」** で自動記録する。
サービスアカウント等は不要。**Google Apps Script の Web App** にPOSTする方式（#32の公開CSVと同じ軽量思想）。

記録される列：`投稿ID / 生成日時 / ブランド / カテゴリ / タイトル / ステータス / URL / 更新日時`
- 生成時に upsert（`投稿ID` で突き合わせ）
- 承認アプリで公開/却下/差し戻しすると **ステータス列**を更新

## セットアップ

### 1. スプレッドシートを用意
記録用のGoogleスプレッドシートを新規作成（シート名は任意。スクリプトが `log` シートを自動作成）。

### 2. Apps Script を設置
スプレッドシートの **拡張機能 → Apps Script** を開き、以下を貼り付けて保存：

```javascript
function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var data = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName('log') || ss.insertSheet('log');
    if (sh.getLastRow() === 0) {
      sh.appendRow(['投稿ID','生成日時','ブランド','カテゴリ','タイトル','ステータス','URL','更新日時']);
    }
    var now = new Date();
    var lastRow = sh.getLastRow();
    var ids = lastRow >= 2
      ? sh.getRange(2, 1, lastRow - 1, 1).getValues().map(function (r) { return String(r[0]); })
      : [];
    var idx = ids.indexOf(String(data.post_id));

    if (data.action === 'status') {            // ステータス列だけ更新
      if (idx >= 0) {
        sh.getRange(idx + 2, 6).setValue(data.status);
        sh.getRange(idx + 2, 8).setValue(now);
      }
    } else {                                   // upsert（生成）
      var row = [data.post_id, data.datetime, data.brand, data.category,
                 data.title, data.status, data.url, now];
      if (idx >= 0) sh.getRange(idx + 2, 1, 1, 8).setValues([row]);
      else sh.appendRow(row);
    }
    return ContentService.createTextOutput('ok');
  } finally {
    lock.releaseLock();
  }
}
```

### 3. Web App としてデプロイ
- Apps Script の **デプロイ → 新しいデプロイ → 種類: ウェブアプリ**
- 「次のユーザーとして実行」= **自分**
- 「アクセスできるユーザー」= **全員**
- デプロイ → 発行された **ウェブアプリURL**（`https://script.google.com/macros/s/.../exec`）を控える

### 4. .env に設定
```
SHEET_LOG_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
```

## 動作
- 未設定なら **no-op**（何もしない）。設定すれば生成バッチ・承認アプリ双方から自動記録。
- POST失敗してもパイプラインや承認操作は止めない（警告のみ）。
- URL列には記事の編集リンクが入るので、シートから直接WPに飛べる。

## 補足
- 承認アプリ(Render)からの更新は、RenderのEnvにも `SHEET_LOG_WEBHOOK_URL` を設定すれば反映される。
- 収益との突き合わせ（#20）も、このシートを起点に拡張できる。
