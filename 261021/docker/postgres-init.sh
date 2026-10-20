#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=mlflow_password="$MLFLOW_DB_PASSWORD" \
  --set=app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE mlflow_app LOGIN PASSWORD :'mlflow_password';
CREATE DATABASE mlflow OWNER mlflow_app;
CREATE ROLE bike_app LOGIN PASSWORD :'app_password';
CREATE DATABASE bikeops OWNER bike_app;
SQL
