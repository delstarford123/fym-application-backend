import re
import datetime
import json
import os

class InstitutionValidator:
    # MMUST Pattern: e.g., SAB/B/01-04774/2023
    MMUST_REGEX = re.compile(r"^[A-Z]{2,4}/[A-Z]/\d{2}-\d{4,5}/\d{4}$")
    
    # GLOBAL Pattern: Broad alphanumeric support allowing dashes and slashes
    GLOBAL_REGEX = re.compile(r"^[A-Z0-9\-\/]{4,25}$")

    # Program duration mapping (in years)
    PROGRAM_DURATIONS = {
        'medicine': 6,
        'engineering': 5,
        'general': 4,
        'diploma': 2,
        'certificate': 1
    }

    _institutions_cache = None

    @classmethod
    def get_all_institutions(cls):
        """Loads and returns all Kenyan institutions from the local JSON storage."""
        if cls._institutions_cache is not None:
            return cls._institutions_cache
            
        try:
            path = os.path.join(os.path.dirname(__file__), 'institutions.json')
            with open(path, 'r') as f:
                data = json.load(f)
                # Flatten the structure but keep category info
                cls._institutions_cache = []
                for category_name, category_list in data.items():
                    for inst in category_list:
                        inst['category'] = category_name
                        cls._institutions_cache.append(inst)
                return cls._institutions_cache
        except Exception as e:
            print(f"[ERROR] Failed to load institutions: {e}")
            return []

    @classmethod
    def validate_registration_number(cls, institution_id: str, reg_number: str) -> bool:
        """
        Dynamically routes the validation based on the institution ID.
        """
        if not reg_number:
            return False
            
        # Clean the input
        reg_number = reg_number.strip().upper()

        # Special case for MMUST (original validator)
        if institution_id.lower() == 'mmust':
            return bool(cls.MMUST_REGEX.match(reg_number))
        
        # General validation for others
        return bool(cls.GLOBAL_REGEX.match(reg_number))

    @classmethod
    def calculate_graduation_date(cls, admission_year: int, program_type: str = 'general') -> int:
        """
        Returns the expected graduation year based on the program duration.
        """
        duration = cls.PROGRAM_DURATIONS.get(program_type.lower(), 4)
        return admission_year + duration
