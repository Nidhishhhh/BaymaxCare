import os
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import engine, get_db, DATABASE_URL
import models
from seed_db import init_and_seed

# Initialize logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("baymaxcare.api")

# Ensure tables and seed exist on boot
init_and_seed()

app = FastAPI(
    title="BaymaxCare Telehealth & In-Home Companion API",
    description="Backend API powering Doctor & Guardian dashboards with PostgreSQL on Aiven.",
    version="1.0.0"
)

# Enable CORS for local file:// access and local dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------
# WebSocket Connection Manager for Live Telemetry & Alerts
# -------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                self.disconnect(connection)

ws_manager = ConnectionManager()

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive()
            # 1. Handle raw binary audio chunks (bidirectional real-time voice streaming)
            if "bytes" in message and message["bytes"]:
                binary_data = message["bytes"]
                for conn in list(ws_manager.active_connections):
                    if conn != websocket:
                        try:
                            await conn.send_bytes(binary_data)
                        except Exception:
                            pass
            # 2. Handle text signaling (calls, alerts, ping/pong)
            elif "text" in message and message["text"]:
                data = message["text"]
                try:
                    payload = json.loads(data)
                    msg_type = payload.get("type")
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong", "time": datetime.utcnow().isoformat()})
                    elif msg_type in ("call_invite", "call_accept", "call_end", "call_busy", "webrtc_offer", "webrtc_answer", "webrtc_ice"):
                        for conn in list(ws_manager.active_connections):
                            if conn != websocket:
                                try:
                                    await conn.send_text(data)
                                except Exception:
                                    pass
                except Exception:
                    pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# -------------------------------------------------------------
# Pydantic Schemas for Request Validation
# -------------------------------------------------------------
class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    age: Optional[int] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    room_number: Optional[str] = None
    code_status: Optional[str] = None
    guardian_name: Optional[str] = None
    guardian_username: Optional[str] = None
    guardian_password: Optional[str] = None
    allergies: Optional[List[str]] = None
    chronic_conditions: Optional[List[str]] = None

class CreatePatientProfile(BaseModel):
    full_name: str
    guardian_name: str
    guardian_username: str
    guardian_password: str
    age: Optional[int] = 75
    dob: Optional[str] = "1951-01-01"
    gender: Optional[str] = "Unspecified"
    blood_group: Optional[str] = "O+"
    room_number: Optional[str] = "Room 101"
    code_status: Optional[str] = "Full Code"

class GuardianLoginRequest(BaseModel):
    username: str
    password: str

class DoctorLoginRequest(BaseModel):
    email: str
    password: str
    doctor_name: Optional[str] = None
    hospital: Optional[str] = None
    license: Optional[str] = None
    license: Optional[str] = None

class MedCreate(BaseModel):
    name: str
    dosage: str
    time_str: str
    instructions: Optional[str] = ""
    status: Optional[str] = "upcoming"

class MedStatusUpdate(BaseModel):
    status: str  # 'taken', 'overdue', 'upcoming', 'missed'

class VitalCreate(BaseModel):
    heart_rate: int
    spo2: int
    temperature: float
    respiration_rate: int
    systolic_bp: Optional[int] = 120
    diastolic_bp: Optional[int] = 80
    device_source: Optional[str] = "Baymax Sensor Hub"

class IncidentCreate(BaseModel):
    incident_type: str  # 'fall', 'sos', 'dizziness', 'vitals_spike', 'offline'
    severity: str = "critical"
    description: str
    location: Optional[str] = "Living Room"

class AppointmentUpdate(BaseModel):
    scheduled_date: str  # 'YYYY-MM-DD'
    scheduled_time: str  # 'HH:MM'
    doctor_name: Optional[str] = None
    specialty: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None

class AutoFillPayload(BaseModel):
    patient_code: Optional[str] = "P-1042"
    patient: Dict[str, Any]
    medications: Optional[List[Dict[str, Any]]] = None
    guardians: Optional[Dict[str, Any]] = None

# -------------------------------------------------------------
# REST Endpoints
# -------------------------------------------------------------

