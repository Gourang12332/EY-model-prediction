import httpx
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from datetime import datetime
import uuid

app = FastAPI()

CALLING_API = "https://calling-agent-ey.onrender.com/make-call"
BOOKING_API = "https://booking-and-log-service-ey.onrender.com/book-service"
SERVICE_CENTER_API = "https://admin-ey-1.onrender.com/get-all-centers"
MESSAGING_API = "https://your-messaging-api.com/send-and-get-reply"  

class CallRequest(BaseModel):
    number: str
    vehicleId: str
    issue : str

async def process_voice_workflow(number: str, vehicle_id: str, issue : str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # 1. Call user
            call_resp = await client.post(CALLING_API, json={"number": number,"issue" : issue,"vehicle_id" : vehicle_id})
            if call_resp.status_code != 200:
                print("Call failed")
                return

            data = call_resp.json()
            if data.get("status") != "success":
                print("Call not successful")
                return

            user_response = data.get("user_choice", "").lower().strip()
            if not user_response:
                print("Empty user response")
                return

            print("User Response:", user_response)

            # 2. Decision Logic

            if "no" in user_response:
                print("Message Sent")
                return

           
            if any(word in user_response for word in ["auto", "best", "automatic"]):

                booking_payload = {
                    "vehicleId": vehicle_id,
                    "confirmationCode": str(uuid.uuid4())[:6].upper(),
                    "status": "CONFIRMED",
                    "scheduledService": {
                        "isScheduled": True,
                        "serviceCenterId": "",  
                        "dateTime": datetime.utcnow().isoformat() + "Z"
                    }
                }

                booking_resp = await client.post(BOOKING_API, json=booking_payload)
                print("Booking Payload Sent:", booking_payload)
                print("Booking Response:", booking_resp.json())
                return

            # YES → USER WANTS LIST // api to book the center through messages. 
            if any(word in user_response for word in ["yes", "ok", "book"]):
                
                if any(word in user_response.lower() for word in ["yes", "ok", "book"]):
                    await client.post(
                        "https://eymessaging.onrender.com/sensor-anomaly",
                        json={
                            "vehicle_id": vehicle_id,
                            "issue_detected": issue
                        }
                    )
                print("Message Sent and booked through the api call")
                return
            
            # CUSTOM INPUT (like ISKCON, Jaipur, etc.)
            print("Custom user input detected:", user_response)
            print("Forwarding to location-based flow /  or unwanted input.")

        except Exception as e:
            print(f"Error in background workflow: {e}")

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "automated-service",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

@app.post("/start-automated-service")
async def start_service(request: CallRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        process_voice_workflow,
        request.number,
        request.vehicleId,
        request.issue
    )
    return {
        "status": "Call initiated",
        "target_number": request.number,
        "vehicleId": request.vehicleId
    }
