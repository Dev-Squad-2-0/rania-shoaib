"""
employee_data.py
Task 3 support — Search Property/Calendar/Email nodes all need a real
employee to assign an appointment to. Nothing in the existing codebase
(schema.sql, seed_data.sql) defines an employees table, so this is a
small static lookup rather than a DB table — swap for a real query if
an employees table gets added later, the shape (name/email/id) is what
appointment_manager.book_appointment expects either way.
"""

EMPLOYEES = [
    {"id": "emp_001", "name": "Ayesha Khan", "email": "ayesha@realestatehub.com", "specialty": "Karachi"},
    {"id": "emp_002", "name": "Bilal Ahmed", "email": "bilal@realestatehub.com", "specialty": "Lahore"},
    {"id": "emp_003", "name": "Sana Malik", "email": "sana@realestatehub.com", "specialty": "Islamabad"},
]


def get_employee_by_city(city: str) -> dict:
    """Naive round-robin-by-specialty assignment. Falls back to the first
    employee if no city match, rather than returning None and forcing
    every caller to null-check."""
    if city:
        for emp in EMPLOYEES:
            if emp["specialty"].lower() == city.lower():
                return emp
    return EMPLOYEES[0]


def get_employee_by_id(employee_id: str) -> dict:
    for emp in EMPLOYEES:
        if emp["id"] == employee_id:
            return emp
    return None