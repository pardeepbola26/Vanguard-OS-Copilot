import sqlite3
import json
import datetime

import os

# Use absolute path for DB to avoid CWD issues
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "vanguard_missions.db")

def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                name TEXT,
                inputs TEXT,
                outputs TEXT
            )
        ''')
        conn.commit()
        conn.close()
        print(f"Database initialized at: {DB_NAME}")
    except Exception as e:
        print(f"DB Init Error: {e}")

def save_mission(inputs: dict, outputs: dict):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        # Generate a name based on Goal or Situation
        name = inputs.get("goal", "Untitled Mission")[:50]
        if not name:
            name = inputs.get("situation", "Untitled")[:50]
            
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        c.execute('INSERT INTO missions (timestamp, name, inputs, outputs) VALUES (?, ?, ?, ?)',
                  (timestamp, name, json.dumps(inputs), json.dumps(outputs)))
        
        mission_id = c.lastrowid
        conn.commit()
        conn.close()
        return mission_id
    except Exception as e:
        print(f"DB Save Error: {e}")
        return None

def get_history():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT id, timestamp, name FROM missions ORDER BY id DESC')
    rows = c.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            "id": r[0],
            "timestamp": r[1],
            "name": r[2]
        })
    return history

def load_mission(mission_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT inputs, outputs FROM missions WHERE id = ?', (mission_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            "inputs": json.loads(row[0]),
            "outputs": json.loads(row[1])
        }
    return None
