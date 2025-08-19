# app.py
import os
import re
import json
import datetime
import pathlib
import requests
import google.generativeai as genai
from flask import Flask, request, render_template_string, jsonify, abort

# ====== 基本設定 ======
app = Flask(__name__)
MODEL_GEMINI = "gemini-2.0-flash"
LANG = "ja"
TOPK = 3  # Serper.devから上位いくつ取得するか

# ====== ユーティリティ ======
def require_env(var_name: str):
    v = os.environ.get(var_name)
    if not v:
        abort(500, f"環境変数 {var_name} が未設定です。setx {var_name} \"YOUR_KEY\" を実行し、ターミナルを開き直してください。")
    return v

# ====== Serper.dev 検索 ======
def serper_search(query: str) -> dict:
    api_key = require_env("SERPER_API_KEY")
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "hl": LANG, "num": TOPK}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

# ====== Gemini呼び出し（記事セクションJSON生成） ======
def gemini_generate_sections(query: str, serp_json: dict, custom_data: str = "") -> dict:
    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    model = genai.GenerativeModel(MODEL_GEMINI)

    system_prompt = (
        "あなたはSEO記事の編集長です。"
        "検索結果の要約(JSON)と、任意の独自データテキストを参考に、記事を10以上のセクションに分割し、"
        "純粋なJSONのみで返してください。"
        "推奨キー: intro, overview, key_points, methodology, advantages, disadvantages, "
        "use_cases, faq, future_outlook, conclusion, references, original_data"
        "。独自データが与えられた場合は original_data セクションを必ず含め、"
        "本文中でも適切に参照・言及してください。"
        "FAQはQ/A形式を推奨します。"
    )

    user_prompt = f"""
# テーマ
{query}

# 検索結果(上位{TOPK}件)
{json.dumps(serp_json.get("organic", [])[:TOPK], ensure_ascii=False, indent=2)}

# 独自データ（任意・そのまま活用）
{custom_data if custom_data.strip() else "（なし）"}
"""

    resp = model.generate_content([system_prompt, user_prompt])
    raw = (resp.text or "").strip()

    # --- デバッグ ---
    print("=== Gemini raw output ===")
    print(raw)
    print("=========================")

    # --- 前処理: コードフェンス/おかしなトークン/末尾カンマを除去しつつJSON抽出 ---
    s = raw
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r",\s*(重要)\s*,", ",", s)
    s = re.sub(r"}\s*(重要)\s*,", "},", s)
    s = s.replace('"内容"', '"content"').replace('内容"', 'content"')
    s = re.sub(r",(\s*[}\]])", r"\1", s)

    m = re.search(r"\{.*\}\s*$", s, re.S)
    if not m:
        raise ValueError("Geminiが有効なJSONを返しませんでした。出力:\n" + raw)
    json_str = m.group(0)

    try:
        data = json.loads(json_str)
    except Exception as e:
        t = json_str.replace("：", ":")
        try:
            data = json.loads(t)
        except Exception:
            raise ValueError("Gemini JSONパース失敗（前処理後）。出力:\n" + json_str) from e

    return data

# ====== セクション正規化 ======
def parse_faq_text(text: str):
    """
    'Q: ...\\nA: ...' 形式や箇条書きを list[ {question, answer} ] に変換
    """
    qa = []
    blocks = re.split(r'(?=Q[:：])', text.strip())
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        m_q = re.search(r'^Q[:：]\s*(.+)', b)
        m_a = re.search(r'A[:：]\s*(.+)', b, re.S)
        if m_q and m_a:
            q = m_q.group(1).strip()
            a = m_a.group(1).strip()
            qa.append({"question": q, "answer": a})
        else:
            lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
            if not lines:
                continue
            q = lines[0][:80]
            a = "\n".join(lines[1:]) if len(lines) > 1 else ""
            qa.append({"question": q, "answer": a})
    if not qa:
        lines = [ln.strip("・-• ").strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines, 1):
            qa.append({"question": f"質問{i}", "answer": ln})
    return qa

