# NetDiagnose AI — Setup Guide (Step 1)

Work locally first. No Docker until Week 8.

## 1. Install

| Tool | Version | Check |
|---|---|---|
| Python | 3.11+ | `python --version` |
| Git | any recent | `git --version` |
| VS Code | latest | extensions: Python, Pylance, Jupyter |
| PostgreSQL | 15+ (needed from Week 4) | `psql --version` |

## 2. Create the project

```bash
cd netdiagnose-ai
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
git init && git add . && git commit -m "chore: project skeleton"
```

In VS Code choose the `.venv` interpreter (Ctrl+Shift+P → *Python: Select Interpreter*).

## 3. Get the dataset

1. Download **UNSW-NB15** from the official UNSW Canberra page.
2. Use the *training-set* and *testing-set* CSV files (they have headers and labels).
3. Put them here:

```text
data/raw/UNSW_NB15_training-set.csv
data/raw/UNSW_NB15_testing-set.csv
```

Also keep the feature description file for reference. The dataset is git-ignored.

## 4. First code to run

Create `notebooks/01_exploration.ipynb` and run:

```python
import pandas as pd

df = pd.read_csv("../data/raw/UNSW_NB15_training-set.csv")
print(df.shape)
print(df.head())
print(df.columns.tolist())
print(df.isnull().sum())
print(df["label"].value_counts())
print(df["attack_cat"].value_counts())
```

(`../` because notebooks run from the `notebooks/` folder.)

## 5. Database setup (Week 4)

Create the app user and database once, as the `postgres` superuser (asks for its password):

```powershell
# Windows (PowerShell); psql is not on PATH by default
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost `
  -c "CREATE USER netdiag WITH PASSWORD 'netdiag';" `
  -c "CREATE DATABASE netdiagnose OWNER netdiag;"
```

Then, from the project root with the venv active:

```bash
cp .env.example .env                      # if you have not already
python -m backend.db.init_db              # apply db/schema.sql + seed 8 devices (safe to re-run)
python -m backend.db.load_dataset         # load the testing set (82k rows) and score it
pytest -q                                 # DB tests now run instead of skipping
```

Useful options: `--limit 60000` (sample), `--days 3` (time span), `--no-score`, `--score-only`.
Analysis queries are in `db/queries.sql`.

## 6. Run the services (Weeks 5–6)

```bash
uvicorn backend.main:app --reload          # http://localhost:8000/docs
streamlit run dashboard/app.py             # http://localhost:8501
pytest -q
```

## 7. Docker (Week 8)

```bash
cp .env.example .env
docker compose up --build
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `psycopg2` build error | Use `psycopg2-binary` (already in requirements) |
| `ModuleNotFoundError: ml` | Run commands from the project root, or `pip install -e .` once you add a `pyproject.toml` |
| `connection refused` on 5432 | Start the PostgreSQL service; verify port and credentials in `.env` |
| Streamlit cannot reach API | Check `API_BASE_URL` and that uvicorn is running |
| Notebook can't find CSV | Check the relative path from the notebook's folder |
