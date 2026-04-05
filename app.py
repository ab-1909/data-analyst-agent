"""
Privacy-First Interactive Data Analyst Agent — Flask Backend
=============================================================
"""

import os, glob, time, json, traceback
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory
import pandas as pd
import matplotlib
matplotlib.use("Agg")                       
import matplotlib.pyplot as plt
import seaborn as sns

try:
    from pandasai import SmartDataframe
    from pandasai.llm import OpenAI
    PANDASAI_AVAILABLE = True
except ImportError:
    PANDASAI_AVAILABLE = False

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024      

BASE_DIR   = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CHART_DIR  = BASE_DIR / "static" / "charts"
UPLOAD_DIR.mkdir(exist_ok=True)
CHART_DIR.mkdir(parents=True, exist_ok=True)

current_df: pd.DataFrame | None = None
current_sdf = None                                        
current_filename: str = ""

def _dtype_friendly(dtype) -> str:
    s = str(dtype)
    if "int" in s:   return "Integer"
    if "float" in s: return "Float"
    if "bool" in s:  return "Boolean"
    if "datetime" in s: return "DateTime"
    return "String"

def _build_schema(df: pd.DataFrame) -> dict:
    return {
        "filename": current_filename,
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "fields": [
            {"name": col, "dtype": _dtype_friendly(df[col].dtype)}
            for col in df.columns
        ],
        "head": df.head(5).fillna("").to_dict(orient="records"),   
    }

def _get_llm():
    api_key = os.environ.get("PANDASAI_API_KEY", "")
    if not api_key:
        return None
    return OpenAI(api_token=api_key)

def _collect_new_charts(before_set: set[str]) -> list[str]:
    after = {p.name for p in CHART_DIR.glob("*.png")}
    return sorted(after - before_set)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    global current_df, current_sdf, current_filename
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Empty filename"}), 400
        if not file.filename.lower().endswith(".csv"):
            return jsonify({"error": "Only .csv files are supported"}), 400

        filepath = UPLOAD_DIR / file.filename
        file.save(str(filepath))

        current_df = pd.read_csv(filepath)
        current_filename = file.filename

        if PANDASAI_AVAILABLE:
            llm = _get_llm()
            if llm:
                current_sdf = SmartDataframe(
                    current_df,
                    config={
                        "llm": llm,
                        "save_charts": True,
                        "save_charts_path": str(CHART_DIR),
                        "enable_cache": False,
                    },
                )
            else:
                current_sdf = None

        schema = _build_schema(current_df)
        return jsonify({"status": "ok", "schema": schema})

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"Upload failed: {exc}"}), 500

@app.route("/ask", methods=["POST"])
def ask():
    global current_df, current_sdf
    try:
        if current_df is None:
            return jsonify({"error": "No dataset loaded. Please upload a CSV first."}), 400

        body = request.get_json(silent=True) or {}
        question = body.get("question", "").strip()
        if not question:
            return jsonify({"error": "Empty question"}), 400

        if not PANDASAI_AVAILABLE or current_sdf is None:
            answer = _fallback_answer(question)
            if answer.startswith("CHART_URL:"):
                return jsonify({
                    "type": "chart",
                    "answer": "Here is the chart you requested:",
                    "charts": [answer.replace("CHART_URL:", "")]
                })
            return jsonify({"type": "text", "answer": answer, "charts": []})

        charts_before = {p.name for p in CHART_DIR.glob("*.png")}
        response = current_sdf.chat(question)
        new_charts = _collect_new_charts(charts_before)
        chart_urls = [f"/static/charts/{c}" for c in new_charts]

        if new_charts:
            return jsonify({
                "type": "chart",
                "answer": str(response) if response else "Here's the chart I generated:",
                "charts": chart_urls,
            })
        else:
            return jsonify({
                "type": "text",
                "answer": str(response),
                "charts": [],
            })

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"Analysis failed: {exc}"}), 500

def _fallback_answer(question: str) -> str:
    q = question.lower()
    df = current_df

    if any(w in q for w in ["describe", "summary", "statistic", "stats"]):
        return f"📊 Dataset Summary:\n```\n{df.describe().to_string()}\n```"
    if any(w in q for w in ["shape", "size", "how many rows", "how many columns"]):
        return f"The dataset has **{df.shape[0]} rows** and **{df.shape[1]} columns**."
    if any(w in q for w in ["columns", "column names", "fields"]):
        return f"Columns: {', '.join(df.columns.tolist())}"
    if any(w in q for w in ["null", "missing", "nan"]):
        nulls = df.isnull().sum()
        return f"Missing values per column:\n```\n{nulls.to_string()}\n```"
    if any(w in q for w in ["head", "first", "preview", "sample"]):
        return f"First 5 rows:\n```\n{df.head().to_string()}\n```"
    if any(w in q for w in ["correlation", "corr"]):
        numeric = df.select_dtypes(include="number")
        if numeric.empty:
            return "No numeric columns found for correlation."
        return f"Correlation matrix:\n```\n{numeric.corr().to_string()}\n```"
    if any(w in q for w in ["unique", "distinct"]):
        uniques = {col: int(df[col].nunique()) for col in df.columns}
        return f"Unique value counts:\n```\n{json.dumps(uniques, indent=2)}\n```"

    if any(w in q for w in ["plot", "chart", "graph", "visuali", "bar", "hist", "scatter", "line"]):
        return _fallback_chart(question)

    return (
        f"I can answer basic questions about your **{current_filename}** dataset "
        f"({df.shape[0]} rows × {df.shape[1]} cols). "
        f"Try: *describe, shape, columns, missing values, correlation, unique values, "
        f"or ask me to plot a chart.*"
    )

