import logging
from datetime import datetime, timedelta
from sqlalchemy import text
from database import SessionLocal, engine, Base
import models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("baymaxcare.seed")

def run_migrations():
    """Ensure any new columns exist in Postgres / SQLite."""
    try:
        with engine.connect() as conn:
            if "sqlite" in str(engine.url):
                pass
            else:
                conn.execute(text('ALTER TABLE patients ADD COLUMN IF NOT EXISTS guardian_name VARCHAR(150);'))
                conn.execute(text('ALTER TABLE patients ADD COLUMN IF NOT EXISTS guardian_username VARCHAR(100);'))
                conn.execute(text('ALTER TABLE patients ADD COLUMN IF NOT EXISTS guardian_password VARCHAR(100);'))
                conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_guardian_user ON patients(guardian_username);'))
                conn.commit()
    except Exception as e:
        logger.warning(f"Migration note: {e}")

def init_and_seed():
    """Initializes tables and seeds default data if tables are empty."""
    Base.metadata.create_all(bind=engine)
    run_migrations()

    db = SessionLocal()
    try:
        # Check if default patient exists
        p1 = db.query(models.Patient).filter_by(patient_code="P-1042").first()
        if p1:
            # Update credentials if not yet set
            if not p1.guardian_username:
                p1.guardian_name = "Sunita Nair"
                p1.guardian_username = "margaret_iyer_care"
                p1.guardian_password = "Care#Iyer2026"
                db.commit()
                logger.info("Updated existing P-1042 with guardian credentials.")
        else:
            logger.info("Seeding initial BaymaxCare database records...")

            # 1. Primary Doctor
            doctor = db.query(models.User).filter_by(role="doctor").first()
            if not doctor:
                doctor = models.User(
                    email="sarah.jenkins@baymaxcare.demo",
                    full_name="Dr. Sarah Jenkins, MD",
                    role="doctor",
                    phone="+1 (555) 432-1098",
                    hospital_affiliation="St. Jude Senior Care Center",
                    medical_license="MD-98421-CA",
                    specialty="Geriatric Medicine"
                )
                db.add(doctor)
                db.flush()

            # 2. Patient: Margaret Iyer
            p1 = models.Patient(
                patient_code="P-1042",
                full_name="Margaret Iyer",
                age=78,
                dob="1946-04-12",
                gender="Female",
                blood_group="O+",
                room_number="Apt 4B - Master Bedroom",
                code_status="Full Code (DNR Pending Family Review)",
                guardian_name="Sunita Nair",
                guardian_username="margaret_iyer_care",
                guardian_password="Care#Iyer2026",
                allergies=["Penicillin (severe anaphylaxis)", "Sulfa drugs", "Latex"],
                chronic_conditions=[
                    "Hypertension (Stage 2 - controlled)",
                    "Atrial Fibrillation (Paroxysmal, on anticoagulation)",
                    "Type 2 Diabetes Mellitus (HbA1c: 6.8%)",
                    "Osteoarthritis (Bilateral knees - fall risk)"
                ],
                primary_doctor_id=doctor.id
            )
            db.add(p1)
            db.flush()

            # Guardians
            db.add(models.Guardian(
                patient_id=p1.id,
                name="Sunita Nair",
                relationship_label="Certified Nursing Assistant & Full-time Caregiver",
                category="caregiver",
                status_badge="Active On-Site",
                phone="+1 (555) 234-5678",
                email="sunita.nair@baymaxcare.demo",
                coverage_hours="Daily 8:00 AM - 8:00 PM (equipped with active companion receiver)",
                is_primary=True,
                escalation_priority=1
            ))
            db.add(models.Guardian(
                patient_id=p1.id,
                name="Priya Iyer",
                relationship_label="Daughter · Legal Medical Decision Maker",
                category="proxy",
                status_badge="Family Proxy",
                phone="+1 (555) 876-5432",
                email="priya.iyer@example.com",
                address="742 Evergreen Terrace, Apt 4B, Springfield",
                is_primary=False,
                escalation_priority=2
            ))

            # Meds
            meds_data = [
                {"name": "Amlodipine", "dosage": "5mg", "time_str": "8:00 AM", "instructions": "Take with water before breakfast", "status": "taken"},
                {"name": "Metformin", "dosage": "500mg", "time_str": "8:30 AM", "instructions": "Take with meal to reduce GI upset", "status": "taken"},
                {"name": "Aspirin (Cardio)", "dosage": "81mg", "time_str": "12:00 PM", "instructions": "Post lunch blood thinner dose", "status": "taken"},
                {"name": "Atorvastatin", "dosage": "20mg", "time_str": "2:00 PM", "instructions": "Daily cholesterol regimen", "status": "overdue"},
                {"name": "Lisinopril", "dosage": "10mg", "time_str": "6:00 PM", "instructions": "Evening blood pressure control", "status": "upcoming"},
                {"name": "Melatonin", "dosage": "3mg", "time_str": "9:00 PM", "instructions": "Take 30 min before bedtime", "status": "upcoming"},
            ]
            for m in meds_data:
                db.add(models.Medication(
                    patient_id=p1.id,
                    name=m["name"],
                    dosage=m["dosage"],
                    time_str=m["time_str"],
                    instructions=m["instructions"],
                    status=m["status"]
                ))

            # Vitals
            base_time = datetime.utcnow()
            db.add(models.Vital(
                patient_id=p1.id,
                recorded_at=base_time,
                heart_rate=72, spo2=98, temperature=98.4, respiration_rate=16,
                systolic_bp=124, diastolic_bp=78, device_source="Baymax Radar Hub"
            ))

            # Appointment
            db.add(models.Appointment(
                patient_id=p1.id,
                doctor_name="Dr. Sarah Jenkins, MD",
                specialty="Geriatric Medicine",
                scheduled_date="2026-11-18",
                scheduled_time="10:30",
                appointment_type="Geriatric Comprehensive Follow-up",
                location="St. Jude Senior Care Center · Suite 402",
                notes="Routine check-up, review knee pain & BP control",
                status="scheduled"
            ))

        # Check and seed Patient 2: Robert Chen
        p2 = db.query(models.Patient).filter_by(patient_code="P-1043").first()
        if not p2:
            p2 = models.Patient(
                patient_code="P-1043",
                full_name="Robert Chen",
                age=74,
                dob="1950-08-19",
                gender="Male",
                blood_group="A+",
                room_number="Suite 210 - East Wing",
                code_status="Full Code",
                guardian_name="David Chen",
                guardian_username="robert_chen_care",
                guardian_password="Care#Chen742",
                allergies=["ACE Inhibitors (Angioedema)", "Shellfish"],
                chronic_conditions=[
                    "Heart Failure (HFrEF - NYHA Class II)",
                    "Chronic Kidney Disease (Stage 3a)",
                    "Hyperlipidemia"
                ]
            )
            db.add(p2)
            db.flush()
            db.add(models.Guardian(
                patient_id=p2.id,
                name="David Chen",
                relationship_label="Son · Primary Caregiver",
                category="caregiver",
                status_badge="Active",
                phone="+1 (555) 345-6789",
                email="david.chen@example.com",
                is_primary=True,
                escalation_priority=1
            ))
            db.add_all([
                models.Medication(patient_id=p2.id, name="Carvedilol", dosage="12.5mg", time_str="8:00 AM", status="taken"),
                models.Medication(patient_id=p2.id, name="Furosemide", dosage="40mg", time_str="9:00 AM", status="taken"),
                models.Medication(patient_id=p2.id, name="Entresto", dosage="24/26mg", time_str="8:00 PM", status="upcoming"),
            ])
            db.add(models.Vital(
                patient_id=p2.id,
                recorded_at=datetime.utcnow(),
                heart_rate=68, spo2=97, temperature=98.2, respiration_rate=15,
                systolic_bp=116, diastolic_bp=74, device_source="Baymax Radar Hub"
            ))

        # Check and seed Patient 3: Eleanor Vance
        p3 = db.query(models.Patient).filter_by(patient_code="P-1044").first()
        if not p3:
            p3 = models.Patient(
                patient_code="P-1044",
                full_name="Eleanor Vance",
                age=81,
                dob="1943-02-14",
                gender="Female",
                blood_group="B+",
                room_number="Room 108 - Rehab Unit",
                code_status="DNR/DNI Signed",
                guardian_name="Clara Vance",
                guardian_username="eleanor_vance_care",
                guardian_password="Care#Vance810",
                allergies=["Morphine (Nausea)", "Codeine"],
                chronic_conditions=[
                    "Post-op Total Hip Arthroplasty (Week 3 Rehab)",
                    "Mild Cognitive Impairment",
                    "Osteoporosis"
                ]
            )
            db.add(p3)
            db.flush()
            db.add(models.Guardian(
                patient_id=p3.id,
                name="Clara Vance",
                relationship_label="Daughter & Healthcare Agent",
                category="proxy",
                status_badge="Active",
                phone="+1 (555) 456-7890",
                email="clara.vance@example.com",
                is_primary=True,
                escalation_priority=1
            ))
            db.add_all([
                models.Medication(patient_id=p3.id, name="Enoxaparin", dosage="40mg SubQ", time_str="9:00 AM", status="taken"),
                models.Medication(patient_id=p3.id, name="Acetaminophen", dosage="650mg", time_str="1:00 PM", status="taken"),
                models.Medication(patient_id=p3.id, name="Calcium + Vit D3", dosage="600mg", time_str="7:00 PM", status="upcoming"),
            ])
            db.add(models.Vital(
                patient_id=p3.id,
                recorded_at=datetime.utcnow(),
                heart_rate=78, spo2=99, temperature=98.6, respiration_rate=16,
                systolic_bp=132, diastolic_bp=82, device_source="Baymax Radar Hub"
            ))

        db.commit()
        logger.info("Patient database initialization and seed complete!")
    except Exception as e:
        db.rollback()
        logger.error(f"Seeding failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    init_and_seed()
