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

GEMINI_API_KEY = "AIzaSyCKWdmgEPQHvVT5VDCWEz_te_efBeeN8-Q"
MONGO_URI = "mongodb+srv://jmdayushkumar_db_user:6oe935cfRww7fQZP@cluster0.iii0dcr.mongodb.net/?appName=Cluster0"
DB_NAME = "techathon_db"

MODEL_NAME = "gemini-3-flash-preview"

# ================= CLIENTS =================

# Initialize Clients
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[DB_NAME]
    cars_db = db["vehicles"]
    logs_db = db["logs"]
    print(f"Connected to DB: {db.name}")
except Exception as e:
    print(f"Failed to initialize clients: {e}")

# ================= FASTAPI SETUP =================

app = FastAPI()

# Manage CORS for all ports/origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# ================= DATA MODELS =================

class VehicleRequest(BaseModel):
    userId: str
    vehicleId: str
    sensors: dict

# ================= LLM PROMPT & LOGIC =================

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

def call_llm(sensors: dict):
    prompt = SYSTEM_PROMPT + "\n\nSensor Data:\n" + json.dumps(sensors)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )

        text = response.text.strip()

        # Clean markdown if Gemini wraps JSON
        if text.startswith("```"):
            text = text.split("```")[1].strip()
            # Handle specifically ```json ... ```
            if text.startswith("json"):
                text = text[4:].strip()

        return json.loads(text)

    except Exception as e:
        print(f"Error calling or parsing LLM: {e}")
        # print("Raw response:", response) # Commented out to avoid unbound local error if response fails
        return None

def process_vehicle_analysis(userId, vehicleId, sensors):
    # Check if car exists
    car_doc = cars_db.find_one({
        "user_id": userId,
        "vehicle_id": vehicleId
    })

    if not car_doc:
        # In a real app, you might want to create the car if it doesn't exist, 
        # but adhering to your logic, we raise an error.
        raise ValueError(f"Car not found for user_id: {userId}, vehicle_id: {vehicleId}")

    # Call LLM
    llm_output = call_llm(sensors)
    if not llm_output:
        raise ValueError("LLM output not generated properly")

    # Update Car Document
    updated_car_doc = {
        "user_id": car_doc["user_id"],
        "vehicle_id": car_doc["vehicle_id"],
        "owner": car_doc.get("owner", "Unknown"),
        "model": car_doc.get("model", "Unknown"),
        "status": llm_output.get("status", "Unknown"),
        "isServiceNeeded": llm_output.get("isServiceNeeded", False),
        "recommendedAction": llm_output.get("recommendedAction", "N/A"),
        "sensors": sensors,
        "predictions": llm_output.get("predictions", []),
        "summary": llm_output.get("summary", "")
    }

    # Helper to convert ObjectId to string for JSON serialization
    def serialize_doc(doc):
        doc["_id"] = str(doc["_id"])
        return doc

    cars_db.update_one(
        {"_id": car_doc["_id"]},
        {"$set": updated_car_doc}
    )

    # Insert Logs
    for pred in llm_output.get("predictions", []):
        log = {
            "logId": str(uuid4()),
            "user_id": userId,
            "vehicle_id": vehicleId,
            "timestamp": datetime.utcnow(),
            "logType": "ISSUE",
            "data": {
                "component": pred.get("component"),
                "issue": pred.get("issue"),
                "severity": pred.get("severity"),
                "prediction": pred.get("prediction"),
                "recommendation": pred.get("recommendation"),
                "modelVersion": MODEL_NAME
            }
        }
        logs_db.insert_one(log)

    return serialize_doc(updated_car_doc)

# ================= API ENDPOINTS =================

@app.get("/")
def health_check():
    return {"status": "running", "db": DB_NAME}

@app.post("/analyze")
def analyze_vehicle_endpoint(payload: VehicleRequest):
    try:
        result = process_vehicle_analysis(
            payload.userId, 
            payload.vehicleId, 
            payload.sensors
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"Server Error: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

# ================= RUNNER =================

if __name__ == "__main__":
    # Railway will provide a PORT env var, default to 8000 locally
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)