@app.get("/api/health")
def health_check():
    db_target = "PostgreSQL (Aiven)" if "postgres" in DATABASE_URL else "SQLite (Local Fallback)"
    return {
        "status": "online",
        "database": db_target,
        "ws_clients": len(ws_manager.active_connections),
        "timestamp": datetime.utcnow().isoformat()
    }

# --- AUTH & PROFILES ---

@app.post("/api/login/guardian")
def login_guardian(payload: GuardianLoginRequest, db: Session = Depends(get_db)):
    """Logs in guardian via username and password generated by doctor."""
    u = payload.username.strip()
    p = payload.password.strip()
    patient = db.query(models.Patient).filter(
        models.Patient.guardian_username == u,
        models.Patient.guardian_password == p
    ).first()

    if not patient:
        raise HTTPException(status_code=401, detail="Invalid guardian username or password")

    return {
        "success": True,
        "role": "guardian",
        "patient_code": patient.patient_code,
        "patient_name": patient.full_name,
        "guardian_name": patient.guardian_name or "Guardian",
        "redirect": f"dashboard-guardian.html?code={patient.patient_code}"
    }

@app.post("/api/login/doctor")
def login_doctor(payload: DoctorLoginRequest, db: Session = Depends(get_db)):
    """Logs in doctor and returns session metadata."""
    email = payload.email.strip()
    # In clinical demo mode, any valid non-empty email/password logs in successfully
    doctor = db.query(models.User).filter_by(role="doctor").first()
    doc_name = payload.doctor_name or (doctor.full_name if doctor else "Dr. Sarah Jenkins, MD")
    hosp = payload.hospital or (doctor.hospital_affiliation if doctor else "St. Jude Senior Care Center")
    lic = payload.license or (doctor.medical_license if doctor else "MD-98421-CA")
    spec = doctor.specialty if doctor else "Geriatric Medicine"
    return {
        "success": True,
        "role": "doctor",
        "email": email,
        "doctor_name": doc_name,
        "hospital": hosp,
        "license": lic,
        "specialty": spec
    }

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()

@app.get("/api/livekit/token")
def get_livekit_token(room: str = "baymaxcare-room-P-1042", identity: str = "user", name: str = "User"):
    """Returns a signed LiveKit token if configured, or signals WebRTC P2P mode."""
    if LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET:
        try:
            from livekit.api import AccessToken, VideoGrants
            token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
                .with_identity(identity) \
                .with_name(name) \
                .with_grants(VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True
                )).to_jwt()
            return {
                "enabled": True,
                "url": LIVEKIT_URL,
                "token": token,
                "room": room,
                "identity": identity
            }
        except Exception as e:
            logger.warning(f"Error generating LiveKit token: {e}")

    return {
        "enabled": False,
        "mode": "webrtc_p2p",
        "room": room,
        "identity": identity,
        "ws_url": "/ws/stream"
    }

@app.get("/api/patients")
def list_patient_profiles(db: Session = Depends(get_db)):
    """Returns all patient profiles with guardian credentials for doctor selection."""
    patients = db.query(models.Patient).order_by(models.Patient.created_at.desc()).all()
    out = []
    for p in patients:
        out.append({
            "id": p.id,
            "patient_code": p.patient_code,
            "full_name": p.full_name,
            "age": p.age,
            "dob": p.dob,
            "gender": p.gender,
            "room_number": p.room_number,
            "code_status": p.code_status,
            "guardian_name": p.guardian_name or "Assigned Guardian",
            "guardian_username": p.guardian_username or f"{p.full_name.lower().replace(' ', '_')}_care",
            "guardian_password": p.guardian_password or "Care#2026",
            "chronic_conditions": p.chronic_conditions or [],
            "allergies": p.allergies or []
        })
    return out

