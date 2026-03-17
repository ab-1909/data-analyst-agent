"""
Privacy-First Interactive Data Analyst Agent — Flask Backend
=============================================================
• /upload   → accepts CSV, returns schema (columns, dtypes, shape, head preview)
• /ask      → accepts natural-language question, returns AI answer (text or chart URL)
• /charts   → lists all generated chart filenames for the gallery
"""

import os, glob, time, json, traceback
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory
import pandas as pd
import matplotlib
matplotlib.use("Agg")                       # headless backend – no GUI needed
import matplotlib.pyplot as plt
import seaborn as sns

# ---------------------------------------------------------------------------
# PandasAI imports (graceful fallback if not installed yet)
# ---------------------------------------------------------------------------
try:
    from pandasai import SmartDataframe
    from pandasai.llm import OpenAI
    PANDASAI_AVAILABLE = True
except ImportError:
    PANDASAI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Flask app & config
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024      # 50 MB upload limit

BASE_DIR   = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CHART_DIR  = BASE_DIR / "static" / "charts"
UPLOAD_DIR.mkdir(exist_ok=True)
CHART_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Global state  (single-user local tool — perfectly fine)
# ---------------------------------------------------------------------------
current_df: pd.DataFrame | None = None
current_sdf = None                                        # SmartDataframe
current_filename: str = ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dtype_friendly(dtype) -> str:
    """Return a human-readable string for a pandas dtype."""
    s = str(dtype)
    if "int" in s:   return "Integer"
    if "float" in s: return "Float"
    if "bool" in s:  return "Boolean"
    if "datetime" in s: return "DateTime"
    return "String"


def _build_schema(df: pd.DataFrame) -> dict:
    """Build a JSON-serialisable schema dict — NO raw row data leaves this fn."""
    return {
        "filename": current_filename,
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "fields": [
            {"name": col, "dtype": _dtype_friendly(df[col].dtype)}
            for col in df.columns
        ],
        "head": df.head(5).fillna("").to_dict(orient="records"),   # tiny preview only
    }


def _get_llm():
    """Instantiate the LLM. Key is read from env so the user never pastes it in the UI."""
    api_key = os.environ.get("PANDASAI_API_KEY", "")
    if not api_key:
        return None
    return OpenAI(api_token=api_key)


def _collect_new_charts(before_set: set[str]) -> list[str]:
    """Return chart filenames created since *before_set* was captured."""
    after = {p.name for p in CHART_DIR.glob("*.png")}
    return sorted(after - before_set)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Accept a CSV file, persist it, parse schema, return JSON."""
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

        # Build SmartDataframe if PandasAI is available
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
    """
    Accept a natural-language question about the loaded dataset.
    Returns either a text answer or a chart image URL.
    PRIVACY: only the schema is described to the LLM; raw rows stay local.
    """
    global current_df, current_sdf
    try:
        if current_df is None:
            return jsonify({"error": "No dataset loaded. Please upload a CSV first."}), 400

        body = request.get_json(silent=True) or {}
        question = body.get("question", "").strip()
        if not question:
            return jsonify({"error": "Empty question"}), 400

        # ----- Fallback: no PandasAI or no API key --------------------------
        if not PANDASAI_AVAILABLE or current_sdf is None:
            # Provide a basic statistical answer using pure Pandas
            answer = _fallback_answer(question)
            return jsonify({"type": "text", "answer": answer, "charts": []})

        # ----- PandasAI path ------------------------------------------------
        charts_before = {p.name for p in CHART_DIR.glob("*.png")}

        response = current_sdf.chat(question)

        new_charts = _collect_new_charts(charts_before)
        chart_urls = [f"/static/charts/{c}" for c in new_charts]

        # Determine response type
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
    """Provide a basic answer using pure Pandas when PandasAI is unavailable."""
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

    # If question asks for a plot, generate one with matplotlib
    if any(w in q for w in ["plot", "chart", "graph", "visuali", "bar", "hist", "scatter", "line"]):
        return _fallback_chart(question)

    # Default
    return (
        f"I can answer basic questions about your **{current_filename}** dataset "
        f"({df.shape[0]} rows × {df.shape[1]} cols). "
        f"Try: *describe, shape, columns, missing values, correlation, unique values, "
        f"or ask me to plot a chart.*"
    )


def _fallback_chart(question: str) -> str:
    """Generate a simple chart using Matplotlib when PandasAI is unavailable."""
    df = current_df
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    q = question.lower()

    if not numeric_cols:
        return "No numeric columns available for charting."

    fig, ax = plt.subplots(figsize=(8, 5))
    sns.set_theme(style="darkgrid")

    chart_type = "histogram"
    if "bar" in q:
        chart_type = "bar"
    elif "scatter" in q:
        chart_type = "scatter"
    elif "line" in q:
        chart_type = "line"
    elif "hist" in q:
        chart_type = "histogram"

    try:
        if chart_type == "histogram":
            col = numeric_cols[0]
            ax.hist(df[col].dropna(), bins=20, color="#7c3aed", edgecolor="#1e1b4b", alpha=0.85)
            ax.set_title(f"Distribution of {col}", fontsize=14, fontweight="bold")
            ax.set_xlabel(col)
            ax.set_ylabel("Frequency")

        elif chart_type == "bar":
            # Use first categorical + first numeric, or first two numeric
            cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
            if cat_cols and numeric_cols:
                top = df.groupby(cat_cols[0])[numeric_cols[0]].mean().nlargest(10)
                top.plot(kind="bar", ax=ax, color="#7c3aed", edgecolor="#1e1b4b")
                ax.set_title(f"Top 10 — Mean {numeric_cols[0]} by {cat_cols[0]}", fontsize=14, fontweight="bold")
            else:
                df[numeric_cols[0]].head(20).plot(kind="bar", ax=ax, color="#7c3aed")
                ax.set_title(f"Bar chart of {numeric_cols[0]}", fontsize=14, fontweight="bold")

        elif chart_type == "scatter" and len(numeric_cols) >= 2:
            ax.scatter(df[numeric_cols[0]], df[numeric_cols[1]], c="#7c3aed", alpha=0.6, edgecolors="#1e1b4b")
            ax.set_xlabel(numeric_cols[0])
            ax.set_ylabel(numeric_cols[1])
            ax.set_title(f"{numeric_cols[0]} vs {numeric_cols[1]}", fontsize=14, fontweight="bold")

        elif chart_type == "line":
            df[numeric_cols[0]].head(50).plot(ax=ax, color="#7c3aed", linewidth=2)
            ax.set_title(f"Line plot of {numeric_cols[0]}", fontsize=14, fontweight="bold")

        else:
            col = numeric_cols[0]
            ax.hist(df[col].dropna(), bins=20, color="#7c3aed", edgecolor="#1e1b4b", alpha=0.85)
            ax.set_title(f"Distribution of {col}", fontsize=14, fontweight="bold")

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


@app.route("/charts", methods=["GET"])
def charts():
    """Return a list of all chart image URLs in the gallery."""
    try:
        files = sorted(
            CHART_DIR.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        urls = [f"/static/charts/{p.name}" for p in files]
        return jsonify({"charts": urls})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀  Privacy-First Data Analyst Agent running at http://127.0.0.1:{port}\n")
    app.run(debug=True, host="0.0.0.0", port=port)
