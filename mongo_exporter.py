import pymongo
from prometheus_client import start_http_server, Gauge
import time
from datetime import datetime

MONGO_URI = "mongodb+srv://vishalvivek10039:Sriram2015@infrahealth.vdxwhfq.mongodb.net/"

# MongoDB collections for raw server logs (metrics)
MONGO_DB_RAW_LOGS = "logs"
MONGO_COLLECTION_SERVER_METRICS = "server"

# MongoDB collections for ML-detected anomalies (Isolation Forest results)
MONGO_DB_ML_RESULTS = "MLAnomalyResultsDB"
MONGO_COLLECTION_ML_ANOMALIES = "ml_server_anomalies"

EXPORTER_PORT = 9100
SCRAPE_INTERVAL_SECONDS = 30

# Prometheus Gauges for raw server metrics
# Labels now correctly include 'environment' and exclude 'ip_address'
SERVER_CPU_USAGE = Gauge('server_cpu_usage_percent', 'Current CPU usage percentage', ['server_name', 'environment'])
SERVER_MEMORY_USAGE = Gauge('server_memory_usage_percent', 'Current Memory usage percentage', ['server_name', 'environment'])
SERVER_DISK_UTILIZATION = Gauge('server_disk_utilization_percent', 'Current Disk utilization percentage', ['server_name', 'environment'])
SERVER_CPU_TEMP = Gauge('server_cpu_temperature_celsius', 'Current CPU temperature in Celsius', ['server_name', 'environment'])
SERVER_STATUS = Gauge('server_status_code', 'Numeric status code for server (1=Good, 0=Bad)', ['server_name', 'environment'])

# Prometheus Gauge for ML (Isolation Forest) detected anomalies
# Labels now correctly include 'environment' and exclude 'ip_address'
ML_SERVER_ANOMALY_ACTIVE = Gauge('ml_server_anomaly_active', 'Indicates if an ML (Isolation Forest)-detected server anomaly is currently active (1=yes, 0=no)', ['server_name', 'environment'])

def update_metrics_from_mongodb():
    client = None
    try:
        client = pymongo.MongoClient(MONGO_URI)
        db_raw_logs = client[MONGO_DB_RAW_LOGS]
        db_ml_results = client[MONGO_DB_ML_RESULTS]

        # --- Scrape Raw Server Metrics (logs.server) ---
        # Grouping by server and environment to get the latest for each logical server
        pipeline_servermetrics = [
            {"$sort": {"createdAt": -1}},
            {"$group": {
                "_id": {"server": "$server", "environment": "$environment"}, # Group by both server and environment
                "latestDoc": {"$first": "$$ROOT"}
            }},
            {"$replaceRoot": {"newRoot": "$latestDoc"}}
        ]
        latest_server_metrics = list(db_raw_logs[MONGO_COLLECTION_SERVER_METRICS].aggregate(pipeline_servermetrics))

        for log in latest_server_metrics:
            server_name = log.get('server', 'unknown_server')
            # ip_address = log.get('ip_address', 'unknown_ip') # Extracted but no longer used as a metric label
            environment = log.get('environment', 'unknown_env') # Correctly extract environment

            if server_name == 'unknown_server' or environment == 'unknown_env':
                print(f"Skipping servermetrics log due to missing server name or environment: {log}")
                continue
            
            cpu_usage = float(log.get('cpu_usage', 0) or 0)
            memory_usage = float(log.get('memory_usage', 0) or 0)
            disk_utilization = float(log.get('disk_utilization', 0) or 0)
            cpu_temp = float(log.get('cpu_temp', 0) or 0)

            # Pass only 'server_name' and 'environment' to the labels
            SERVER_CPU_USAGE.labels(server_name=server_name, environment=environment).set(cpu_usage)
            SERVER_MEMORY_USAGE.labels(server_name=server_name, environment=environment).set(memory_usage)
            SERVER_DISK_UTILIZATION.labels(server_name=server_name, environment=environment).set(disk_utilization)
            SERVER_CPU_TEMP.labels(server_name=server_name, environment=environment).set(cpu_temp)
            
            status_map = {"Good": 1, "Warning": 0.5, "Critical": 0, "Bad": 0}
            SERVER_STATUS.labels(server_name=server_name, environment=environment).set(status_map.get(log.get('server_health', 'Good'), 1))


        # --- Scrape ML (Isolation Forest) Anomalies (MLAnomalyResultsDB.ml_server_anomalies) ---
        active_ml_anomalies = list(db_ml_results[MONGO_COLLECTION_ML_ANOMALIES].find({"is_active": True}))

        active_ml_anomaly_combinations = set()

        for anomaly in active_ml_anomalies:
            server_name_ml = anomaly.get('server_name')
            environment_ml = anomaly.get('environment') # Correctly extract environment from ML results

            if not server_name_ml or not environment_ml:
                print(f"Warning: Skipping ML anomaly result due to missing server_name or environment: {anomaly}")
                continue
            
            # Pass only 'server_name' and 'environment' to the labels
            ML_SERVER_ANOMALY_ACTIVE.labels(server_name=server_name_ml, environment=environment_ml).set(1)
            active_ml_anomaly_combinations.add((server_name_ml, environment_ml))
        
        # Reset ML_SERVER_ANOMALY_ACTIVE to 0 for servers no longer reporting an active anomaly
        for labels, gauge in list(ML_SERVER_ANOMALY_ACTIVE._metrics.items()):
            server_name, environment = labels[0], labels[1] # Corrected for 2 labels
            if (server_name, environment) not in active_ml_anomaly_combinations:
                gauge.set(0)

    except pymongo.errors.ConnectionFailure as e:
        print(f"MongoDB connection error: {e}. Check MONGO_URI and MongoDB Atlas status.")
    except pymongo.errors.OperationFailure as e:
        print(f"MongoDB operation error (authentication/authorization): {e}. Check credentials in MONGO_URI.")
    except Exception as e:
        print(f"An unexpected error occurred in exporter: {e}")
    finally:
        if client:
            client.close()

if __name__ == '__main__':
    start_http_server(EXPORTER_PORT)
    print(f"Prometheus exporter listening on port {EXPORTER_PORT}")

    while True:
        update_metrics_from_mongodb()
        time.sleep(SCRAPE_INTERVAL_SECONDS)