@app.post("/api/patients")
async def create_patient_profile(payload: CreatePatientProfile, db: Session = Depends(get_db)):
    """Creates a new patient profile with Patient Name, Guardian Name, and auto-generated credentials."""
    # Find next patient code
    count = db.query(models.Patient).count()
    patient_code = f"P-{1042 + count}"
    while db.query(models.Patient).filter_by(patient_code=patient_code).first():
        count += 1
        patient_code = f"P-{1042 + count}"

    # Get primary doctor
    doctor = db.query(models.User).filter_by(role="doctor").first()

    patient = models.Patient(
        patient_code=patient_code,
        full_name=payload.full_name.strip(),
        age=payload.age or 75,
        dob=payload.dob or "1951-01-01",
        gender=payload.gender or "Unspecified",
        blood_group=payload.blood_group or "O+",
        room_number=payload.room_number or "Room 101",
        code_status=payload.code_status or "Full Code",
        guardian_name=payload.guardian_name.strip(),
        guardian_username=payload.guardian_username.strip(),
        guardian_password=payload.guardian_password.strip(),
        allergies=[],
        chronic_conditions=[],
        primary_doctor_id=doctor.id if doctor else None
    )
    db.add(patient)
    db.flush()

    # Create associated primary guardian record
    guardian = models.Guardian(
        patient_id=patient.id,
        name=payload.guardian_name.strip(),
        relationship_label="Primary Guardian",
        category="caregiver",
        status_badge="Active",
        phone="+1 (555) 000-0000",
        is_primary=True,
        escalation_priority=1
    )
    db.add(guardian)

    # Seed initial baseline vitals
    db.add(models.Vital(
        patient_id=patient.id,
        heart_rate=72,
        spo2=98,
        temperature=98.6,
        respiration_rate=16,
        systolic_bp=120,
        diastolic_bp=80,
        device_source="Baymax Radar Hub"
    ))

    db.commit()
    db.refresh(patient)

    await ws_manager.broadcast({
        "type": "new_patient_created",
        "patient_code": patient.patient_code,
        "full_name": patient.full_name
    })

    return {
        "message": "Patient profile created successfully",
        "patient_code": patient.patient_code,
        "full_name": patient.full_name,
        "guardian_username": patient.guardian_username,
        "guardian_password": patient.guardian_password
    }

# 1. Patient Info
@app.get("/api/patient")
def get_patient(code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "id": patient.id,
        "patient_code": patient.patient_code,
        "full_name": patient.full_name,
        "age": patient.age,
        "dob": patient.dob,
        "gender": patient.gender,
        "blood_group": patient.blood_group,
        "room_number": patient.room_number,
        "code_status": patient.code_status,
        "guardian_name": patient.guardian_name or "Sunita Nair",
        "guardian_username": patient.guardian_username or "margaret_iyer_care",
        "guardian_password": patient.guardian_password or "Care#Iyer2026",
        "allergies": patient.allergies or [],
        "chronic_conditions": patient.chronic_conditions or [],
    }