def _fallback_chart(question: str) -> str:
    df = current_df.copy()
    q = question.lower()

    target_num = None
    for col in df.columns:
        if col.lower() in q:
            target_num = col
            break
            
    if not target_num:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if not numeric_cols:
            return "No numeric columns available for charting."
        target_num = numeric_cols[0]

    if not pd.api.types.is_numeric_dtype(df[target_num]):
        df[target_num] = pd.to_numeric(
            df[target_num].astype(str).str.replace(r'[,\$\s]', '', regex=True), 
            errors='coerce'
        )

    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    target_cat = cat_cols[0] if cat_cols else None
    for col in cat_cols:
        if col.lower() in q and col != target_num:
            target_cat = col
            break

    # --- THIS IS THE MAGIC DARK MODE FIX ---
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.set_theme(style="darkgrid", rc={"axes.facecolor": "#1e1b4b", "grid.color": "#334155", "text.color": "white", "axes.labelcolor": "white", "xtick.color": "white", "ytick.color": "white"})
    # ---------------------------------------

    # 1. ADD "PIE" TO THE RECOGNIZED WORDS
    chart_type = "histogram"
    if "bar" in q: chart_type = "bar"
    elif "scatter" in q: chart_type = "scatter"
    elif "line" in q: chart_type = "line"
    elif "pie" in q: chart_type = "pie"

    try:
        if chart_type == "histogram":
            ax.hist(df[target_num].dropna(), bins=20, color="#7c3aed", edgecolor="#1e1b4b", alpha=0.85)
            ax.set_title(f"Distribution of {target_num}", fontsize=14, fontweight="bold")
            ax.set_xlabel(target_num)
            ax.set_ylabel("Frequency")

        elif chart_type == "bar":
            if target_cat:
                top = df.groupby(target_cat)[target_num].mean().nlargest(10)
                top.plot(kind="bar", ax=ax, color="#7c3aed", edgecolor="#1e1b4b")
                ax.set_title(f"Top 10: Mean {target_num} by {target_cat}", fontsize=14, fontweight="bold")
            else:
                df[target_num].dropna().head(20).plot(kind="bar", ax=ax, color="#7c3aed")
                ax.set_title(f"Bar chart of {target_num}", fontsize=14, fontweight="bold")

        # 2. ADD THE INSTRUCTIONS FOR DRAWING THE PIE CHART
        elif chart_type == "pie":
            if target_cat:
                # Grab only the top 6 biggest slices so it looks clean
                top = df.groupby(target_cat)[target_num].sum().nlargest(6)
                # Draw the pie chart with white percentages
                top.plot(kind="pie", ax=ax, autopct='%1.1f%%', textprops={'color':"white"})
                ax.set_ylabel("") # Hide the ugly default side label
                ax.set_title(f"Top 6 Share of {target_num} by {target_cat}", fontsize=14, fontweight="bold")
            else:
                return f"I need a category to slice the pie! Try asking: 'Plot a pie chart of {target_num} by Industry'."

        
        elif chart_type == "scatter":
            second_num = None
            for col in df.columns:
                if col.lower() in q and col != target_num:
                    second_num = col
                    break
            
            if second_num:
                if not pd.api.types.is_numeric_dtype(df[second_num]):
                    df[second_num] = pd.to_numeric(df[second_num].astype(str).str.replace(r'[,\$\s]', '', regex=True), errors='coerce')
                ax.scatter(df[target_num], df[second_num], c="#7c3aed", alpha=0.6, edgecolors="#1e1b4b")
                ax.set_xlabel(target_num)
                ax.set_ylabel(second_num)
                ax.set_title(f"{target_num} vs {second_num}", fontsize=14, fontweight="bold")
            else:
                return "Need a second column name for a scatter plot."

        elif chart_type == "line":
            df[target_num].dropna().head(50).plot(ax=ax, color="#7c3aed", linewidth=2)
            ax.set_title(f"Line plot of {target_num}", fontsize=14, fontweight="bold")

        fig.tight_layout()
        chart_name = f"chart_{int(time.time())}_{chart_type}.png"
        chart_path = CHART_DIR / chart_name
        fig.savefig(str(chart_path), dpi=120, bbox_inches="tight", facecolor="#0f0a1e")
        plt.close(fig)

        chart_url = f"/static/charts/{chart_name}"
        return f"CHART_URL:{chart_url}"

    except Exception as exc:
        plt.close(fig)
        return f"Could not generate chart: {exc}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 Privacy-First Data Analyst Agent running at http://127.0.0.1:{port}\n")
    app.run(debug=True, host="0.0.0.0", port=port)