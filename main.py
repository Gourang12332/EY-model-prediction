from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pymongo import MongoClient
from datetime import datetime
from google import genai
from uuid import uuid4
import json
import uvicorn
import os

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "techathon_db"
MODEL_NAME = "gemini-3-flash-preview"

client = genai.Client(api_key=GEMINI_API_KEY)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]
logs_db = db["logs"]

# ================= FASTAPI =================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= MODELS =================
class VehicleRequest(BaseModel):
    userId: str
    vehicleId: str
    sensors: dict

# ================= PROMPTS =================
SYSTEM_PROMPT = """
You are an AI vehicle diagnostic system.

Given sensor readings of a vehicle, your task is to:
1. Identify faulty components.
2. Classify issues.
3. Estimate remaining days.
4. Assign severity: LOW, MEDIUM, HIGH.
5. Decide if service is needed.

You must respond ONLY in valid JSON in this exact schema:

{
  "status": "",
  "isServiceNeeded": boolean,
  "recommendedAction": "",
  "predictions": [
    {
      "component": "",
      "issue": "",
      "severity": "",
      "prediction": {
        "days_left": number,
        "certainty": number
      },
      "recommendation": ""
    }
  ],
  "summary": ""
}

No explanations. No extra text.
"""

CAPA_PROMPT = """
You are an automotive quality management AI.

Generate CAPA JSON:

{
  "company": "",
  "root_causes": [],
  "corrective_actions": [],
  "preventive_actions": [],
  "risk_assessment": "",
  "summary": ""
}
"""

# ================= LLM =================
def call_llm(prompt):
    res = client.models.generate_content(
        model=MODEL_NAME,
        contents=[prompt]
    )
    text = res.text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].replace("json", "").strip()
    return json.loads(text)

# ================= CORE =================
def process_vehicle_analysis(userId, vehicleId, sensors):
    car_doc = cars_db.find_one({
        "user_id": userId,
        "vehicle_id": vehicleId
    })

    if not car_doc:
        raise ValueError("Car not found")

    llm_output = call_llm(SYSTEM_PROMPT + json.dumps(sensors))

    # DO NOT TOUCH THIS BLOCK (as per your requirement)
    updated_car_doc = {
        "user_id": car_doc["user_id"],
        "vehicle_id": car_doc["vehicle_id"],
        "owner": car_doc.get("owner", "Unknown"),
        "model": car_doc.get("model", "Unknown"),
        "status": llm_output.get("status"),
        "isServiceNeeded": llm_output.get("isServiceNeeded"),
        "recommendedAction": llm_output.get("recommendedAction"),
        "sensors": sensors,
        "predictions": llm_output.get("predictions"),
        "summary": llm_output.get("summary")
    }

    cars_db.update_one(
        {"_id": car_doc["_id"]},
        {"$set": updated_car_doc}
    )

    # Logs use DIFFERENT schema (event system)
    for pred in llm_output["predictions"]:
        logs_db.insert_one({
            "logId": str(uuid4()),
            "userId": userId,
            "vehicleId": vehicleId,
            "timestamp": datetime.utcnow(),
            "logType": "ISSUE",
            "data": pred
        })

    updated_car_doc["_id"] = str(car_doc["_id"])
    return updated_car_doc

def get_company_from_vehicle(vehicle_id: str):
    return vehicle_id.split("_")[0]

def generate_capa_with_llm(logs: list, company: str):
    prompt = CAPA_PROMPT + "\nCompany: " + company + "\nLogs:\n" + json.dumps(logs)
    return call_llm(prompt)

# ================= PDF =================
def create_capa_pdf_from_llm(capa_data: dict):
    os.makedirs("/mnt/data", exist_ok=True)
    filename = f"/mnt/data/CAPA_{capa_data['company']}.pdf"

    c = canvas.Canvas(filename, pagesize=A4)
    x, y = 50, 800

    def draw(title, items):
        nonlocal y
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x, y, title)
        y -= 20
        c.setFont("Helvetica", 10)
        for i in items:
            c.drawString(x + 10, y, f"- {i}")
            y -= 15

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, "CAPA REPORT")
    y -= 30
    c.drawString(x, y, f"Company: {capa_data['company']}")
    y -= 40

    draw("Root Causes", capa_data["root_causes"])
    draw("Corrective Actions", capa_data["corrective_actions"])
    draw("Preventive Actions", capa_data["preventive_actions"])

    c.drawString(x, y, "Risk Assessment:")
    y -= 20
    c.drawString(x + 10, y, capa_data["risk_assessment"])

    y -= 40
    c.drawString(x, y, "Summary:")
    y -= 20
    c.drawString(x + 10, y, capa_data["summary"])

    c.save()
    return filename

# ================= API =================
@app.get("/")
def health_check():
    return {"status": "running", "db": DB_NAME}

@app.post("/analyze")
def analyze_vehicle_endpoint(payload: VehicleRequest):
    try:
        return process_vehicle_analysis(
            payload.userId,
            payload.vehicleId,
            payload.sensors
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print("SERVER ERROR:", e)
        raise HTTPException(status_code=500, detail="LLM Failure")

@app.get("/capa/{vehicle_id}")
def generate_company_capa_from_vehicle(vehicle_id: str):
    company = get_company_from_vehicle(vehicle_id)
    regex = f"{company}_"
    print(regex)

    # IMPORTANT: logs schema uses vehicleId
    issue_logs = list(logs_db.find({
        "vehicle_id": {"$regex": regex},
        "logType": "ISSUE"
    }))

    if not issue_logs:
        raise HTTPException(404, "No ISSUE logs found for this company")

    # Clean logs before sending to LLM
    clean_logs = [
        {
            "component": log["data"]["component"],
            "issue": log["data"]["issue"],
            "severity": log["data"]["severity"],
            "prediction": log["data"]["prediction"],
            "recommendation": log["data"]["recommendation"]
        }
        for log in issue_logs
    ]

    capa_json = generate_capa_with_llm(clean_logs, company)
    pdf_path = create_capa_pdf_from_llm(capa_json)

    return FileResponse(
        pdf_path,
        filename=f"{company}_GLOBAL_CAPA.pdf",
        media_type="application/pdf"
    )

# ================= RUN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
