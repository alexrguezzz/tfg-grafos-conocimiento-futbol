from __future__ import annotations

import os

SECRET_KEY = os.getenv("SECRET_KEY", "datagol-development-secret")
GRAPHDB_ENDPOINT = os.getenv(
    "GRAPHDB_ENDPOINT",
    "http://127.0.0.1:7200/repositories/TFG_SoccerData",
)
BRAND_LOGO_PATH = "images/brand/datagol-logo.png"

PREFIXES = """
PREFIX class: <http://example.org/TFG_SoccerData/class/>
PREFIX prop: <http://example.org/TFG_SoccerData/property/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

SECTIONS = [
    {"key": "home", "label": "Inicio", "path": "home"},
    {"key": "competition", "label": "Competicion", "path": "competition"},
    {"key": "matches", "label": "Partidos", "path": "matches"},
    {"key": "teams", "label": "Equipos", "path": "teams"},
    {"key": "players", "label": "Jugadores", "path": "players"},
    {"key": "compare", "label": "Comparador", "path": "compare"},
]

KNOWN_LEAGUE_ASSETS = [
    {"label": "La Liga", "resource_id": "ESP-La_Liga", "logo": "images/leagues/la-liga.svg", "fallback": "LL"},
    {"label": "Premier League", "resource_id": "ENG-Premier_League", "logo": "images/leagues/premier-league.svg", "fallback": "PL"},
    {"label": "Bundesliga", "resource_id": "GER-Bundesliga", "logo": "images/leagues/bundesliga.svg", "fallback": "B"},
    {"label": "Ligue 1", "resource_id": "FRA-Ligue_1", "logo": "images/leagues/ligue-1.svg", "fallback": "L1"},
    {"label": "Serie A", "resource_id": "ITA-Serie_A", "logo": "images/leagues/serie-a.svg", "fallback": "SA"},
]