def normalize_sections(sections: dict) -> dict:
    def collapse(v):
        """ dict({title, content}) → content（なければtitle+content連結） """
        if isinstance(v, dict):
            title = v.get("title") or v.get("題名") or ""
            content = v.get("content") or v.get("内容") or ""
            if content and title:
                return f"{title}\n{content}"
            return content or title
        return v

    norm = {}

    alias = {
        "intro": ["intro", "introduction", "lead"],
        "overview": ["overview", "summary", "abstract"],
        "key_points": ["key_points", "keypoints", "highlights", "bullets"],
        "methodology": ["methodology", "approach", "process", "methods"],
        "advantages": ["advantages", "pros", "benefits"],
        "disadvantages": ["disadvantages", "cons", "limitations"],
        "use_cases": ["use_cases", "cases", "examples"],
        "faq": ["faq", "qna", "questions", "faqs"],
        "future_outlook": ["future_outlook", "outlook", "roadmap"],
        "conclusion": ["conclusion", "closing", "summary_end"],
        "references": ["references", "citations", "sources"],
        "original_data": ["original_data", "独自のデータ", "custom_data", "own_data"]
    }
    reverse = {n: k for k, names in alias.items() for n in names}

    # 1) キー正規化 + 入れ子畳み込み
    for k, v in sections.items():
        std = reverse.get(k, k)
        norm[std] = collapse(v)

    # 2) 型の矯正
    if "key_points" in norm and isinstance(norm["key_points"], str):
        items = [s.strip("・-• ").strip() for s in norm["key_points"].splitlines() if s.strip()]
        norm["key_points"] = [i for i in items if i]

    if "faq" in norm:
        if isinstance(norm["faq"], str):
            norm["faq"] = parse_faq_text(norm["faq"])
        elif isinstance(norm["faq"], list) and norm["faq"] and isinstance(norm["faq"][0], str):
            norm["faq"] = parse_faq_text("\n".join(norm["faq"]))

    if "references" in norm:
        refs = norm["references"]
        urls = []
        if isinstance(refs, str):
            urls = re.findall(r"https?://[^\s)\]]+", refs)
        elif isinstance(refs, list):
            for r in refs:
                if isinstance(r, str):
                    urls += re.findall(r"https?://[^\s)\]]+", r)
        norm["references"] = list(dict.fromkeys(urls))

    return norm

# ====== JSON-LD 構造化データ ======
def build_jsonld(article: dict) -> str:
    faq_entities = []
    faq = article["sections"].get("faq")
    if isinstance(faq, list) and faq and isinstance(faq[0], dict):
        for qa in faq:
            faq_entities.append({
                "@type": "Question",
                "name": qa.get("question", ""),
                "acceptedAnswer": {"@type": "Answer", "text": qa.get("answer", "")}
            })

    jsonld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["query"],
        "datePublished": datetime.datetime.now().strftime("%Y-%m-%d"),
        "dateModified": datetime.datetime.now().strftime("%Y-%m-%d"),
        "author": {"@type": "Organization", "name": "Local AI Writer"},
        "mainEntity": []
    }
    if faq_entities:
        jsonld["mainEntity"].append({"@type": "FAQPage", "mainEntity": faq_entities})
    return json.dumps(jsonld, ensure_ascii=False, indent=2)

# ====== マークアップHTML生成（頑丈版） ======
def safe_p(x):
    if isinstance(x, list):
        return "<br>".join([str(i) for i in x])
    return str(x)

