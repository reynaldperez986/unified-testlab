# Unified TestLab

Single Django application that unifies all major capabilities from:
- `C:\web__automation` (web recorder/replay automation)
- `C:\api-testlab` (API test management and execution)
- `C:\db-testlab` (database connection and SQL test execution)

## Unified Modules

- Web Automation: `/` (existing recorder module)
- API TestLab: `/api-lab/`
- DB TestLab: `/db-lab/`

## What Was Unified

- Imported API app into `api_testcases`
- Imported DB app into `db_testcases`
- Added both apps to the same Django project (`webapp`)
- Namespaced templates and URL names to avoid route/template collisions
- Added API and DB static assets to global static settings
- Merged package dependencies into one `requirements.txt`

## Setup

1. Create venv and activate

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies

```powershell
pip install -r requirements.txt
```

3. Configure environment variables

- Reuse existing web automation `.env` values for PostgreSQL.
- API and DB modules use the same Django database connection configured in `webapp/settings.py`.

4. Run migrations

```powershell
python manage.py migrate
```

5. Optional seed users

```powershell
python manage.py setup_initial
```

6. Start app

```powershell
python manage.py runserver 0.0.0.0:8000
```

## Access

- Web Automation: `http://127.0.0.1:8000/`
- API TestLab: `http://127.0.0.1:8000/api-lab/`
- DB TestLab: `http://127.0.0.1:8000/db-lab/`

## Notes

- API and DB templates were namespaced under:
  - `templates_api/api/...`
  - `templates_db/db/...`
- API and DB URL names are namespaced as `api:*` and `db:*`.
- If Oracle or SQL Server clients are not available on the machine, DB module tests for those engines will fail until their native drivers are installed.
