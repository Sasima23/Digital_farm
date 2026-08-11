from datetime import date, timedelta
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    """A logged-in account. Each account has exactly one role.

    Farmers are additionally scoped to a single farm (farm_id) — they can
    only see and act on that farm's records. Vets and regulators are not
    farm-scoped: vets service multiple farms, regulators audit all of them.
    """
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'farmer' | 'vet' | 'regulator'
    farm_id = db.Column(db.Integer, db.ForeignKey("farm.id"), nullable=True)

    farm = db.relationship("Farm")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Farm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(120))
    species = db.Column(db.String(120))

    groups = db.relationship("Group", backref="farm", cascade="all, delete-orphan")


class Group(db.Model):
    """A herd / flock / pen within a farm."""
    id = db.Column(db.Integer, primary_key=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farm.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    species = db.Column(db.String(120))
    product = db.Column(db.String(20), default="meat")  # 'meat' or 'dairy'
    headcount = db.Column(db.Integer, default=0)


class Drug(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    drug_class = db.Column(db.String(120))
    cia = db.Column(db.Boolean, default=False)  # WHO Critically Important Antimicrobial
    meat_days = db.Column(db.Integer, default=0)   # meat withdrawal period, days
    milk_days = db.Column(db.Integer, default=0)   # milk withdrawal period, days (0 = n/a)
    notes = db.Column(db.String(300))


class Treatment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farm.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("group.id"), nullable=False)
    drug_id = db.Column(db.Integer, db.ForeignKey("drug.id"), nullable=False)
    dose = db.Column(db.String(40))
    unit = db.Column(db.String(40))
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(200))
    prescribed_by = db.Column(db.String(120))
    marketed_date = db.Column(db.Date, nullable=True)  # date animal/product was sold, if any
    logged_by = db.Column(db.String(120))  # username of the account that recorded this

    farm = db.relationship("Farm")
    group = db.relationship("Group")
    drug = db.relationship("Drug")

    # -------- compliance logic --------
    def withdrawal_days(self):
        if self.group and self.group.product == "dairy":
            return self.drug.milk_days or 0
        return self.drug.meat_days or 0

    def withdrawal_end(self):
        return self.end_date + timedelta(days=self.withdrawal_days())

    def status(self):
        """Returns (label, tone, days_left) where tone in {pasture, amber, rust, slate}."""
        today = date.today()
        w_end = self.withdrawal_end()

        if today < self.end_date:
            return "Active Treatment", "amber", (self.end_date - today).days
        if self.marketed_date:
            if self.marketed_date < w_end:
                return "Violation", "rust", 0
            return "Cleared (marketed)", "pasture", 0
        if today < w_end:
            return "Withdrawal Period", "amber", (w_end - today).days
        return "Cleared", "pasture", 0