def render_marked_up_article(sections: dict) -> str:
    html = []

    if sections.get("intro"):
        html.append(f"<h2>イントロ</h2><p>{safe_p(sections['intro'])}</p>")
    if sections.get("overview"):
        html.append(f"<h2>概要</h2><p>{safe_p(sections['overview'])}</p>")
    if sections.get("key_points"):
        html.append("<h2>ポイント</h2><ul>")
        items = sections["key_points"] if isinstance(sections["key_points"], list) else [sections["key_points"]]
        for p in items:
            html.append(f"<li>{p}</li>")
        html.append("</ul>")
    if sections.get("methodology"):
        html.append(f"<h2>方法</h2><p>{safe_p(sections['methodology'])}</p>")
    if sections.get("advantages"):
        html.append("<h2>メリット</h2>")
        adv = sections["advantages"]
        if isinstance(adv, list):
            html.append("<ul>" + "".join(f"<li>{a}</li>" for a in adv) + "</ul>")
        else:
            html.append(f"<p>{adv}</p>")
    if sections.get("disadvantages"):
        html.append("<h2>デメリット</h2>")
        dis = sections["disadvantages"]
        if isinstance(dis, list):
            html.append("<ul>" + "".join(f"<li>{d}</li>" for d in dis) + "</ul>")
        else:
            html.append(f"<p>{dis}</p>")
    if sections.get("use_cases"):
        html.append("<h2>ユースケース</h2>")
        uc = sections["use_cases"]
        if isinstance(uc, list):
            html.append("<ul>" + "".join(f"<li>{u}</li>" for u in uc) + "</ul>")
        else:
            html.append(f"<p>{uc}</p>")

    # ★ 独自データ表示
    if sections.get("original_data"):
        html.append(f"<h2>独自のデータ</h2><p>{safe_p(sections['original_data'])}</p>")

    if sections.get("faq"):
        html.append("<h2>FAQ</h2>")
        faq = sections["faq"]
        if isinstance(faq, list) and faq and isinstance(faq[0], dict):
            for qa in faq:
                html.append(f"<h3>{qa.get('question','')}</h3><p>{qa.get('answer','')}</p>")
        else:
            html.append(f"<p>{safe_p(faq)}</p>")
    if sections.get("future_outlook"):
        html.append(f"<h2>今後の見通し</h2><p>{safe_p(sections['future_outlook'])}</p>")
    if sections.get("conclusion"):
        html.append(f"<h2>まとめ</h2><p>{safe_p(sections['conclusion'])}</p>")
    if sections.get("references"):
        refs = sections["references"] if isinstance(sections["references"], list) else [sections["references"]]
        html.append("<h2>参考資料</h2><ul>" + "".join(
            f"<li><a href='{r}' target='_blank' rel='nofollow noopener'>{r}</a></li>" for r in refs
        ) + "</ul>")

    return "\n".join(html)

# ====== LLM TXT 保存 ======
def save_llm_txt(query: str, sections: dict) -> str:
    base = pathlib.Path("static/llm")
    base.mkdir(parents=True, exist_ok=True)
    fname = base / f"llm_{query.replace(' ', '_')}.txt"
    lines = [
        f"Article Title: {query}",
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Sections:"
    ]
    for key in sections.keys():
        lines.append(f"- {key}")
    refs = sections.get("references")
    if refs:
        lines.append("\nReferences:")
        if isinstance(refs, list):
            lines.extend([f"- {r}" for r in refs])
        else:
            lines.append(f"- {refs}")
    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[+] LLM TXT 保存: {fname}")
    return str(fname)

# ====== グローバル状態（簡易） ======
current_article = {"query": "", "sections": {}}

