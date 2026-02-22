import time
import threading
import requests
from fastapi import FastAPI
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="EY Intelligent Master Supervisor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*","https://superb-bubblegum-6c035a.netlify.app/"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


LOGS_API = "https://booking-and-log-service-ey.onrender.com/logs"

SERVICES = {
    "Messaging_API": "https://eymessaging.onrender.com/health",
    "Admin_API": "https://admin-ey-1.onrender.com/",
    "Car_API": "https://carapi-2goc.onrender.com/docs", # adding vehicles and users
    "Vendor_API": "https://eyvendor.onrender.com/health",
    "Model_API": "https://ey-model-prediction.onrender.com/",
    "Booking_API": "https://booking-and-log-service-ey.onrender.com/health",
    "Calling_API": "https://calling-agent-ey-1.onrender.com/health"
}

FETCH_INTERVAL = 60

cached_logs = []
security_alerts = []
health_status = {}

# HEALTH CHECK 

def check_health():
    global health_status
    print("\n===== HEALTH CHECK =====")

    status_report = {}

    for name, url in SERVICES.items():
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                status_report[name] = "ONLINE"
            else:
                status_report[name] = f"ERROR {r.status_code}"
        except:
            status_report[name] = "DOWN"

        print(f"{name} → {status_report[name]}")

    health_status = status_report

# -FETCH LOGS  #

def fetch_logs():
    global cached_logs
    print("\n===== FETCHING LOGS =====")

    try:
        response = requests.get(LOGS_API, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                cached_logs = data
                print(f"Fetched {len(cached_logs)} logs successfully")
            else:
                print("Logs API returned non-list response")
                cached_logs = []
        else:
            print("Failed to fetch logs:", response.status_code)
            cached_logs = []
    except Exception as e:
        print("Error fetching logs:", e)
        cached_logs = []

# - UEBA ENGINE #

def run_ueba_analysis():
    global security_alerts
    print("\n===== RUNNING UEBA ANALYSIS =====")

    alerts = []

    if not cached_logs:
        print("No logs available for analysis")
        security_alerts = []
        return

    now = datetime.now(timezone.utc)
    five_minutes_ago = now - timedelta(minutes=5)
    seven_days_ago = now - timedelta(days=7)

    user_booking_count = defaultdict(int)
    vehicle_users = defaultdict(set)
    issue_records = {}
    booking_records = []

    for log in cached_logs:
        try:
            log_time = datetime.fromisoformat(
                log["timestamp"].replace("Z", "+00:00")
            )
        except:
            continue

        user = log["userId"]
        vehicle = log["vehicleId"]
        log_type = log["logType"]
        data = log.get("data", {})

        vehicle_users[vehicle].add(user)

        if log_type == "ISSUE":
            issue_records[(user, vehicle)] = {
                "time": log_time,
                "severity": data.get("severity"),
                "certainty": data.get("prediction", {}).get("certainty", 100)
            }

        if log_type == "BOOKING":
            booking_records.append((user, vehicle, log_time))
            if log_time > five_minutes_ago:
                user_booking_count[user] += 1

    print(f"Users analyzed: {len(user_booking_count)}")
    print(f"Vehicles analyzed: {len(vehicle_users)}")

 
    for user, count in user_booking_count.items():
        if count > 3:
            msg = f"Anomaly: {user} created {count} bookings in 5 minutes"
            print("ALERT →", msg)
            alerts.append(msg)

   
    for user, vehicle, _ in booking_records:
        if (user, vehicle) not in issue_records:
            msg = f"Suspicious: {user} booked {vehicle} without ISSUE record"
            print("ALERT →", msg)
            alerts.append(msg)

    
    for vehicle, users in vehicle_users.items():
        if len(users) > 1:
            msg = f"Ownership anomaly: Vehicle {vehicle} used by multiple users"
            print("ALERT →", msg)
            alerts.append(msg)

    
    for (user, vehicle), issue in issue_records.items():
        if issue["severity"] == "HIGH":
            if issue["time"] < seven_days_ago:
                booked = any(
                    u == user and v == vehicle
                    for u, v, _ in booking_records
                )
                if not booked:
                    msg = f"Risk: HIGH severity issue ignored for {vehicle}"
                    print("ALERT →", msg)
                    alerts.append(msg)

    
    for user, vehicle, _ in booking_records:
        issue = issue_records.get((user, vehicle))
        if issue and issue["certainty"] < 50:
            msg = f"Suspicious: {user} booked {vehicle} despite low certainty"
            print("ALERT →", msg)
            alerts.append(msg)

    if not alerts:
        print("No anomalies detected")

    security_alerts = list(set(alerts))

# ----- BACKGROUND LOOP 

def monitor_loop():
    while True:
        print("\n===================================")
        print("MASTER SUPERVISOR SCAN STARTED")
        print("Time:", datetime.now(timezone.utc).isoformat())
        print("===================================")

        check_health()
        fetch_logs()
        run_ueba_analysis()

        print("\nScan complete. Sleeping...\n")
        time.sleep(FETCH_INTERVAL)

@app.on_event("startup")
def start_monitor():
    threading.Thread(target=monitor_loop, daemon=True).start()

#  DASHBOARD - #

@app.get("/status")
def dashboard():
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services_health": health_status,
        "total_logs_analyzed": len(cached_logs),
        "security_alerts": security_alerts
    }

# - MAIN - # 

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)