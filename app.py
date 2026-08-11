import csv
import functools
import io
from collections import defaultdict
from datetime import date, datetime

from flask import (Flask, render_template, request, redirect, url_for,
                    session, flash, jsonify, abort)

from models import db, User, Farm, Group, Drug, Treatment, seed_if_empty, resistance_score

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///herdwatch.db"
app.config["SECRET_KEY"] = "herdwatch-dev-secret"  # replace with a real secret in production
db.init_app(app)

with app.app_context():
    db.create_all()
    seed_if_empty()

ROLES = {
    "farmer": {"label": "Farmer", "tabs": ["dashboard", "treatments", "groups", "upload", "compliance"]},
    "vet": {"label": "Veterinarian", "tabs": ["dashboard", "treatments", "groups", "upload", "compliance", "analytics", "catalog"]},
    "regulator": {"label": "Regulator", "tabs": ["dashboard", "compliance", "analytics", "catalog"]},
}
TAB_LABELS = {
    "dashboard": "Dashboard", "treatments": "Treatment Log", "groups": "Herds & Farms",
    "upload": "Import Data", "compliance": "Compliance & Alerts",
    "analytics": "Resistance Risk", "catalog": "Drug Catalog",
}

# Which roles are allowed to perform each write action. Regulators are
# read-only across the whole app — they can see everything but change
# nothing. Farms are only created/managed by vets; farmers and vets can
# both log treatments, register herd groups, import CSVs, and clear
# withdrawal periods, but a farmer can only ever act on their own farm.
PERMISSIONS = {
    "add_treatment": {"farmer", "vet"},
    "add_farm": {"vet"},
    "add_group": {"farmer", "vet"},
    "upload_csv": {"farmer", "vet"},
    "mark_marketed": {"farmer", "vet"},
}


# ---------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def current_role():
    u = current_user()
    return u.role if u else None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def guard_tab(tab):
    """Redirect to dashboard if the current role can't see this tab."""
    if tab not in ROLES[current_role()]["tabs"]:
        flash("Your role doesn't have access to that page.", "error")
        return redirect(url_for("dashboard"))
    return None