# ====== ルート（生成＆タブUI） ======
@app.route("/", methods=["GET", "POST"])
def index():
    global current_article
    jsonld = ""
    markup = ""
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        custom_data = request.form.get("custom_data", "").strip()
        if not query:
            abort(400, "記事テーマを入力してください。")

        serp = serper_search(query)
        sections_raw = gemini_generate_sections(query, serp, custom_data)
        sections = normalize_sections(sections_raw)
        # 独自データがあるのに original_data が無い場合、明示的に差し込む
        if custom_data and not sections.get("original_data"):
            sections["original_data"] = custom_data

        current_article = {"query": query, "sections": sections}
        jsonld = build_jsonld(current_article)
        markup = render_marked_up_article(sections)
        save_llm_txt(query, sections)

    return render_template_string("""
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>SEO記事生成（編集＋再生成＋マークアップ＋LLM TXT＋JSON-LD＋評価）</title>
<style>
  .tabs button { margin-right: 8px; }
  .tab { display:none; }
  .tab.active { display:block; }
  textarea { font-family: inherit; }
  .row { margin: 6px 0; }
</style>
</head>
<body>
  <h1>SEO記事生成（編集＋再生成＋マークアップ＋LLM TXT＋JSON-LD＋評価）</h1>
  <form method="post">
      <div class="row">
        <input type="text" name="query" placeholder="どのようなSEO記事を作りますか？（テーマ）" style="width:420px">
      </div>
      <div class="row">
        <textarea name="custom_data" placeholder="独自のデータ（任意。数値、事例、社内知見などをそのまま貼り付け）" style="width:420px;height:120px;"></textarea>
      </div>
      <button type="submit">生成</button>
  </form>

  {% if current_article.sections %}
    <hr>
    <h2>記事: {{ current_article.query }}</h2>

    <div class="tabs">
      <button onclick="showTab('article')">記事ビュー</button>
      <button onclick="showTab('markup')">マークアッププレビュー</button>
      <button onclick="showTab('jsonld')">JSON-LD</button>
      <button onclick="evaluateArticle()">AI評価</button>
    </div>

    <div id="tab-article" class="tab active">
      {% for sec, text in current_article.sections.items() %}
        <div id="sec-{{sec}}" style="margin-bottom:20px;">
          <h3>{{sec}}</h3>
          <div class="content">{{text|safe}}</div>
          <button onclick="startEdit('{{sec}}')">編集</button>
          <button onclick="regenerate('{{sec}}')">再生成</button>
        </div>
      {% endfor %}
    </div>

    <div id="tab-markup" class="tab">
      <h3>マークアップ済み記事</h3>
      <div class="content">{{ markup|safe }}</div>
    </div>

    <div id="tab-jsonld" class="tab">
      <h3>JSON-LD 構造化データ</h3>
      <pre style="white-space:pre-wrap;">{{ jsonld }}</pre>
    </div>

    <div id="evaluation" class="tab">
      <h3>AI評価</h3>
      <pre id="evaluation-pre" style="white-space:pre-wrap;"></pre>
    </div>
  {% endif %}

<script>
function showTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
}

function startEdit(section) {
  const div = document.querySelector("#sec-" + section + " .content");
  const current = div.innerHTML; // 改行・リスト保持
  div.innerHTML = `
    <textarea id="edit-${section}" style="width:100%;height:150px;">${current}</textarea>
    <br>
    <button onclick="saveEdit('${section}')">保存</button>
  `;
}

async function saveEdit(section) {
  const newText = document.querySelector("#edit-" + section).value;
  const res = await fetch("/edit", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: section, new_text: newText})
  });
  const data = await res.json();
  document.querySelector("#sec-" + section + " .content").innerHTML = data.new_text;

  // タブも同期更新
  const markupEl = document.querySelector("#tab-markup .content");
  if (markupEl) markupEl.innerHTML = data.markup;
  const jsonldEl = document.querySelector("#tab-jsonld pre");
  if (jsonldEl) jsonldEl.textContent = data.jsonld;
}

async function regenerate(section) {
  const current = document.querySelector("#sec-" + section + " .content").innerHTML;
  const res = await fetch("/regenerate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: section, current_text: current})
  });
  const data = await res.json();
  document.querySelector("#sec-" + section + " .content").innerHTML = data.new_text;

  const markupEl = document.querySelector("#tab-markup .content");
  if (markupEl) markupEl.innerHTML = data.markup;
  const jsonldEl = document.querySelector("#tab-jsonld pre");
  if (jsonldEl) jsonldEl.textContent = data.jsonld;
}

async function evaluateArticle() {
  const res = await fetch("/evaluate", { method: "POST" });
  const data = await res.json();
  // JSON評価を見やすく整形
  const pretty = JSON.stringify(data, null, 2);
  document.getElementById("evaluation-pre").textContent = pretty;
  showTab("evaluation");
}
</script>
</body>
</html>
    """, current_article=current_article, jsonld=jsonld, markup=markup)

