# 商品候補プール（スワイプ選定の共有ストア / Issue #3・#12）

クローラ(Xserver)が書き込み、スワイプUI(Render)が読み書きする**共有ストア**。
Google Apps Script Web App（=スプレッドシート）で実現（#4/#32と同じ方式）。

```
クローラ(Xserver) ──append──▶ [candidates シート] ◀──読込/承認──  スワイプUI(Render)
                                   status: pending→approved/rejected→generated
                                        │approved
                                        ▼
                                   生成バッチが記事化（Phase3）
```

列: `asin / title / price / image / url / status / added_at / updated_at`

## 1. スプレッドシート＋Apps Script を設置
記録用スプレッドシートの **拡張機能 → Apps Script** に貼り付けて保存：

```javascript
function _sheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName('candidates');
  if (!sh) { sh = ss.insertSheet('candidates');
    sh.appendRow(['asin','title','price','image','url','status','added_at','updated_at']); }
  return sh;
}
function _asinSet(sh){ var s={}; if(sh.getLastRow()<2) return s;
  sh.getRange(2,1,sh.getLastRow()-1,1).getValues().forEach(function(r){ if(r[0]) s[r[0]]=true; }); return s; }
function _setStatus(sh,asin,status){ if(sh.getLastRow()<2) return;
  var v=sh.getRange(2,1,sh.getLastRow()-1,1).getValues();
  for(var i=0;i<v.length;i++){ if(String(v[i][0])===String(asin)){
    sh.getRange(i+2,6).setValue(status); sh.getRange(i+2,8).setValue(new Date()); return; } } }

function doGet(e){
  var status=(e.parameter.status||'pending'), limit=parseInt(e.parameter.limit||'100',10);
  var sh=_sheet(), rows=sh.getDataRange().getValues(), head=rows.shift()||[], col={};
  head.forEach(function(h,i){col[h]=i;});
  var items=[];
  for(var i=0;i<rows.length && items.length<limit;i++){ var r=rows[i];
    if(status==='all' || String(r[col['status']])===status){
      items.push({asin:r[col['asin']],title:r[col['title']],price:r[col['price']],
                  image:r[col['image']],url:r[col['url']],status:r[col['status']]}); } }
  return ContentService.createTextOutput(JSON.stringify({items:items}))
    .setMimeType(ContentService.MimeType.JSON);
}
function doPost(e){
  var lock=LockService.getScriptLock(); lock.waitLock(30000);
  try{
    var data=JSON.parse(e.postData.contents), sh=_sheet();
    if(data.action==='append'){
      var exist=_asinSet(sh), now=new Date();
      (data.candidates||[]).forEach(function(c){ if(!c.asin||exist[c.asin]) return;
        sh.appendRow([c.asin,c.title,c.price,c.image,c.url,'pending',now,now]); exist[c.asin]=true; });
    } else if(data.action==='status'){ _setStatus(sh,data.asin,data.status); }
    return ContentService.createTextOutput('ok');
  } finally { lock.releaseLock(); }
}
```

## 2. Web App としてデプロイ
- デプロイ → 新しいデプロイ → 種類: **ウェブアプリ**
- 実行=**自分** / アクセス=**全員**
- 発行URL（`.../exec`）を控える

## 3. .env に設定（XserverとRender 両方）
```
CANDIDATES_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
```
- **Xserver**: クローラが候補を append する
- **Render**: スワイプUIが pending を読み、承認/却下で status を更新する

## 4. クローラのcron（Xserver）
```cron
# 毎朝5時に候補をクロールして候補プールへ投入
0 5 * * *  cd ~/afi && .venv/bin/python scripts/crawl_candidates.py >> data/crawl.log 2>&1
```
クロール対象（keywords/ranking_nodes）は `config.yaml` の `candidates` で調整。

## 5. 使い方
1. 毎朝クローラが候補を pending で追加
2. 担当者が Render の `/select` でスワイプ（右=承認 / 左=却下）
3. 承認(approved)分を生成バッチが記事化（Phase3）→ `/review` で記事承認→公開
