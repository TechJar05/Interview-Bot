import json
from datetime import datetime
import numpy as np
 
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, np.integer)):
            return str(obj)
        return super().default(obj) 