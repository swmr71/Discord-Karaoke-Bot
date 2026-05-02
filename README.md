# Discord-Karaoke-Bot
## 概要
Discordでカラオケする用Bot
## セットアップ方法
1. ライブラリのインストール  
  `pip install -r requirements.txt`  
  `sudo apt install ffmpeg`
2. Botトークンの発行  
  まず、[Discord Developper Portal](https://discord.com/developers/applications)からアプリケーション(Bot)を作成します。  
  Botタブを開きトークンをリセットボタンを押します。  
  表示された長いコードをコピーします。これは絶対に紛失・公開しないでください。  
  「認可フロー」の「Presence Intent」、「Server Members Intent」、「Message Content Intent」のトグルをオンにします。  
  OAuth2タブを開き下にスクロールしたところにある「スコープ」の「bot」にチェックを入れます。  
  またスクロールして「Botの権限」の「管理者」にチェックを入れてください（自分の実行環境やコードが信頼できない場合、自分で考えて適切な権限に絞るようにしてください）  
  「生成されたURL」をコピーして、ブラウザで開きます。
  画面に従って操作し、Discordサーバーに導入してください。  
3. bot.pyを編集
  bot.pyを編集します。メモ帳等で開き、一番下まで移動します。  
  「TOKEN="ここに入力"」の""の内容を先ほどコピーしたトークンに置き換えます。  
  保存します。  
4. 実行
  実行します。  
  「python bot.py」を実行します。  
  おしまい。
