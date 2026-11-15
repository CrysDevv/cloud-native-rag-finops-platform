#!/usr/bin/env python3
"""Genera una API key aleatoria y el hash SHA-256 que se debe registrar en API_KEYS_SHA256."""
import hashlib
import secrets

clave = secrets.token_urlsafe(32)
print(f"API_KEY (entregar al cliente, no se almacena): {clave}")
print(f"API_KEYS_SHA256 (agregar a .env; varias claves separadas por coma): {hashlib.sha256(clave.encode()).hexdigest()}")
