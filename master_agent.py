import time
import json
import threading
import requests
from fastapi import FastAPI
from collections import deque
from datetime import datetime, timedelta

app = FastAPI(title="Master Supervisor & UEBA Agent")

# --- CONFIGURATION ---
SERVICES = {
    "Telematics_API": "http://localhost:8001/health",
    "Scheduling_API": "http://localhost:8002/health",
    "Voice_Engagement_API": "http://localhost:8003/health"
}

LOG_FILE = "agent_activity.log"
UEBA_THRESHOLD = 10  # Max actions allowed per minute per agent
agent_memory = {}    # Stores timestamps in a sliding window

# --- 1. HEALTH MONITOR LOGIC ---
def check_system_health():
    print("\n--- [System Health Check] ---")
    report = {}
    for name, url in SERVICES.items():
        try:
            # We use a short timeout so the Master doesn't hang
            resp = requests.get(url, timeout=2)
            status = " ONLINE" if resp.status_code == 200 else f" ERROR {resp.status_code}"
        except:
            status = " DOWN"
        report[name] = status
        print(f"{name}: {status}")
    return report

# --- 2. UEBA ANALYZER LOGIC ---
def run_ueba_scan():
    """Reads the log file and detects behavior anomalies."""
    print(" UEBA Scanner: Monitoring agent behavior...")
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
        
        current_time = datetime.now()
        one_minute_ago = current_time - timedelta(minutes=1)
        
        # Reset memory for fresh calculation
        summary = {} 

        for line in lines:
            data = json.loads(line)
            agent = data["agent_id"]
            timestamp = datetime.fromisoformat(data["timestamp"])

            if timestamp > one_minute_ago:
                if agent not in summary: summary[agent] = 0
                summary[agent] += 1

        # Check against threshold
        alerts = []
        for agent, count in summary.items():
            if count > UEBA_THRESHOLD:
                msg = f" ANOMALY: {agent} performed {count} actions in 1 min!"
                print(msg)
                alerts.append(msg)
        return alerts
    except FileNotFoundError:
        return ["Log file not found. Waiting for agents to log activity..."]

# --- 3. BACKGROUND THREAD ---
# This runs the health check every 30 seconds automatically
def background_monitor():
    while True:
        check_system_health()
        run_ueba_scan()
        time.sleep(30)

@app.on_event("startup")
def start_monitor():
    threading.Thread(target=background_monitor, daemon=True).start()

# --- 4. API ENDPOINTS ---
@app.get("/status")
def get_master_dashboard():
    health = check_system_health()
    ueba_alerts = run_ueba_scan()
    return {
        "timestamp": datetime.now().isoformat(),
        "services_status": health,
        "security_alerts": ueba_alerts
    }

if __name__ == "__main__":
    import uvicorn
    # Start on port 9000 to avoid conflict with your microservices
    uvicorn.run(app, host="0.0.0.0", port=9000)