def require_permission(action):
    """Block the request unless the logged-in user's role may perform `action`.

    This is enforced server-side on every write route below — not just in
    the templates — so a role can't reach a POST endpoint it isn't allowed
    to use just because it knows the URL.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user.role not in PERMISSIONS.get(action, set()):
                flash("Your role doesn't have permission to do that.", "error")
                return redirect(request.referrer or url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def visible_farm_ids(user):
    """None means 'no restriction — all farms'. A list scopes to just those farms."""
    if user.role == "farmer":
        return [user.farm_id]
    return None


def owns_farm(user, farm_id):
    """Whether this user is allowed to act on records belonging to farm_id."""
    if user.role == "farmer":
        return user.farm_id == farm_id
    return user.role in ("vet", "regulator")  # regulators never reach write routes anyway


@app.context_processor
def inject_nav():
    user = current_user()
    if not user:
        return dict(roles=ROLES, current_role=None, tab_labels=TAB_LABELS, allowed_tabs=[], current_user=None,
                    permissions=PERMISSIONS)
    return dict(
        roles=ROLES, current_role=user.role, tab_labels=TAB_LABELS,
        allowed_tabs=ROLES[user.role]["tabs"], current_user=user,
        permissions=PERMISSIONS,
    )


@app.get("/login")
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        flash("Incorrect username or password.", "error")
        return redirect(url_for("login"))
    session["user_id"] = user.id
    flash(f"Signed in as {user.display_name} ({ROLES[user.role]['label']}).", "success")
    return redirect(request.args.get("next") or url_for("dashboard"))


@app.post("/logout")
def logout():
    session.pop("user_id", None)
    flash("Signed out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------
@app.get("/")
@login_required
def dashboard():
    user = current_user()
    q = Treatment.query
    farm_ids = visible_farm_ids(user)
    if farm_ids is not None:
        q = q.filter(Treatment.farm_id.in_(farm_ids))
    treatments = q.order_by(Treatment.start_date.desc()).all()

    active = withdrawal = violations = 0
    by_class = defaultdict(int)
    upcoming = []
    for t in treatments:
        label, tone, days_left = t.status()
        by_class[t.drug.drug_class] += 1
        if label == "Active Treatment":
            active += 1
        elif label == "Withdrawal Period":
            withdrawal += 1
        elif label == "Violation":
            violations += 1
        if tone == "amber":
            upcoming.append((t, label, tone, days_left))

    upcoming.sort(key=lambda x: x[3])
    upcoming = upcoming[:5]

    cia_count = sum(1 for t in treatments if t.drug.cia)
    cia_pct = round((cia_count / len(treatments)) * 100) if treatments else 0

    return render_template(
        "dashboard.html", tab="dashboard",
        active=active, withdrawal=withdrawal, violations=violations, cia_pct=cia_pct,
        class_labels=list(by_class.keys()), class_values=list(by_class.values()),
        upcoming=upcoming,
    )


# ---------------------------------------------------------------
# TREATMENTS
# ---------------------------------------------------------------
@app.get("/treatments")
@login_required
def treatments_view():
    redir = guard_tab("treatments")
    if redir:
        return redir
    user = current_user()
    farm_ids = visible_farm_ids(user)
    q = Treatment.query
    farms_q = Farm.query
    if farm_ids is not None:
        q = q.filter(Treatment.farm_id.in_(farm_ids))
        farms_q = farms_q.filter(Farm.id.in_(farm_ids))
    treatments = q.order_by(Treatment.start_date.desc()).all()
    farms = farms_q.all()
    drugs = Drug.query.order_by(Drug.name).all()
    rows = [(t, t.status()) for t in treatments]
    return render_template("treatments.html", tab="treatments", rows=rows, farms=farms, drugs=drugs)


@app.post("/treatments/add")
@login_required
@require_permission("add_treatment")
def add_treatment():
    user = current_user()
    try:
        farm_id = int(request.form["farm_id"])
        if not owns_farm(user, farm_id):
            flash("You can only log treatments for your own farm.", "error")
            return redirect(url_for("treatments_view"))
        t = Treatment(
            farm_id=farm_id,
            group_id=int(request.form["group_id"]),
            drug_id=int(request.form["drug_id"]),
            dose=request.form.get("dose", ""),
            unit=request.form.get("unit", ""),
            start_date=datetime.strptime(request.form["start_date"], "%Y-%m-%d").date(),
            end_date=datetime.strptime(request.form["end_date"], "%Y-%m-%d").date(),
            reason=request.form.get("reason", ""),
            prescribed_by=request.form.get("prescribed_by", ""),
            logged_by=user.username,
        )
        db.session.add(t)
        db.session.commit()
        flash("Treatment logged.", "success")
    except Exception:
        flash("Couldn't save that treatment — check the required fields.", "error")
    return redirect(url_for("treatments_view"))


@app.get("/api/groups-for-farm/<int:farm_id>")
@login_required
def groups_for_farm(farm_id):
    user = current_user()
    if not owns_farm(user, farm_id):
        return jsonify([])
    groups = Group.query.filter_by(farm_id=farm_id).all()
    return jsonify([{"id": g.id, "name": g.name} for g in groups])


# ---------------------------------------------------------------
# GROUPS / FARMS
# ---------------------------------------------------------------
@app.get("/groups")
@login_required
def groups_view():
    redir = guard_tab("groups")
    if redir:
        return redir
    user = current_user()
    farm_ids = visible_farm_ids(user)
    farms_q = Farm.query
    groups_q = Group.query
    if farm_ids is not None:
        farms_q = farms_q.filter(Farm.id.in_(farm_ids))
        groups_q = groups_q.filter(Group.farm_id.in_(farm_ids))
    return render_template("groups.html", tab="groups", farms=farms_q.all(), groups=groups_q.all())


@app.post("/groups/add-farm")
@login_required
@require_permission("add_farm")
def add_farm():
    name = request.form.get("name", "").strip()
    if name:
        db.session.add(Farm(name=name, location=request.form.get("location", ""), species=request.form.get("species", "")))
        db.session.commit()
        flash("Farm added.", "success")
    return redirect(url_for("groups_view"))


@app.post("/groups/add-group")
@login_required
@require_permission("add_group")
def add_group():
    user = current_user()
    name = request.form.get("name", "").strip()
    farm_id = request.form.get("farm_id")
    if name and farm_id and owns_farm(user, int(farm_id)):
        db.session.add(Group(
            farm_id=int(farm_id), name=name, species=request.form.get("species", ""),
            product=request.form.get("product", "meat"),
            headcount=int(request.form.get("headcount") or 0),
        ))
        db.session.commit()
        flash("Herd group added.", "success")
    elif farm_id and not owns_farm(user, int(farm_id)):
        flash("You can only add herd groups to your own farm.", "error")
    return redirect(url_for("groups_view"))


# ---------------------------------------------------------------
# CSV UPLOAD
# ---------------------------------------------------------------
@app.get("/upload")
@login_required
def upload_view():
    redir = guard_tab("upload")
    if redir:
        return redir
    return render_template("upload.html", tab="upload")


@app.post("/upload")
@login_required
@require_permission("upload_csv")
def upload_csv():
    user = current_user()
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Choose a CSV file first.", "error")
        return redirect(url_for("upload_view"))

    stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
    reader = csv.DictReader(stream)

    farms_by_name = {f.name.lower(): f for f in Farm.query.all()}
    drugs_by_name = {d.name.lower(): d for d in Drug.query.all()}

    imported, skipped = 0, []
    for idx, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        farm = farms_by_name.get((row.get("farm") or "").lower())
        group = None
        if farm:
            group = Group.query.filter(Group.farm_id == farm.id,
                                        db.func.lower(Group.name) == (row.get("group") or row.get("herd") or "").lower()).first()
        drug = drugs_by_name.get((row.get("drug") or "").lower())
        start_date = row.get("startDate") or row.get("start_date")
        end_date = row.get("endDate") or row.get("end_date") or start_date

        if not farm:
            skipped.append((idx, "unknown farm")); continue
        if not owns_farm(user, farm.id):
            skipped.append((idx, "not your farm")); continue
        if not group:
            skipped.append((idx, "unknown group")); continue
        if not drug:
            skipped.append((idx, "unknown drug")); continue
        if not start_date:
            skipped.append((idx, "missing start date")); continue

        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            skipped.append((idx, "bad date format (use YYYY-MM-DD)")); continue

        marketed = row.get("marketedDate")
        md = None
        if marketed:
            try:
                md = datetime.strptime(marketed, "%Y-%m-%d").date()
            except ValueError:
                pass

        db.session.add(Treatment(
            farm_id=farm.id, group_id=group.id, drug_id=drug.id,
            dose=row.get("dose", ""), unit=row.get("unit", ""),
            start_date=sd, end_date=ed,
            reason=row.get("reason", ""), prescribed_by=row.get("prescribedBy") or row.get("vet", ""),
            marketed_date=md, logged_by=user.username,
        ))
        imported += 1

    db.session.commit()
    flash(f"Imported {imported} record(s). {len(skipped)} skipped.", "success" if imported else "error")
    return render_template("upload.html", tab="upload", imported=imported, skipped=skipped)


# ---------------------------------------------------------------
# COMPLIANCE
# ---------------------------------------------------------------
@app.get("/compliance")
@login_required
def compliance_view():
    redir = guard_tab("compliance")
    if redir:
        return redir
    user = current_user()
    farm_ids = visible_farm_ids(user)
    q = Treatment.query
    if farm_ids is not None:
        q = q.filter(Treatment.farm_id.in_(farm_ids))
    treatments = q.all()
    violations = [(t, t.status()) for t in treatments if t.status()[0] == "Violation"]
    in_withdrawal = [(t, t.status()) for t in treatments if t.status()[1] == "amber"]
    in_withdrawal.sort(key=lambda x: x[1][2])
    return render_template("compliance.html", tab="compliance", violations=violations, in_withdrawal=in_withdrawal)


@app.post("/compliance/mark-marketed/<int:treatment_id>")
@login_required
@require_permission("mark_marketed")
def mark_marketed(treatment_id):
    user = current_user()
    t = Treatment.query.get_or_404(treatment_id)
    if not owns_farm(user, t.farm_id):
        flash("You can only update records for your own farm.", "error")
        return redirect(url_for("compliance_view"))
    t.marketed_date = date.today()
    db.session.commit()
    flash("Marked as marketed today.", "success")
    return redirect(url_for("compliance_view"))


# ---------------------------------------------------------------
# ANALYTICS
# ---------------------------------------------------------------
@app.get("/analytics")
@login_required
def analytics_view():
    redir = guard_tab("analytics")
    if redir:
        return redir
    farms = Farm.query.all()
    farm_scores = [(f, resistance_score(f.id)) for f in farms]

    treatments = Treatment.query.all()
    cia_count = sum(1 for t in treatments if t.drug.cia)
    non_cia_count = len(treatments) - cia_count

    monthly = defaultdict(int)
    for t in treatments:
        monthly[t.start_date.strftime("%Y-%m")] += 1
    months = sorted(monthly.keys())
    monthly_values = [monthly[m] for m in months]

    return render_template(
        "analytics.html", tab="analytics", farm_scores=farm_scores,
        cia_count=cia_count, non_cia_count=non_cia_count,
        months=months, monthly_values=monthly_values,
    )


# ---------------------------------------------------------------
# CATALOG
# ---------------------------------------------------------------
@app.get("/catalog")
@login_required
def catalog_view():
    redir = guard_tab("catalog")
    if redir:
        return redir
    return render_template("catalog.html", tab="catalog", drugs=Drug.query.order_by(Drug.name).all())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
