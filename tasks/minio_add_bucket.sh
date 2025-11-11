#!/bin/sh

# MinIO bucket initialization script
#
# This script runs in the MinIO client container to:
# 1. Configure the MinIO client alias with credentials
# 2. Create required buckets if they don't exist:
#    - datasets: For storing dataset files
#    - models: For storing model artifacts
#
# Environment variables required:
# - MINIO_ROOT_USER: MinIO root access key
# - MINIO_ROOT_PASSWORD: MinIO root secret key

# Set up MinIO client alias
mc alias set minio http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD}

# Create required buckets (ignore if they already exist)
mc mb --ignore-existing minio/datasets
mc mb --ignore-existing minio/models
