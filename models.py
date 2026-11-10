import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Date, Time,
    Text, ForeignKey, JSON
)
from sqlalchemy.orm import relationship
from database import Base

def gen_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=True)
    full_name = Column(String(150), nullable=False)
    role = Column(String(30), nullable=False, default="doctor")  # 'doctor', 'guardian', 'admin'
    phone = Column(String(50), nullable=True)
    hospital_affiliation = Column(String(200), nullable=True)
    medical_license = Column(String(100), nullable=True)
    specialty = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patients = relationship("Patient", back_populates="primary_doctor")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    patient_code = Column(String(50), unique=True, nullable=False, index=True)  # e.g. 'P-1042'
    full_name = Column(String(150), nullable=False)
    age = Column(Integer, default=78)
    dob = Column(String(20), default="1946-04-12")
    gender = Column(String(20), default="Female")
    blood_group = Column(String(10), default="O+")
    room_number = Column(String(50), default="Apt 4B - Master Bedroom")
    code_status = Column(String(50), default="Full Code (DNR Pending Family Review)")
    allergies = Column(JSON, default=lambda: ["Penicillin", "Sulfa drugs", "Latex"])
    chronic_conditions = Column(JSON, default=lambda: [
        "Hypertension (Stage 2)",
        "Atrial Fibrillation (Paroxysmal)",
        "Type 2 Diabetes Mellitus",
        "Osteoarthritis (Bilateral Knees)"
    ])
    guardian_name = Column(String(150), nullable=True)
    guardian_username = Column(String(100), unique=True, index=True, nullable=True)
    guardian_password = Column(String(100), nullable=True)
    primary_doctor_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    primary_doctor = relationship("User", back_populates="patients")
    guardians = relationship("Guardian", back_populates="patient", cascade="all, delete-orphan")
    medications = relationship("Medication", back_populates="patient", cascade="all, delete-orphan")
    vitals = relationship("Vital", back_populates="patient", cascade="all, delete-orphan")
    incidents = relationship("Incident", back_populates="patient", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="patient", cascade="all, delete-orphan")


class Guardian(Base):
    __tablename__ = "guardians"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    patient_id = Column(String(36), ForeignKey("patients.id"), nullable=False)
    name = Column(String(150), nullable=False)
    relationship_label = Column(String(150), nullable=False)  # 'Daughter · Legal Medical Decision Maker'
    category = Column(String(50), default="caregiver")  # 'caregiver' or 'proxy'
    status_badge = Column(String(50), default="Active On-Site")
    phone = Column(String(50), nullable=False)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    coverage_hours = Column(String(150), nullable=True)
    is_primary = Column(Boolean, default=False)
    escalation_priority = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="guardians")


class Medication(Base):
    __tablename__ = "medications"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    patient_id = Column(String(36), ForeignKey("patients.id"), nullable=False)
    name = Column(String(150), nullable=False)
    dosage = Column(String(100), nullable=False)  # '5mg'
    time_str = Column(String(50), nullable=False)  # '8:00 AM'
    instructions = Column(String(255), nullable=True)  # 'Take with water before breakfast'
    status = Column(String(30), default="upcoming")  # 'taken', 'overdue', 'upcoming'
    taken_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="medications")


class Vital(Base):
    __tablename__ = "vitals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(36), ForeignKey("patients.id"), nullable=False, index=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)
    heart_rate = Column(Integer, nullable=True)  # bpm (e.g. 74)
    spo2 = Column(Integer, nullable=True)  # % (e.g. 98)
    temperature = Column(Float, nullable=True)  # °F (e.g. 98.4)
    respiration_rate = Column(Integer, nullable=True)  # rpm (e.g. 16)
    systolic_bp = Column(Integer, nullable=True)  # e.g. 128
    diastolic_bp = Column(Integer, nullable=True)  # e.g. 82
    device_source = Column(String(50), default="Baymax Radar Hub")

    patient = relationship("Patient", back_populates="vitals")


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    patient_id = Column(String(36), ForeignKey("patients.id"), nullable=False, index=True)
    incident_type = Column(String(50), nullable=False)  # 'fall', 'sos', 'dizziness', 'vitals_spike', 'offline'
    severity = Column(String(20), default="critical")  # 'low', 'medium', 'high', 'critical'
    description = Column(Text, nullable=False)
    location = Column(String(100), default="Living Room")
    status = Column(String(30), default="active")  # 'active', 'acknowledged', 'resolved'
    detected_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(150), nullable=True)

    patient = relationship("Patient", back_populates="incidents")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    patient_id = Column(String(36), ForeignKey("patients.id"), nullable=False)
    doctor_name = Column(String(150), default="Dr. Sarah Jenkins, MD")
    specialty = Column(String(100), default="Geriatric Medicine")
    scheduled_date = Column(String(50), default="2026-11-18")  # 'YYYY-MM-DD'
    scheduled_time = Column(String(50), default="10:30")  # 'HH:MM'
    appointment_type = Column(String(100), default="Geriatric Comprehensive Follow-up")
    location = Column(String(150), default="St. Jude Senior Care Center · Suite 402")
    notes = Column(Text, nullable=True)
    status = Column(String(30), default="scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="appointments")

