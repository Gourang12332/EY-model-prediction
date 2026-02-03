from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient
from datetime import datetime
from google import genai
from uuid import uuid4
import json
import uvicorn
import os

# ================= CONFIG =================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # MUST set in env
MONGO_URI = os.getenv("MONGO_URI")           # MUST set in env
DB_NAME = "techathon_db"

MODEL_NAME = "gemini-3-flash-preview"

# ================= CLIENTS =================

try:
    client = genai.Client(api_key=GEMINI_API_KEY)
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[DB_NAME]
    cars_db = db["vehicles"]
    logs_db = db["logs"]
    print(f"Connected to DB: {db.name}")
except Exception as e:
    print(f"Failed to initialize clients: {e}")
    raise e

# ================= FASTAPI SETUP =================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= DATA MODELS =================

class VehicleRequest(BaseModel):
    userId: str
    vehicleId: str
    sensors: dict

# ================= LLM PROMPT =================

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

# ================= LLM CALL =================

def call_llm(sensors: dict):
    prompt = SYSTEM_PROMPT + "\n\nSensor Data:\n" + json.dumps(sensors)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[prompt]   # 🔥 MUST be list
        )

        text = response.text.strip()

        # Remove markdown if exists
        if text.startswith("```"):
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()

        return json.loads(text)

    except Exception as e:
        print("===== GEMINI ERROR =====")
        print(e)
        print("Raw Response:", getattr(response, "text", None))
        raise e   # don't hide errors

# ================= CORE LOGIC =================

def process_vehicle_analysis(userId, vehicleId, sensors):
    car_doc = cars_db.find_one({
        "user_id": userId,
        "vehicle_id": vehicleId
    })

    if not car_doc:
        raise ValueError("Car not found")

    llm_output = call_llm(sensors)

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

    for pred in llm_output["predictions"]:
        log = {
            "logId": str(uuid4()),
            "user_id": userId,
            "vehicle_id": vehicleId,
            "timestamp": datetime.utcnow(),
            "logType": "ISSUE",
            "data": {
                "component": pred["component"],
                "issue": pred["issue"],
                "severity": pred["severity"],
                "prediction": pred["prediction"],
                "recommendation": pred["recommendation"],
                "modelVersion": MODEL_NAME
            }
        }
        logs_db.insert_one(log)

    updated_car_doc["_id"] = str(car_doc["_id"])
    return updated_car_doc

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

# ================= RUN =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
