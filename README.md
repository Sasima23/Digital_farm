# Herd Watch — Antibiotic Use & Safety Monitor (Flask)

A Flask web app for tracking antibiotic treatments in livestock, computing withdrawal-period
compliance, and flagging antimicrobial resistance risk.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**. A SQLite database (`herdwatch.db`) is created automatically on
first run, seeded with a few sample farms, herds, drugs, and treatments so the dashboard isn't
empty.

## Features

- **Role switcher** (top of sidebar) — Farmer / Veterinarian / Regulator. This only changes which
  tabs are visible; it is **not** real authentication. Add login + permissions before deploying
  this beyond a trusted local/internal use case.
- **Treatment log** — record drug, dose, dates, reason, and prescribing vet per herd group.
- **Withdrawal compliance** — every treatment's withdrawal-clearance date is computed from the
  drug catalog (meat vs. dairy withdrawal periods) and shown as a status "ear tag": Active
  Treatment → Withdrawal Period → Cleared, or **Violation** if a "marketed" date is logged before
  the withdrawal period ends.
- **CSV import** — bulk-upload treatment records. Columns: `farm, group, drug, dose, unit,
  startDate, endDate, reason, prescribedBy, marketedDate` (dates as `YYYY-MM-DD`). Farm, group,
  and drug names must already exist in the system.
- **Resistance risk analytics** — a simplified per-farm score weighted toward WHO Critically
  Important Antimicrobials (fluoroquinolones, 3rd-gen cephalosporins, macrolides) and repeated use
  of the same drug class within 90 days. This is a screening indicator, not a diagnostic or
  regulatory determination.
- **Drug catalog** — reference withdrawal periods. **The seeded values are illustrative
  placeholders** — always confirm actual withdrawal periods against the current product label and
  a licensed veterinarian; they vary by country, formulation, and regulatory body.

## Project structure

```
app.py              Flask routes
models.py            SQLAlchemy models + compliance/resistance logic + seed data
templates/           Jinja2 templates
static/style.css     Design system (colors, type, components)
```

## Notes on taking this to production

- Replace `SECRET_KEY` in `app.py` with a securely generated value (e.g. `secrets.token_hex(32)`),
  read from an environment variable.
- Swap SQLite for Postgres/MySQL for multi-user concurrent writes.
- Add real authentication (Flask-Login or similar) instead of the role dropdown.
- Run behind a production WSGI server (gunicorn/uWSGI) and a reverse proxy, not `app.run(debug=True)`.
- Consider audit logging (who changed what, when) given this is compliance-relevant data.