# ====== 編集API ======
@app.route("/edit", methods=["POST"])
def edit():
    global current_article
    data = request.get_json(force=True)
    sec = data.get("section")
    new_text = data.get("new_text", "")
    if not sec or sec not in current_article["sections"]:
        abort(400, "不正なセクション名です。")
    current_article["sections"][sec] = new_text
    current_article["sections"] = normalize_sections(current_article["sections"])
    markup = render_marked_up_article(current_article["sections"])
    jsonld = build_jsonld(current_article)
    return jsonify({"new_text": new_text, "markup": markup, "jsonld": jsonld})

# ====== 再生成API ======
def gemini_regenerate_section(section_name: str, current_text: str, query: str) -> str:
    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    model = genai.GenerativeModel(MODEL_GEMINI)
    system_prompt = (
        f"次の記事テーマに関する {section_name} セクションを改善してください。"
        "出力はプレーンテキストのみ。箇条書きや改行はそのまま使って良い。"
        "可能であれば独自データ(original_data)との整合性・参照も強化してください。"
    )
    user_prompt = f"""
# テーマ
{query}

# 現在の {section_name} テキスト
{current_text}
"""
    resp = model.generate_content([system_prompt, user_prompt])
    return (resp.text or "").strip()

@app.route("/regenerate", methods=["POST"])
def regenerate():
    global current_article
    data = request.get_json(force=True)
    sec = data.get("section")
    old_text = data.get("current_text", "")
    if not sec or sec not in current_article["sections"]:
        abort(400, "不正なセクション名です。")
    new_text = gemini_regenerate_section(sec, old_text, current_article["query"])
    current_article["sections"][sec] = new_text
    current_article["sections"] = normalize_sections(current_article["sections"])
    markup = render_marked_up_article(current_article["sections"])
    jsonld = build_jsonld(current_article)
    return jsonify({"new_text": new_text, "markup": markup, "jsonld": jsonld})

# ====== 評価API（AIレビュー） ======
@app.route("/evaluate", methods=["POST"])
def evaluate():
    global current_article
    if not current_article["sections"]:
        abort(400, "記事がまだありません。先に生成してください。")

    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    model = genai.GenerativeModel(MODEL_GEMINI)

    article_json = json.dumps(current_article, ensure_ascii=False, indent=2)
    prompt = (
        "以下のSEO記事（JSON）を評価してください。"
        "JSONだけで返してください。"
        "キー: {"
        "\"comprehensiveness\": {\"score\":1-5, \"reason\":\"...\"}, "
        "\"readability\": {\"score\":1-5, \"reason\":\"...\"}, "
        "\"authority\": {\"score\":1-5, \"reason\":\"...\"}, "
        "\"seo_fitness\": {\"score\":1-5, \"reason\":\"...\"}, "
        "\"improvement_suggestions\": [\"...\", \"...\"], "
        "\"overall_comment\": \"...\"}"
        "。独自データ(original_data)の活用度合いも authority に反映してください。"
    )
    resp = model.generate_content([prompt, article_json])
    raw = (resp.text or "").strip()

    # コードフェンス剥がし＆末尾カンマ等の救済
    s = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    m = re.search(r"\{.*\}\s*$", s, re.S)
    if not m:
        # 最低限テキストで返す
        return jsonify({"raw": raw})
    json_str = m.group(0)
    try:
        data = json.loads(json_str)
    except Exception:
        data = {"raw": raw}

    return jsonify(data)

# ====== 起動 ======
if __name__ == "__main__":
    # 依存: pip install flask requests google-generativeai
    # 環境変数: setx SERPER_API_KEY "xxxx", setx GEMINI_API_KEY "xxxx" → 新しいターミナルで起動
    app.run(debug=True)
