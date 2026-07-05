#!/bin/sh
set -e

CONFIG="/data/options.json"

export VEHICLE_NAME=$(jq -r '.vehicle_name // "Skoda Superb"' "$CONFIG")
export TANK_CAPACITY_L=$(jq -r '.tank_capacity_l // "66.0"' "$CONFIG")
export DEFAULT_FUEL_TYPE=$(jq -r '.default_fuel_type // "PB95"' "$CONFIG")
export MONTHLY_FUEL_BUDGET=$(jq -r '.monthly_fuel_budget // "0"' "$CONFIG")
export ODOMETER_ENTITY=$(jq -r '.odometer_entity // ""' "$CONFIG")
export FUEL_LEVEL_ENTITY=$(jq -r '.fuel_level_entity // ""' "$CONFIG")
export DRIVVO_EMAIL=$(jq -r '.drivvo_email // ""' "$CONFIG")
export DRIVVO_PASSWORD=$(jq -r '.drivvo_password // ""' "$CONFIG")
export DRIVVO_VEHICLE_ID=$(jq -r '.drivvo_vehicle_id // "0"' "$CONFIG")
export NOTIFY_SERVICE=$(jq -r '.notify_service // "notify/family"' "$CONFIG")
export MQTT_HOST=$(jq -r '.mqtt_host // "core-mosquitto"' "$CONFIG")
export MQTT_PORT=$(jq -r '.mqtt_port // "1883"' "$CONFIG")
export MQTT_USER=$(jq -r '.mqtt_user // ""' "$CONFIG")
export MQTT_PASSWORD=$(jq -r '.mqtt_password // ""' "$CONFIG")
export LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG")
export BACKUP_SHARE=$(jq -r '.backup_share // "/share/fuel_tracker"' "$CONFIG")
export TZ=$(jq -r '.timezone // "Europe/Warsaw"' "$CONFIG")

export DB_PATH="/data/fuel_tracker.db"

exec python3 -m fuel_tracker.main