@app.put("/api/patient")
async def update_patient(payload: PatientUpdate, code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    for field, val in payload.dict(exclude_unset=True).items():
        setattr(patient, field, val)

    db.commit()
    db.refresh(patient)

    # Broadcast update to doctor & guardian dashboards
    await ws_manager.broadcast({
        "type": "patient_updated",
        "patient_code": code,
        "full_name": patient.full_name,
        "age": patient.age
    })

    return {"message": "Patient updated successfully", "patient": patient.full_name}

# 2. Medications
@app.get("/api/meds")
def get_meds(code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    meds = db.query(models.Medication).filter_by(patient_id=patient.id).all()
    taken_count = sum(1 for m in meds if m.status == "taken")

    return {
        "total": len(meds),
        "taken": taken_count,
        "summary": f"{taken_count} of {len(meds)} taken",
        "items": [
            {
                "id": m.id,
                "name": m.name,
                "dosage": m.dosage,
                "time_str": m.time_str,
                "instructions": m.instructions,
                "status": m.status,
                "taken_at": m.taken_at.isoformat() if m.taken_at else None
            }
            for m in meds
        ]
    }

@app.post("/api/meds")
async def add_med(payload: MedCreate, code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    med = models.Medication(
        patient_id=patient.id,
        name=payload.name,
        dosage=payload.dosage,
        time_str=payload.time_str,
        instructions=payload.instructions,
        status=payload.status
    )
    db.add(med)
    db.commit()
    db.refresh(med)

    await ws_manager.broadcast({"type": "meds_updated", "patient_code": code})
    return {"message": "Medication added", "id": med.id}

@app.patch("/api/meds/{med_id}")
async def update_med_status(med_id: str, payload: MedStatusUpdate, db: Session = Depends(get_db)):
    med = db.query(models.Medication).filter_by(id=med_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found")

    med.status = payload.status
    if payload.status == "taken":
        med.taken_at = datetime.utcnow()
    else:
        med.taken_at = None

    db.commit()
    await ws_manager.broadcast({
        "type": "med_status_changed",
        "med_id": med_id,
        "med_name": med.name,
        "status": med.status
    })
    return {"message": "Medication status updated", "status": med.status}

# 3. Guardians & Contacts
@app.get("/api/guardians")
def get_guardians(code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    contacts = db.query(models.Guardian).filter_by(patient_id=patient.id).order_by(models.Guardian.escalation_priority).all()
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "relationship": c.relationship_label,
                "category": c.category,
                "status_badge": c.status_badge,
                "phone": c.phone,
                "email": c.email,
                "address": c.address,
                "coverage_hours": c.coverage_hours,
                "is_primary": c.is_primary,
                "escalation_priority": c.escalation_priority
            }
            for c in contacts
        ]
    }

# 4. Telemetry & Vitals
@app.get("/api/vitals/latest")
def get_latest_vitals(code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    latest = db.query(models.Vital).filter_by(patient_id=patient.id).order_by(desc(models.Vital.recorded_at)).first()
    if not latest:
        return {"heart_rate": 72, "spo2": 98, "temperature": 98.4, "respiration_rate": 16}

    return {
        "heart_rate": latest.heart_rate,
        "spo2": latest.spo2,
        "temperature": latest.temperature,
        "respiration_rate": latest.respiration_rate,
        "systolic_bp": latest.systolic_bp,
        "diastolic_bp": latest.diastolic_bp,
        "recorded_at": latest.recorded_at.isoformat()
    }

@app.get("/api/vitals/history")
def get_vitals_history(code: str = "P-1042", limit: int = 24, db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    records = db.query(models.Vital).filter_by(patient_id=patient.id).order_by(desc(models.Vital.recorded_at)).limit(limit).all()
    records.reverse()  # Chronological order

    return [
        {
            "time": r.recorded_at.strftime("%H:%M"),
            "heart_rate": r.heart_rate,
            "spo2": r.spo2,
            "temperature": r.temperature,
            "respiration_rate": r.respiration_rate
        }
        for r in records
    ]

@app.post("/api/vitals")
async def record_vital(payload: VitalCreate, code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    vital = models.Vital(
        patient_id=patient.id,
        heart_rate=payload.heart_rate,
        spo2=payload.spo2,
        temperature=payload.temperature,
        respiration_rate=payload.respiration_rate,
        systolic_bp=payload.systolic_bp,
        diastolic_bp=payload.diastolic_bp,
        device_source=payload.device_source
    )
    db.add(vital)
    db.commit()

    # Broadcast live reading
    await ws_manager.broadcast({
        "type": "vital_update",
        "data": {
            "heart_rate": vital.heart_rate,
            "spo2": vital.spo2,
            "temperature": vital.temperature,
            "respiration_rate": vital.respiration_rate
        }
    })
    return {"message": "Vital recorded", "id": vital.id}

# 5. Incidents & Alerts
@app.get("/api/incidents")
def get_incidents(code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    incidents = db.query(models.Incident).filter_by(patient_id=patient.id).order_by(desc(models.Incident.detected_at)).limit(10).all()
    return [
        {
            "id": inc.id,
            "type": inc.incident_type,
            "severity": inc.severity,
            "description": inc.description,
            "location": inc.location,
            "status": inc.status,
            "time": inc.detected_at.strftime("%I:%M %p")
        }
        for inc in incidents
    ]

@app.post("/api/incidents")
async def trigger_incident(payload: IncidentCreate, code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    inc = models.Incident(
        patient_id=patient.id,
        incident_type=payload.incident_type,
        severity=payload.severity,
        description=payload.description,
        location=payload.location,
        status="active"
    )
    db.add(inc)
    db.commit()
    db.refresh(inc)

    # Immediately broadcast critical alert to all dashboard screens
    await ws_manager.broadcast({
        "type": "incident_alert",
        "incident": {
            "id": inc.id,
            "incident_type": inc.incident_type,
            "severity": inc.severity,
            "description": inc.description,
            "location": inc.location,
            "time": inc.detected_at.strftime("%I:%M %p")
        }
    })
    return {"message": "Incident triggered", "id": inc.id}

# 6. Appointments
@app.get("/api/appointment")
def get_appointment(code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    appt = db.query(models.Appointment).filter_by(patient_id=patient.id).order_by(desc(models.Appointment.created_at)).first()
    if not appt:
        return {}

    return {
        "id": appt.id,
        "doctor_name": appt.doctor_name,
        "specialty": appt.specialty,
        "scheduled_date": appt.scheduled_date,
        "scheduled_time": appt.scheduled_time,
        "appointment_type": appt.appointment_type,
        "location": appt.location,
        "notes": appt.notes
    }

@app.put("/api/appointment")
async def update_appointment(payload: AppointmentUpdate, code: str = "P-1042", db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter_by(patient_code=code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    appt = db.query(models.Appointment).filter_by(patient_id=patient.id).order_by(desc(models.Appointment.created_at)).first()
    if not appt:
        appt = models.Appointment(patient_id=patient.id)
        db.add(appt)

    appt.scheduled_date = payload.scheduled_date
    appt.scheduled_time = payload.scheduled_time
    if payload.doctor_name: appt.doctor_name = payload.doctor_name
    if payload.specialty: appt.specialty = payload.specialty
    if payload.location: appt.location = payload.location
    if payload.notes: appt.notes = payload.notes

    db.commit()
    await ws_manager.broadcast({
        "type": "appointment_updated",
        "date": appt.scheduled_date,
        "time": appt.scheduled_time
    })
    return {"message": "Appointment updated", "scheduled_date": appt.scheduled_date, "scheduled_time": appt.scheduled_time}

# 7. Auto-Fill Intake (Upload Information)
@app.post("/api/upload-intake")
async def auto_fill_intake(payload: AutoFillPayload, db: Session = Depends(get_db)):
    """Receives parsed intake documents and updates patient, meds, and guardians in DB."""
    patient = db.query(models.Patient).filter_by(patient_code=payload.patient_code).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Update patient fields
    p_data = payload.patient
    if "full_name" in p_data: patient.full_name = p_data["full_name"]
    if "age" in p_data: patient.age = int(p_data["age"])
    if "dob" in p_data: patient.dob = p_data["dob"]
    if "allergies" in p_data: patient.allergies = p_data["allergies"]
    if "chronic_conditions" in p_data: patient.chronic_conditions = p_data["chronic_conditions"]

    # Replace medications if provided
    if payload.medications:
        db.query(models.Medication).filter_by(patient_id=patient.id).delete()
        for m in payload.medications:
            db.add(models.Medication(
                patient_id=patient.id,
                name=m.get("name", "Unknown"),
                dosage=m.get("dosage", ""),
                time_str=m.get("time_str", "8:00 AM"),
                instructions=m.get("instructions", ""),
                status=m.get("status", "upcoming")
            ))

    db.commit()

    # Broadcast intake sync to all screens
    await ws_manager.broadcast({
        "type": "intake_autofilled",
        "patient_name": patient.full_name,
        "patient_code": patient.patient_code
    })

    return {"message": "Records successfully auto-filled in database", "patient": patient.full_name}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
def serve_index():
    """Serves login.html by default."""
    return FileResponse(os.path.join(BASE_DIR, "login.html"))

@app.get("/{filename}.html")
def serve_html(filename: str):
    """Serves any dashboard or html file in project root."""
    file_path = os.path.join(BASE_DIR, f"{filename}.html")
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Page not found")

if __name__ == "__main__":
    import uvicorn
    # Run accessible to phone and local network on port 8000
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

