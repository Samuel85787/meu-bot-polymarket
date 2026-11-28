"""Configuração isolada da AcesAPI; nunca coloque a chave no repositório."""
import os

API_KEY = os.getenv("ACESAPI_API_KEY", "").strip()
