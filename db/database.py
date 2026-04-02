import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Union

from db.models import AnalysisRecord
 

DB_PATH = "data.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        question TEXT,
        category TEXT,
        market_probability TEXT,
        system_probability TEXT,
        confidence TEXT,
        reasoning TEXT,
        main_scenario TEXT,
        alt_scenario TEXT,
        conclusion TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        question TEXT,
        category TEXT,
        market_probability TEXT,
        system_probability TEXT,
        confidence TEXT,
        reasoning TEXT,
        main_scenario TEXT,
        alt_scenario TEXT,
        conclusion TEXT,
        opportunity_score INTEGER,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()


def save_analysis(url: str, result: Union[Dict[str, Any], AnalysisRecord]):
    conn = get_connection()
    cursor = conn.cursor()

    if isinstance(result, dict):
        record = AnalysisRecord.from_result(url, result)
    else:
        record = result

    created_at = record.created_at or datetime.utcnow().isoformat()

    cursor.execute("""
    INSERT INTO analyses (
        url,
        question,
        category,
        market_probability,
        system_probability,
        confidence,
        reasoning,
        main_scenario,
        alt_scenario,
        conclusion,
        created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record.url,
        record.question,
        record.category,
        record.market_probability,
        record.system_probability,
        record.confidence,
        record.reasoning,
        record.main_scenario,
        record.alt_scenario,
        record.conclusion,
        created_at
    ))

    conn.commit()
    conn.close()


def save_opportunity(result: Dict[str, Any]):
    conn = get_connection()
    cursor = conn.cursor()

    created_at = result.get("created_at") or datetime.utcnow().isoformat()

    cursor.execute("""
    INSERT INTO opportunities (
        url,
        question,
        category,
        market_probability,
        system_probability,
        confidence,
        reasoning,
        main_scenario,
        alt_scenario,
        conclusion,
        opportunity_score,
        created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.get("url", ""),
        result.get("question", ""),
        result.get("category", ""),
        result.get("market_probability", ""),
        result.get("probability", ""),
        result.get("confidence", ""),
        result.get("reasoning", ""),
        result.get("main_scenario", ""),
        result.get("alt_scenario", ""),
        result.get("conclusion", ""),
        int(result.get("opportunity_score", 0) or 0),
        created_at
    ))

    conn.commit()
    conn.close()


def get_recent_analyses(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT
        url,
        question,
        category,
        system_probability,
        confidence,
        created_at
    FROM analyses
    ORDER BY id DESC
    LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append({
            "url": row[0],
            "question": row[1],
            "category": row[2],
            "system_probability": row[3],
            "confidence": row[4],
            "created_at": row[5],
        })

    return result


def get_top_opportunities(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT
        url,
        question,
        category,
        market_probability,
        system_probability,
        confidence,
        opportunity_score,
        created_at
    FROM opportunities
    ORDER BY opportunity_score DESC, id DESC
    LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append({
            "url": row[0],
            "question": row[1],
            "category": row[2],
            "market_probability": row[3],
            "system_probability": row[4],
            "confidence": row[5],
            "opportunity_score": row[6],
            "created_at": row[7],
        })

    return result
