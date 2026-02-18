import json
import os

def calculate_total(filename):
    if not os.path.exists(filename):
        return f"{filename}: File not found"
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            expenses = data.get('expenses', [])
            total = sum(float(e.get('tutar', 0)) for e in expenses)
            vehicle_count = len(data.get('vehicles', []))
            return f"{filename}: Total Expenses = {total:.2f}, Vehicles = {vehicle_count}"
    except Exception as e:
        return f"{filename}: Error - {e}"

print(calculate_total("data.json"))
print(calculate_total("data.json"))
print(calculate_total("data.json.bak"))
print(calculate_total("../arac_takip_backup_2026-01-26.json"))
