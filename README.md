# SEO-Article-Generator-with-Gemini-Serper.dev
このプロジェクトは Gemini API と Serper.dev (Google検索API) を活用し、ローカル環境でSEO記事を自動生成・編集できるWebアプリケーションです。 研究目的・個人利用向けに設計しており、検索結果をもとにした記事生成や、AIによる改善・編集フローを試せます。
特徴

🔍 Serper.dev API を使って検索結果（上位数件）をJSONで取得

✍️ Gemini API による記事の自動生成（10以上のセクションに分割）

📝 対話型編集機能：セクション単位で「編集」「再生成」が可能

🌐 マークアッププレビュー：SEO対策されたHTML構造を即時プレビュー

📄 JSON-LD 構造化データを自動生成し、検索エンジン最適化を補助

📑 LLM TXT 出力：robot.txtに似た形式で記事構造を保存（LLMクローラー用メタデータ想定）

⚡ Flaskベースでローカルホストから簡単に利用可能

画面イメージ

記事ビュー：セクションごとの内容を表示、編集や再生成ボタン付き

マークアップビュー：H1/H2タグやリストを反映したHTML形式でプレビュー

JSON-LDビュー：FAQスキーマなどを含んだ構造化データを出力

想定ユースケース

AIオーバービューやLLMO対策を意識したSEO記事の自動生成

独自データや一次情報を追加して「権威性・網羅性」を強化

記事改善のA/BテストやSEO施策の実験環境

必要環境

Python 3.9+

APIキー

Serper.dev → SERPER_API_KEY

Gemini API → GEMINI_API_KEY

pip install flask requests google-generativeai
setx SERPER_API_KEY "xxxx"
setx GEMINI_API_KEY "xxxx"
python app.py


起動後、ブラウザで http://127.0.0.1:5000/ にアクセスしてください。