def seed_if_empty():
    if Drug.query.first():
        return

    drugs = [
        Drug(name="Amoxicillin", drug_class="Penicillin", cia=False, meat_days=14, milk_days=3,
             notes="Broad-spectrum, common first-line."),
        Drug(name="Oxytetracycline", drug_class="Tetracycline", cia=False, meat_days=22, milk_days=4,
             notes="Long-acting injectable common in beef cattle."),
        Drug(name="Ceftiofur", drug_class="3rd-gen Cephalosporin", cia=True, meat_days=4, milk_days=0,
             notes="WHO Highest Priority Critically Important Antimicrobial."),
        Drug(name="Enrofloxacin", drug_class="Fluoroquinolone", cia=True, meat_days=12, milk_days=0,
             notes="WHO Highest Priority CIA. Not for lactating dairy in many jurisdictions."),
        Drug(name="Tylosin", drug_class="Macrolide", cia=True, meat_days=21, milk_days=4,
             notes="Macrolides are WHO High Priority CIA."),
        Drug(name="Sulfamethazine", drug_class="Sulfonamide", cia=False, meat_days=10, milk_days=4,
             notes="Often combined with trimethoprim."),
        Drug(name="Florfenicol", drug_class="Amphenicol", cia=False, meat_days=28, milk_days=0,
             notes="Not approved for lactating dairy animals."),
        Drug(name="Procaine Penicillin G", drug_class="Penicillin", cia=False, meat_days=14, milk_days=6,
             notes="Common for respiratory & foot infections."),
    ]
    db.session.add_all(drugs)
    db.session.commit()

    f1 = Farm(name="Greenfield Dairy", location="Erode, TN", species="Dairy Cattle")
    f2 = Farm(name="Hillcrest Poultry", location="Namakkal, TN", species="Broiler Chicken")
    f3 = Farm(name="Sunridge Beef Co.", location="Salem, TN", species="Beef Cattle")
    db.session.add_all([f1, f2, f3])
    db.session.commit()

    g1 = Group(farm_id=f1.id, name="Milking Herd A", species="Dairy Cattle", product="dairy", headcount=64)
    g2 = Group(farm_id=f1.id, name="Heifer Group B", species="Dairy Cattle", product="meat", headcount=28)
    g3 = Group(farm_id=f2.id, name="Broiler Shed 3", species="Broiler Chicken", product="meat", headcount=4200)
    g4 = Group(farm_id=f3.id, name="Feedlot Pen 7", species="Beef Cattle", product="meat", headcount=90)
    db.session.add_all([g1, g2, g3, g4])
    db.session.commit()

    by_name = {d.name: d for d in Drug.query.all()}
    today = date.today()

    def d(n):
        return today - timedelta(days=n)

    treatments = [
        Treatment(farm_id=f1.id, group_id=g1.id, drug_id=by_name["Amoxicillin"].id, dose="10", unit="mL/100kg",
                  start_date=d(20), end_date=d(17), reason="Mastitis", prescribed_by="Dr. Kavya R.",
                  logged_by="farmer_greenfield"),
        Treatment(farm_id=f1.id, group_id=g2.id, drug_id=by_name["Enrofloxacin"].id, dose="7.5", unit="mg/kg",
                  start_date=d(4), end_date=d(2), reason="Respiratory infection", prescribed_by="Dr. Kavya R.",
                  logged_by="vet_kavya"),
        Treatment(farm_id=f2.id, group_id=g3.id, drug_id=by_name["Tylosin"].id, dose="0.5", unit="g/L water",
                  start_date=d(45), end_date=d(42), reason="Flock respiratory outbreak",
                  prescribed_by="Dr. Arjun M.", marketed_date=d(30), logged_by="vet_arjun"),
        Treatment(farm_id=f3.id, group_id=g4.id, drug_id=by_name["Ceftiofur"].id, dose="1", unit="mL/45kg",
                  start_date=d(2), end_date=d(1), reason="Foot rot", prescribed_by="Dr. Arjun M.",
                  logged_by="vet_arjun"),
        Treatment(farm_id=f3.id, group_id=g4.id, drug_id=by_name["Oxytetracycline"].id, dose="20", unit="mg/kg",
                  start_date=d(90), end_date=d(87), reason="Bovine respiratory disease",
                  prescribed_by="Dr. Arjun M.", marketed_date=d(50), logged_by="farmer_sunridge"),
        Treatment(farm_id=f2.id, group_id=g3.id, drug_id=by_name["Enrofloxacin"].id, dose="10", unit="mg/kg",
                  start_date=d(70), end_date=d(67), reason="E. coli outbreak", prescribed_by="Dr. Arjun M.",
                  logged_by="vet_arjun"),
    ]
    db.session.add_all(treatments)
    db.session.commit()

    seed_users(f1, f2, f3)


def seed_users(f1, f2, f3):
    if User.query.first():
        return

    def make(username, password, display_name, role, farm=None):
        u = User(username=username, display_name=display_name, role=role,
                  farm_id=farm.id if farm else None)
        u.set_password(password)
        return u

    users = [
        make("farmer_greenfield", "farmer123", "Priya (Greenfield Dairy)", "farmer", f1),
        make("farmer_hillcrest", "farmer123", "Suresh (Hillcrest Poultry)", "farmer", f2),
        make("farmer_sunridge", "farmer123", "Muthu (Sunridge Beef Co.)", "farmer", f3),
        make("vet_kavya", "vet123", "Dr. Kavya R.", "vet"),
        make("vet_arjun", "vet123", "Dr. Arjun M.", "vet"),
        make("regulator1", "regulator123", "T. Meena (State Regulator)", "regulator"),
    ]
    db.session.add_all(users)
    db.session.commit()


def resistance_score(farm_id):
    """Weighted resistance-risk indicator for a farm over the last 90 days."""
    cutoff = date.today() - timedelta(days=90)
    recent = Treatment.query.filter(Treatment.farm_id == farm_id, Treatment.start_date >= cutoff).all()

    weighted = 0
    class_counts = {}
    for t in recent:
        weighted += 3 if t.drug.cia else 1
        class_counts[t.drug.drug_class] = class_counts.get(t.drug.drug_class, 0) + 1

    repeat_penalty = sum(1 for c in class_counts.values() if c >= 3) * 4
    score = min(100, weighted * 6 + repeat_penalty)
    tier = "High" if score >= 65 else "Moderate" if score >= 30 else "Low"
    return {"score": score, "tier": tier, "count": len(recent)}
